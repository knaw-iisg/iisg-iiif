"""Fetching manifests, behind an on-disk cache.

There are ~112k manifests, one HTTP round trip (plus a hdl.handle.net
redirect) each, so re-fetching all of them every night isn't an option.
Each successfully fetched manifest is kept as gzipped JSON under the cache
directory, keyed by a hash of its URL; by default a cached manifest is
reused as-is, so a nightly run only fetches manifests that are new since
the last one. ``--refresh`` re-fetches everything (e.g. after IISG changes
its manifest service).

Failures aren't cached, so a manifest that 404s or times out tonight is
simply tried again tomorrow.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
import time
from pathlib import Path

import requests

USER_AGENT = "iisg-iiif/0.1 (+https://github.com/knaw-iisg/iisg-iiif)"


class ManifestFetcher:
    def __init__(self, cache_dir: Path, refresh: bool = False, retries: int = 4, backoff: float = 5,
                 max_backoff: float = 60, session: requests.Session | None = None):
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.retries = retries
        self.backoff = backoff
        self.max_backoff = max_backoff
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.session.headers.setdefault("Accept", "application/ld+json, application/json")

    def cache_path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.json.gz"

    def get(self, url: str) -> tuple[dict | None, bool]:
        """Return ``(manifest, from_cache)``; ``manifest`` is None if it couldn't be fetched."""
        path = self.cache_path(url)
        if not self.refresh and path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f), True
        manifest = self._download(url)
        if manifest is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(manifest, f)
            tmp.replace(path)
        return manifest, False

    def _download(self, url: str) -> dict | None:
        """Retry transient failures (connection errors, 429, 5xx) with
        exponential backoff, same policy as dataverse-etl's http_get_json;
        give up immediately on anything else (a 404 won't fix itself)."""
        for attempt in range(self.retries):
            try:
                r = self.session.get(url, timeout=60)
            except requests.exceptions.RequestException as e:
                reason = repr(e)
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError:
                        print(f"  not JSON: {url} ({r.headers.get('content-type')})", file=sys.stderr)
                        return None
                if r.status_code != 429 and r.status_code < 500:
                    print(f"  {r.status_code}: {url}", file=sys.stderr)
                    return None
                reason = str(r.status_code)
            if attempt < self.retries - 1:
                wait = min(self.backoff * (2 ** attempt), self.max_backoff)
                print(f"  [retry {attempt + 1}] {url} -> {reason}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
        print(f"  giving up: {url} -> {reason}", file=sys.stderr)
        return None
