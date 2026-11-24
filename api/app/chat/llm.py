"""Free-first language model chain for natural chat.

Order: Capzoryx's own Hisab-Chat model (served from the team's GPU or a free Space), then free API tiers. Each
provider is used only when its key is set, is skipped for a while after a quota or network error, and every answer
passes a number check: a reply that states an amount not present in the owner's facts or message is discarded.
"""
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from api.app.chat.prompts import CHAT_SYSTEM as SYSTEM, FACTS_HEADER, NO_FACTS  # one voice for every model

TRANSPORT = None  # tests inject an httpx.MockTransport
MAX_TOKENS = int(os.getenv('CAPZORYX_CHAT_MAX_TOKENS', '500'))
PER_CALL_SECONDS = 15
OWN_CALL_SECONDS = 12  # the own model is tried first; if its Space is cold or busy a free API answers instead
TOTAL_SECONDS = 40  # GPU own model, then the free CPU copy, then free APIs
CPU_CALL_SECONDS = 30
CPU_FACTS_CHARS = 1800  # a 2-core CPU reads a long prompt slowly; it gets the most important part of the facts
CPU_MAX_TOKENS = 260
_cooldown = {}
_lock = threading.Lock()


_models = {}  # provider name -> model chosen from its live /models list


@dataclass(frozen=True)
class Provider:
    name: str
    base: str  # empty for the own model, whose URL comes from the environment
    model: str
    own: bool = False
    alternatives: tuple = ()  # used when the preferred model has been retired

    def env(self, field):
        return os.getenv(f'CAPZORYX_CHAT_{self.name.upper()}_{field}', '').strip()

    @property
    def url(self):
        return (self.env('URL') if self.own else self.base).rstrip('/')

    @property
    def key(self):
        key = self.env('KEY')
        if not key and self.name == 'gemini' and os.getenv('SAFELEDGER_LLM_PROFILE', 'gemini_free') == 'gemini_free':
            key = os.getenv('SAFELEDGER_LLM_API_KEY', '').strip()  # reuse the app's existing Gemini key
        if not key and self.name == 'hisab':
            key = os.getenv('CAPZORYX_HISAB_HF_TOKEN', '').strip()  # the Hugging Face token cash extraction already uses
        return key

    @property
    def model_name(self):
        return self.env('MODEL') or _models.get(self.name) or self.model

    def discover(self, timeout=8):
        """Pick the first preferred model the provider still offers; free model names change often."""
        if self.own or self.env('MODEL') or self.name in _models:
            return
        try:
            with httpx.Client(transport=TRANSPORT, trust_env=False, timeout=timeout) as client:
                listed = client.get(self.url + '/models', headers={'Authorization': 'Bearer ' + self.key})
            offered = {str(m.get('id', '')).removeprefix('models/') for m in listed.json().get('data', [])}
        except (httpx.HTTPError, ValueError, AttributeError):
            return
        choice = next((m for m in (self.model, *self.alternatives) if m in offered), None)
        if choice:
            _models[self.name] = choice

    def configured(self):
        if self.own:
            try:
                parts = urlsplit(self.url)
            except ValueError:
                return False
            local = parts.hostname in ('127.0.0.1', 'localhost', '::1')
            return bool(parts.scheme == 'https' or (parts.scheme == 'http' and local)) and bool(parts.hostname) \
                and not (parts.username or parts.password or parts.query or parts.fragment)
        return bool(self.key)


