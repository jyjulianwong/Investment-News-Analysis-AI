from email_providers.base import EmailProvider
from email_providers.imap_provider import ImapEmailProvider

# Registry of email adapters keyed by the EMAIL_PROVIDER env var. Add support
# for a new email service by implementing `EmailProvider` (see email_providers/base.py)
# and registering its constructor here — node and graph code never change.
_PROVIDERS = {
    "imap": ImapEmailProvider,
}


def build_email_provider(provider: str, **kwargs) -> EmailProvider:
    try:
        provider_cls = _PROVIDERS[provider]
    except KeyError:
        raise ValueError(
            f"Unknown EMAIL_PROVIDER {provider!r} — supported: {sorted(_PROVIDERS)}"
        ) from None
    return provider_cls(**kwargs)
