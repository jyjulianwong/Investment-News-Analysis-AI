from email_adapter.base import MailProvider
from email_adapter.imap_provider import ImapMailProvider

# Registry of mail adapters keyed by the EMAIL_PROVIDER env var. Add support
# for a new mail service by implementing `MailProvider` (see email_adapter/base.py)
# and registering its constructor here — node and graph code never change.
_PROVIDERS = {
    "imap": ImapMailProvider,
}


def build_mail_provider(provider: str, **kwargs) -> MailProvider:
    try:
        provider_cls = _PROVIDERS[provider]
    except KeyError:
        raise ValueError(
            f"Unknown EMAIL_PROVIDER {provider!r} — supported: {sorted(_PROVIDERS)}"
        ) from None
    return provider_cls(**kwargs)