PROVIDERS = (
    Provider('hisab', '', 'hisab-chat', own=True),
    Provider('hisab_cpu', '', 'hisab-chat', own=True),  # the same model as GGUF on a free CPU Space, when GPU quota runs out
    Provider('groq', 'https://api.groq.com/openai/v1', 'llama-3.3-70b-versatile',
             alternatives=('openai/gpt-oss-120b', 'moonshotai/kimi-k2-instruct', 'qwen/qwen3-32b', 'llama-3.1-8b-instant')),
    Provider('gemini', 'https://generativelanguage.googleapis.com/v1beta/openai', 'gemini-flash-latest',
             alternatives=('gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-flash-lite-latest')),
    Provider('cerebras', 'https://api.cerebras.ai/v1', 'llama-3.3-70b',
             alternatives=('gpt-oss-120b', 'qwen-3-235b-a22b-instruct-2507', 'llama3.1-8b')),
    Provider('openrouter', 'https://openrouter.ai/api/v1', 'meta-llama/llama-3.3-70b-instruct:free',
             alternatives=('deepseek/deepseek-chat-v3-0324:free', 'qwen/qwen3-235b-a22b:free', 'google/gemma-3-27b-it:free',
                           'mistralai/mistral-small-3.2-24b-instruct:free')),
    Provider('mistral', 'https://api.mistral.ai/v1', 'mistral-small-latest', alternatives=('open-mistral-nemo',)),
    Provider('github', 'https://models.github.ai/inference', 'openai/gpt-4.1-mini', alternatives=('openai/gpt-4o-mini',)),
    Provider('sambanova', 'https://api.sambanova.ai/v1', 'Meta-Llama-3.3-70B-Instruct',
             alternatives=('DeepSeek-V3-0324', 'Llama-4-Maverick-17B-128E-Instruct', 'Meta-Llama-3.1-8B-Instruct')),
)



def configured():
    return any(p.configured() for p in PROVIDERS)


def available():
    now = time.monotonic()
    with _lock:
        return [p for p in PROVIDERS if p.configured() and _cooldown.get(p.name, 0) <= now]


def rest(provider, seconds):
    with _lock:
        _cooldown[provider.name] = time.monotonic() + seconds


NUMBER = re.compile(r'(?<![\w.])(\d[\d,]*(?:\.\d+)?)')
BN_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')


def numbers(text):
    found = set()
    for raw in NUMBER.findall(text.translate(BN_DIGITS)):
        value = raw.replace(',', '').rstrip('.')
        try:
            number = float(value)
        except ValueError:
            continue
        if number >= 100:  # small counts and dates are not money claims
            found.add(round(number, 2))
    return found


def grounded(answer, facts, question):
    """Every amount of 100 or more in the answer must appear in the facts or in the owner's own message."""
    known = numbers(facts) | numbers(question)
    return numbers(answer) <= known


