"""Policy for model requests that include source records or images."""

from api.app.agents.errors import ProviderError


def record_provider():
    from api.app.gateway import providers_for, permitted
    error = None
    for provider in providers_for('agent_plan'):
        try:
            permitted(provider, 'record_data', True)
        except ProviderError as exc:
            error = exc
            continue
        return provider
    raise error or ProviderError('PRIVATE_RECORD_PROVIDER_REQUIRED')
