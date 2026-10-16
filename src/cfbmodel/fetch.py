"""Cached, polite HTTP fetching with immutable raw snapshots.

Every response is written to data/raw/<source>/<YYYY-MM-DD>/ and recorded in
`raw_snapshots`. History already on disk is never re-downloaded unless the caller
asks for a fresh copy (live weekly data), and nothing is ever overwritten.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import duckdb
import httpx

from . import config
from .db import utcnow


def canonical_url(url: str, params: dict | None = None) -> str:
    if not params:
        return url
    query = urlencode(sorted((k, v) for k, v in params.items() if v is not None))
    return f"{url}?{query}"


def _slug(url: str) -> str:
    stem = re.sub(r"^https?://", "", url)
    stem = re.sub(r"[^A-Za-z0-9._=-]+", "_", stem).strip("_")
    digest = hashlib.sha256(url.encode()).hexdigest()[:10]
    return f"{stem[:120]}_{digest}"


@dataclass
class Snapshot:
    body: bytes
    fetched_at: datetime
    path: Path
    url: str


class Fetcher:
    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        raw_dir: Path | None = None,
        client: httpx.Client | None = None,
        min_interval: float = 1.0,
    ):
        http_cfg = config.sources().get("http", {})
        self.con = con
        self.raw_dir = raw_dir or config.RAW_DIR
        self.client = client or httpx.Client(
            timeout=http_cfg.get("timeout_seconds", 30),
            headers={"User-Agent": http_cfg.get("user_agent", "cfbmodel/0.1")},
            follow_redirects=True,
        )
        self.min_interval = min_interval
        self._fetched_this_run: set[str] = set()
        self._last_request = 0.0

    def cached(self, url: str) -> Snapshot | None:
        row = self.con.execute(
            "SELECT path_on_disk, fetched_at FROM raw_snapshots WHERE url = ? ORDER BY fetched_at DESC LIMIT 1",
            [url],
        ).fetchone()
        if row and Path(row[0]).exists():
            path = Path(row[0])
            return Snapshot(path.read_bytes(), row[1], path, url)
        return None

    def get(self, *args, **kwargs) -> bytes:
        return self.fetch(*args, **kwargs).body

    def fetch(
        self,
        source: str,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        fresh: bool = False,
        ext: str = "txt",
        count_call: bool = False,
    ) -> Snapshot:
        """Return the body for `url`, from disk when possible.

        fresh=True takes a new snapshot (once per run per URL); the old one stays on disk.
        """
        params = {k: v for k, v in params.items() if v is not None} if params else None
        full = canonical_url(url, params)
        snap = self.cached(full)
        if snap is not None and (not fresh or full in self._fetched_this_run):
            return snap
        if full in self._fetched_this_run:
            raise RuntimeError(f"refusing a second request to {full} in one run")

        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        resp = self.client.get(url, params=params, headers=headers)
        self._last_request = time.monotonic()
        self._fetched_this_run.add(full)
        resp.raise_for_status()
        body = resp.content

        fetched_at = utcnow()
        out_dir = self.raw_dir / source / fetched_at.strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{_slug(full)}_{fetched_at.strftime('%H%M%S%f')}.{ext}"
        out.write_bytes(body)
        self.con.execute(
            "INSERT INTO raw_snapshots VALUES (?, ?, ?, ?, ?)",
            [source, full, fetched_at, str(out), hashlib.sha256(body).hexdigest()],
        )
        if count_call:
            self.con.execute("INSERT INTO api_calls VALUES (?, ?, ?)", [source, full, fetched_at])
        return Snapshot(body, fetched_at, out, full)


def calls_this_month(con: duckdb.DuckDBPyConnection, source: str = "cfbd") -> int:
    return con.execute(
        "SELECT count(*) FROM api_calls WHERE source = ? AND date_trunc('month', called_at) = date_trunc('month', now())",
        [source],
    ).fetchone()[0]