def clean(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S | re.I)
    text = re.sub(r'[*_`#]{1,3}', '', text)
    # Keep line breaks so bullet points and steps stay readable in Telegram and the web chat.
    lines = [' '.join(line.split()) for line in text.splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()[:2000]


def call_space(provider, messages, timeout):
    """The own model on a Hugging Face ZeroGPU Space (Gradio API: start a call, then read its result stream)."""
    headers = {'Authorization': 'Bearer ' + provider.key} if provider.key else {}
    with httpx.Client(transport=TRANSPORT, trust_env=False, follow_redirects=False, timeout=timeout) as client:
        start = client.post(provider.url + '/gradio_api/call/chat', headers=headers,
                            json={'data': [json.dumps(messages, ensure_ascii=False), MAX_TOKENS]})
        if start.status_code != 200:
            rest(provider, 600 if start.status_code == 429 else 120)
            raise RuntimeError('status')
        result = client.get(f"{provider.url}/gradio_api/call/chat/{start.json()['event_id']}", headers=headers)
    kind = None
    for line in result.text.splitlines():
        if line.startswith('event:'):
            kind = line[6:].strip()
        elif line.startswith('data:') and kind == 'complete':
            return clean(json.loads(line[5:].strip())[0] or '')
        elif line.startswith('data:') and kind == 'error':
            rest(provider, 600)  # usually the daily GPU quota; the free API chain answers meanwhile
            raise RuntimeError('space')
    raise RuntimeError('space')


def call(provider, messages, deadline):
    limit = CPU_CALL_SECONDS if provider.name == 'hisab_cpu' else OWN_CALL_SECONDS if provider.own else PER_CALL_SECONDS
    timeout = min(limit, deadline - time.monotonic())
    if timeout <= 1:
        raise TimeoutError
    if provider.own and provider.env('MODE') == 'zerogpu':
        return call_space(provider, messages, timeout)
    payload = {'model': provider.model_name, 'messages': messages, 'temperature': 0.6,
               'max_tokens': CPU_MAX_TOKENS if provider.name == 'hisab_cpu' else MAX_TOKENS}
    headers = {'Authorization': 'Bearer ' + provider.key} if provider.key else {}
    with httpx.Client(transport=TRANSPORT, trust_env=False, follow_redirects=False, timeout=timeout) as client:
        response = client.post(provider.url + '/chat/completions', json=payload, headers=headers)
    if response.status_code == 429:
        rest(provider, 600)
        raise RuntimeError('quota')
    if response.status_code != 200:
        if response.status_code in (400, 404) and provider.name not in _models:
            provider.discover()  # a retired model name: look up a current one and retry next time
            if provider.name in _models:
                raise RuntimeError('model')
        rest(provider, 120)
        raise RuntimeError('status')
    data = response.json()
    content = data['choices'][0]['message'].get('content')
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    return clean(content or '')


def compact(facts, limit):
    """The first whole lines of the facts up to `limit` characters (business, cash and the most valuable products come first)."""
    if len(facts) <= limit:
        return facts
    kept, size = [], 0
    for line in facts.splitlines():
        if size + len(line) + 1 > limit:
            break
        kept.append(line)
        size += len(line) + 1
    return '\n'.join(kept + ['(More details exist but are not shown here.)'])


POLISH = os.getenv('CAPZORYX_CHAT_POLISH', '1') == '1'
POLISH_SECONDS = 12
POLISH_SYSTEM = (
    'You edit a business assistant\'s draft reply before the owner sees it. Rewrite it so it reads naturally and '
    'warmly, like a helpful local advisor: same language and script as the owner\'s message (Bangla, Banglish or '
    'English), short sentences, a few short points when there are steps. Keep every number, amount, date, name and '
    'fact exactly as written; add no new facts or figures and drop nothing important. Do not mention drafts, editing, '
    'models or AI. Reply with the final text only.')


def polish(question, draft, deadline):
    """The own model's reply, rewritten by the first free API that answers, so every reply has one voice.

    The rewrite may not add an amount that the draft or the owner's message does not already contain; when it does,
    or no API answers in time, the draft is returned unchanged."""
    end = min(deadline, time.monotonic() + POLISH_SECONDS)
    messages = [{'role': 'system', 'content': POLISH_SYSTEM},
                {'role': 'user', 'content': f'Owner\'s message:\n{question[:400]}\n\nDraft reply:\n{draft}'}]
    for provider in available():
        if provider.own:
            continue
        try:
            better = call(provider, messages, end)
        except (httpx.HTTPError, TimeoutError, RuntimeError, ValueError, KeyError, IndexError, TypeError):
            if time.monotonic() >= end:
                break
            continue
        if better and len(better) >= min(40, len(draft) // 3) and grounded(better, draft, question):
            return better
    return draft


def reply(question, history=(), facts='', share_facts=False):
    """Return (answer, provider name), or (None, None) when no provider gave a grounded answer in time."""
    deadline = time.monotonic() + TOTAL_SECONDS
    turns = [{'role': role, 'content': text[:400]} for role, text in list(history)[-6:]]
    for provider in available():
        sees_facts = bool(facts) and (provider.own or share_facts)
        shown = compact(facts, CPU_FACTS_CHARS) if provider.name == 'hisab_cpu' else facts
        system = SYSTEM + (FACTS_HEADER + shown if sees_facts else NO_FACTS)
        messages = [{'role': 'system', 'content': system}, *turns, {'role': 'user', 'content': question[:400]}]
        try:
            try:
                answer = call(provider, messages, deadline)
            except RuntimeError as error:
                if str(error) != 'model':
                    raise
                answer = call(provider, messages, deadline)  # retry once with the newly discovered model
        except (httpx.HTTPError, TimeoutError, RuntimeError, ValueError, KeyError, IndexError, TypeError):
            if time.monotonic() >= deadline:
                break
            continue
        if answer and grounded(answer, shown if sees_facts else '', question):
            # The draft holds the owner's figures, so it goes to a cloud editor only when they allow cloud answers.
            if provider.own and POLISH and share_facts:
                answer = polish(question, answer, deadline + POLISH_SECONDS)
            return answer, provider.name
    return None, None
