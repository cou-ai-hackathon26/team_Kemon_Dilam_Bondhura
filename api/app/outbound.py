"""Only engine-rendered message variants can be shown for sending.

Callers render approved sentences from trusted static templates and typed amounts,
dates, and verified contact fields. A model can select wording, never destinations
or new instructions. Near matches, invisible controls and additions are rejected.
"""
import unicodedata

from api.app.agents.errors import ProviderError


def verified_draft(candidate: str, *, approved: tuple[str, ...]) -> str:
    if (not isinstance(candidate, str) or not 1 <= len(candidate) <= 600
            or any(unicodedata.category(char) in ('Cc', 'Cf') and char != '\n' for char in candidate)
            or candidate not in approved):
        raise ProviderError('UNVERIFIED_OUTBOUND_MESSAGE')
    return candidate
