"""Scrub credentials, account numbers and personal data from recorded payloads."""

from __future__ import annotations

from typing import Any

REDACTED = "REDACTED"

# Compared case-insensitively against JSON keys at any depth.
SENSITIVE_KEYS = {
    # credentials / tokens
    "access_token", "refresh_token", "id_token", "client_id", "client_secret", "authorization",
    # account identifiers
    "accountnumber", "shippernumber", "shipperaccountnumber", "account",
    "x-merchant-id",
    # request correlation ids can embed account data on some carriers
    "transactionid", "customertransactionid", "customercontext",
    # personal data
    "name", "attentionname", "personname", "companyname", "phone", "phonenumber", "number",
    "emailaddress", "addressline", "streetlines",
}


def sanitize(value: Any, secrets: list[str] | None = None) -> Any:
    """Return a deep copy of ``value`` with sensitive keys and literal secrets redacted."""
    secrets = sorted((s for s in (secrets or []) if s), key=len, reverse=True)

    def scrub_str(text: str) -> str:
        for s in secrets:
            text = text.replace(s, REDACTED)
        return text

    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() in SENSITIVE_KEYS and not isinstance(v, (dict, list)):
                    out[k] = REDACTED
                elif isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
                    out[k] = _redact_leaves(v)
                else:
                    out[k] = walk(v)
            return out
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        if isinstance(obj, str):
            return scrub_str(obj)
        return obj

    return walk(value)


def _redact_leaves(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _redact_leaves(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_leaves(v) for v in obj]
    return REDACTED
