"""Single bounded call to the configured cloud model (free tier). Used for general finance answers and for reading
photos or sentences into a draft record. It never writes records, approves payments or sees credentials."""
import json
import re
import time
import httpx
from api.app.agents.errors import ProviderError

TRANSPORT = None  # tests inject an httpx.MockTransport
MAX_RESPONSE_BYTES = 65536


def configured():
    from api.app.gateway import disclosure
    return any('question' in provider['allowed'] or 'ui_text' in provider['allowed'] for provider in disclosure())


def send(provider, messages, max_tokens, schema, deadline):
    """Bounded OpenAI-compatible text/vision transport used by the policy gateway."""
    payload = {'model': provider.model, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0.2}
    if provider.profile == 'gemini_free':payload['reasoning_effort'] = 'low'
    if schema is not None:
        payload['response_format'] = {'type':'json_object'} if schema == {'type':'object'} else {
            'type':'json_schema', 'json_schema':{'name':'gateway_result', 'strict':True, 'schema':schema}}
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise ProviderError('AI_TIMEOUT')
    try:
        with httpx.Client(transport=TRANSPORT, trust_env=False, follow_redirects=False, timeout=timeout) as client:
            with client.stream('POST', provider.base + '/chat/completions', headers=provider.headers(), json=payload) as response:
                if response.status_code == 429:raise ProviderError('AI_QUOTA_EXHAUSTED')
                if response.status_code != 200:raise ProviderError('AI_PROVIDER_REJECTED', http_status=response.status_code)
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:raise ProviderError('AI_OUTPUT_BUDGET_EXCEEDED')
                    if time.monotonic() > deadline:raise ProviderError('AI_TIMEOUT')
                    chunks.append(chunk)
        data = json.loads(b''.join(chunks).decode('utf-8'))
        choice = data['choices'][0]
        text = choice['message'].get('content')
        if choice.get('finish_reason') not in {'stop', None} and not text:raise ProviderError('AI_INCOMPLETE_RESPONSE')
        if isinstance(text, list):text = ''.join(part.get('text', '') for part in text if isinstance(part, dict))
        if not isinstance(text, str) or not text.strip():raise ProviderError('AI_INVALID_RESPONSE')
        return text.strip(), data.get('usage')
    except (httpx.HTTPError, TimeoutError):
        raise ProviderError('AI_TIMEOUT_OR_NETWORK_FAILURE') from None
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ProviderError('AI_INVALID_RESPONSE') from None


def parse_json_object(text):
    """Models sometimes wrap JSON in prose or code fences; accept the first complete object, nothing else."""
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.I)
    start = text.find('{')
    if start < 0:raise ProviderError('AI_INVALID_RESPONSE')
    try:value, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:raise ProviderError('AI_INVALID_RESPONSE') from None
    if not isinstance(value, dict):raise ProviderError('AI_INVALID_RESPONSE')
    return value
