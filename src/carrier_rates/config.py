"""Environment and credential configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import ConfigError

SANDBOX = "sandbox"
PRODUCTION = "production"

BASE_URLS = {
    "fedex": {SANDBOX: "https://apis-sandbox.fedex.com", PRODUCTION: "https://apis.fedex.com"},
    "ups": {SANDBOX: "https://wwwcie.ups.com", PRODUCTION: "https://onlinetools.ups.com"},
}


def resolve_env(value: str | None) -> str:
    """Sandbox unless the caller explicitly says ``production``."""
    env = (value or SANDBOX).strip().lower()
    if env not in (SANDBOX, PRODUCTION):
        raise ConfigError(f"CARRIER_RATES_ENV must be 'sandbox' or 'production', got {value!r}")
    return env


@dataclass(frozen=True)
class CarrierCredentials:
    client_id: str
    client_secret: str
    account_number: str | None = None

    def __repr__(self) -> str:  # never leak secrets into logs or tracebacks
        return "CarrierCredentials(client_id=***, client_secret=***, account_number=***)"


def credentials_from_env(carrier: str, environ: Mapping[str, str] | None = None) -> CarrierCredentials | None:
    """Read ``<CARRIER>_CLIENT_ID`` / ``_CLIENT_SECRET`` / ``_ACCOUNT_NUMBER``; None if incomplete."""
    environ = os.environ if environ is None else environ
    prefix = carrier.upper()
    cid = environ.get(f"{prefix}_CLIENT_ID", "").strip()
    secret = environ.get(f"{prefix}_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        return None
    account = environ.get(f"{prefix}_ACCOUNT_NUMBER", "").strip() or None
    return CarrierCredentials(cid, secret, account)


def load_dotenv(path: str | Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Minimal KEY=VALUE loader. Existing environment variables are not overwritten."""
    environ = os.environ if environ is None else environ  # type: ignore[assignment]
    loaded: dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        return loaded
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in environ:
            environ[key] = value
            loaded[key] = value
    return loaded
