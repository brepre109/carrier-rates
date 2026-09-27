"""HTTP transport abstraction.

Carrier clients only ever call ``transport.send(HttpRequest) -> HttpResponse``. That
makes it trivial to swap the real network for replayed JSON fixtures (tests, offline
demo) or to wrap the network with a recorder that writes sanitized fixtures.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .errors import CarrierRatesError, TransportError
from .sanitize import sanitize


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None

    @property
    def path(self) -> str:
        return urlsplit(self.url).path


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


class UrllibTransport:
    """Real network transport built on the standard library (no extra dependencies)."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def send(self, request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(request.url, data=request.body, method=request.method,
                                     headers=request.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return HttpResponse(resp.status, resp.read(), dict(resp.headers.items()))
        except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a useful JSON body
            return HttpResponse(exc.code, exc.read() or b"", dict(exc.headers.items()) if exc.headers else {})
        except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
            raise TransportError(f"{request.method} {request.url} failed: {exc}") from exc


# --- fixtures ---------------------------------------------------------------------------

_ROUTES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/oauth/token$"), "fedex_oauth"),
    (re.compile(r"^/rate/v1/rates/quotes$"), "fedex_rate"),
    (re.compile(r"^/security/v1/oauth/token$"), "ups_oauth"),
    (re.compile(r"^/api/rating/[^/]+/(?P<option>[A-Za-z]+)$"), "ups_{option}"),
]


def fixture_name(url: str) -> str:
    """Map a carrier URL onto a fixture file stem, e.g. ``.../Shop`` -> ``ups_shop``."""
    path = urlsplit(url).path
    for pattern, template in _ROUTES:
        m = pattern.match(path)
        if m:
            return template.format(**{k: v.lower() for k, v in m.groupdict().items()})
    raise CarrierRatesError(f"no fixture route for {path}")


def bundled_fixture_dir() -> Path:
    return Path(str(resources.files("carrier_rates") / "fixtures"))


class FixtureTransport:
    """Replays ``<dir>/<fixture_name>.json`` files.

    A fixture file looks like ``{"status": 200, "headers": {...}, "body": {...}}``.
    Every request is kept in ``self.requests`` so tests can assert on what was sent.
    ``overrides`` maps a fixture name to an alternative file stem (e.g. an error case).
    """

    def __init__(self, directory: str | Path | None = None, overrides: dict[str, str] | None = None):
        self.directory = Path(directory) if directory else bundled_fixture_dir()
        self.overrides = dict(overrides or {})
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        name = fixture_name(request.url)
        name = self.overrides.get(name, name)
        path = self.directory / f"{name}.json"
        if not path.exists():
            raise CarrierRatesError(f"fixture not found: {path}")
        data = json.loads(path.read_text())
        body = data.get("body")
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        return HttpResponse(int(data.get("status", 200)), raw, dict(data.get("headers") or {}))


class RecordingTransport:
    """Wraps a live transport and saves every response as a sanitized fixture.

    ``secrets`` are literal values (account numbers, client ids) that are scrubbed from
    anywhere in the payload, in addition to the key-based redaction in ``sanitize``.
    """

    def __init__(self, inner: Transport, directory: str | Path, secrets: list[str] | None = None):
        self.inner = inner
        self.directory = Path(directory)
        self.secrets = [s for s in (secrets or []) if s]
        self.saved: list[Path] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        response = self.inner.send(request)
        try:
            body: object = json.loads(response.body or b"null")
        except ValueError:
            body = response.body.decode("utf-8", "replace")
        record = {
            "status": response.status,
            "headers": {"Content-Type": response.headers.get("Content-Type", "application/json")},
            "body": sanitize(body, self.secrets),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{fixture_name(request.url)}.json"
        path.write_text(json.dumps(record, indent=2) + "\n")
        self.saved.append(path)
        return response
