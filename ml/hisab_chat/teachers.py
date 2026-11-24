"""Teachers for building Hisab-Chat data: a local model on the GPU plus free API models under a daily budget.

The local teacher (Qwen3-30B-A3B, or Gemma-3-12B with less RAM, served by llama-server on the RTX 5060 Ti) does the bulk work without
limits. Free API teachers are spent where quality matters most, each held to its requests per minute and to a daily
budget kept in a small usage file, leaving a reserve so the live bot's free quota is never used up. When a budget is
spent, the local teacher takes over.
"""
import json
import os
import re
import threading
import time
from datetime import date
from pathlib import Path

import httpx

from api.app.chat import llm

TRANSPORT = None  # tests inject an httpx.MockTransport
# Free tiers per minute and per day (approximate); override with CAPZORYX_TEACHER_RPM_<NAME> / _DAILY_<NAME>.
DEFAULT_RPM = {'groq': 28, 'cerebras': 28, 'mistral': 20, 'openrouter': 15, 'gemini': 10, 'github': 10, 'local': 600}
DEFAULT_DAILY = {'groq': 1000, 'cerebras': 1500, 'mistral': 500, 'openrouter': 45, 'gemini': 250, 'github': 60}
RESERVE = float(os.getenv('CAPZORYX_TEACHER_RESERVE', '0.3'))  # share of each daily quota left for the live bot


class Local:
    """The GPU teacher, shaped like an llm.Provider."""
    name, own = 'local', False

    def __init__(self, url, model, key=''):
        self.url, self.model_name, self.key = url.rstrip('/'), model, key


def limit(kind, name, default):
    raw = os.getenv(f'CAPZORYX_TEACHER_{kind}_{name.upper()}', '')
    return int(raw) if raw.isdigit() else default


class Teachers:
    def __init__(self, local_url='', local_model='teacher', local_key='', usage_file=None):
        self.local = Local(local_url, local_model, local_key) if local_url else None
        self.usage_file = Path(usage_file or os.getenv('CAPZORYX_TEACHER_USAGE', '~/.cache/capzoryx/teacher_usage.json')).expanduser()
        self.lock = threading.Lock()
        self.next_slot = {}
        try:
            stored = json.loads(self.usage_file.read_text())
        except (OSError, ValueError):
            stored = {}
        self.usage = stored if stored.get('day') == str(date.today()) else {'day': str(date.today())}

    def remote(self):
        """API teachers with budget left today."""
        def left(p):
            budget = int(limit('DAILY', p.name, DEFAULT_DAILY.get(p.name, 50)) * (1 - RESERVE))
            return self.usage.get(p.name, 0) < budget
        return [p for p in llm.available() if not p.own and left(p)]

    def spend(self, provider):
        if provider is self.local:
            return
        self.usage[provider.name] = self.usage.get(provider.name, 0) + 1
        try:
            self.usage_file.parent.mkdir(parents=True, exist_ok=True)
            self.usage_file.write_text(json.dumps(self.usage))
        except OSError:
            pass

    def reserve(self, prefer, deadline):
        """Pick the soonest-free teacher of the preferred kind (falling back to the other kind) and wait for its slot."""
        with self.lock:
            remote, local = self.remote(), [self.local] if self.local else []
            options = (local or remote) if prefer == 'local' else (remote or local)
            if not options:
                return None
            now = time.monotonic()
            provider = min(options, key=lambda p: self.next_slot.get(p.name, now))
            start = max(now, self.next_slot.get(provider.name, now))
            self.next_slot[provider.name] = start + 60 / limit('RPM', provider.name, DEFAULT_RPM.get(provider.name, 10))
            self.spend(provider)  # count the call when it is booked, so parallel workers cannot overspend
        if start > deadline:
            return None
        time.sleep(max(0.0, start - time.monotonic()))
        return provider

    def complete(self, messages, deadline, *, prefer='local', max_tokens=900, temperature=0.8):
        """Text from a teacher; tries others on quota or network errors. Returns (text, teacher name)."""
        for _ in range(6):
            provider = self.reserve(prefer, deadline)
            if provider is None:
                return None, None
            headers = {'Authorization': 'Bearer ' + provider.key} if provider.key else {}
            try:
                with httpx.Client(transport=TRANSPORT, trust_env=False, timeout=60) as client:
                    response = client.post(provider.url + '/chat/completions', headers=headers, json={
                        'model': provider.model_name, 'messages': messages, 'max_tokens': max_tokens, 'temperature': temperature})
                if response.status_code == 429 and provider is not self.local:
                    llm.rest(provider, 90)
                    continue
                if response.status_code != 200:
                    if provider is not self.local and response.status_code in (400, 404) and provider.name not in llm._models:
                        provider.discover()  # retired model name: switch to a current one and try again
                        if provider.name in llm._models:
                            continue
                    if provider is not self.local:
                        llm.rest(provider, 300)
                    continue
                text = response.json()['choices'][0]['message']['content'] or ''
                return re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip(), provider.name
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                continue
        return None, None

    def ask_json(self, prompt, deadline, *, prefer='local', max_tokens=900):
        text, _ = self.complete([{'role': 'user', 'content': prompt}], deadline, prefer=prefer, max_tokens=max_tokens,
                                temperature=0.9)
        match = re.search(r'\{.*\}', text or '', re.S)
        try:
            return json.loads(match.group(0)) if match else None
        except ValueError:
            return None


def add_arguments(parser):
    parser.add_argument('--local-teacher-url', default='', help='OpenAI-compatible URL of the GPU teacher, e.g. '
                        'http://127.0.0.1:8081/v1 (llama-server with Qwen3-30B-A3B)')
    parser.add_argument('--local-teacher-model', default='teacher')
    parser.add_argument('--local-teacher-key', default='')
