"""HTTP with a disk cache that doubles as the raw layer.

Every response is stored as JSON: {"url", "fetched_at", "status", "body"} under data/raw/<source>/
(binary bodies such as icons are base64-encoded).
- Freshness is per call (ttl): wiki data for days, prices for minutes, hiscores for minutes.
- Offline mode (GP_OFFLINE=1 or offline=True): never touches the network; serves the last cached copy.
- Polite: descriptive User-Agent, minimum interval per host, retries 429/5xx with backoff (Retry-After honored).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

from gp.config import settings


class OfflineMiss(Exception):
    """Offline mode and nothing cached for this request."""


@dataclass(frozen=True)
class Cached:
    url: str
    fetched_at: datetime
    status: int
    body: str
    from_cache: bool

    def json(self):
        return json.loads(self.body)

    def content(self) -> bytes:
        """Binary responses (images) are cached base64-encoded in the same JSON envelope."""
        return base64.b64decode(self.body)


class Client:
    def __init__(
        self,
        raw_dir: Path | None = None,
        user_agent: str | None = None,
        min_interval: float = 1.0,
        offline: bool | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        s = settings()
        self.raw_dir = raw_dir or s.raw_dir
        self.min_interval = min_interval
        self.offline = offline if offline is not None else os.environ.get("GP_OFFLINE") == "1"
        self._last: dict[str, float] = {}
        self._http = httpx.Client(
            headers={"User-Agent": user_agent or s.user_agent}, timeout=60, follow_redirects=True, transport=transport
        )

    @staticmethod
    def build_url(url: str, params: dict | None = None) -> str:
        return f"{url}?{urlencode(sorted(params.items()))}" if params else url

    def path_for(self, source: str, url: str) -> Path:
        return self.raw_dir / source / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.json"

    def get(
        self,
        source: str,
        url: str,
        params: dict | None = None,
        *,
        ttl: float = 3600,
        allow_404: bool = False,
        binary: bool = False,
    ) -> Cached:
        full = self.build_url(url, params)
        path = self.path_for(source, full)
        if path.exists():
            meta = json.loads(path.read_text())
            fetched_at = datetime.fromisoformat(meta["fetched_at"])
            fresh = (datetime.now(UTC) - fetched_at).total_seconds() < ttl
            if fresh or self.offline:
                return Cached(full, fetched_at, meta["status"], meta["body"], from_cache=True)
        if self.offline:
            raise OfflineMiss(full)

        response = self._fetch(full)
        if response.status_code >= 400 and not (allow_404 and response.status_code == 404):
            response.raise_for_status()
        fetched_at = datetime.now(UTC)
        body = base64.b64encode(response.content).decode() if binary else response.text
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "url": full,
                    "fetched_at": fetched_at.isoformat(),
                    "status": response.status_code,
                    "body": body,
                }
            )
        )
        tmp.replace(path)
        return Cached(full, fetched_at, response.status_code, body, from_cache=False)

    def _fetch(self, url: str, retries: int = 4) -> httpx.Response:
        host = urlsplit(url).netloc
        for attempt in range(retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()
            response = self._http.get(url)
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt < retries:
                retry_after = response.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else min(2**attempt * 2, 60))
        return response
