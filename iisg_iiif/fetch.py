"""Fetching manifests, behind an on-disk cache.

There are ~112k manifests, one HTTP round trip (plus a hdl.handle.net
redirect) each, so re-fetching all of them every night isn't an option.
Each successfully fetched manifest is kept as gzipped JSON under the cache
directory, keyed by a hash of its URL; by default a cached manifest is
reused as-is, so a nightly run only fetches manifests that are new since
the last one. ``--refresh`` re-fetches everything (e.g. after IISG changes
its manifest service).

Each manifest's access level is cached the same way, next to it. IISG's
image server caps what the public can download for some images, and says
so only by redirecting the image to a ``{id}__{level}`` variant (e.g.
``N30051001508412__pictoright``, at most 450 px). The manifest itself
looks the same either way, so the level is read from where its thumbnail
redirects to: one HEAD request per manifest, not per canvas.

Failures aren't cached, so a manifest that 404s or times out tonight is
simply tried again tomorrow. With ``offline`` (``--cache-only``) nothing is
requested at all: only what's already cached is returned, e.g. to publish
a partial harvest while IISG's manifest service is down.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import requests

USER_AGENT = "iisg-iiif/0.1 (+https://github.com/knaw-iisg/iisg-iiif)"

# What an un-capped image is recorded as.
OPEN = "open"
_LEVEL_REDIRECT = re.compile(r"/iiif/image/[^/]+__([a-z0-9-]+)/")


class ManifestFetcher:
    def __init__(self, cache_dir: Path, refresh: bool = False, retries: int = 4, backoff: float = 5,
                 max_backoff: float = 60, session: requests.Session | None = None, offline: bool = False):
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.offline = offline
        self.retries = retries
        self.backoff = backoff
        self.max_backoff = max_backoff
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.session.headers.setdefault("Accept", "application/ld+json, application/json")

    def cache_path(self, url: str, suffix: str = ".json.gz") -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}{suffix}"

    def get(self, url: str) -> tuple[dict | None, bool]:
        """Return ``(manifest, from_cache)``; ``manifest`` is None if it couldn't be fetched."""
        path = self.cache_path(url)
        if not self.refresh and path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f), True
        if self.offline:
            return None, False
        manifest = self._download(url)
        if manifest is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(manifest, f)
            tmp.replace(path)
        return manifest, False

    def access_level(self, url: str, thumbnail: str | None) -> str | None:
        """The access level of manifest ``url``'s images, judged by where its
        ``thumbnail`` redirects: ``"open"`` if it doesn't, else the
        ``__{level}`` suffix (``"pictoright"``, ``"restricted"``, ...).
        None if it couldn't be determined."""
        if not thumbnail:
            return None
        path = self.cache_path(url, ".access")
        if not self.refresh and path.exists():
            return path.read_text().strip()
        if self.offline:
            return None
        r = self._request("HEAD", thumbnail, allow_redirects=False)
        if r is None:
            return None
        if r.status_code == 200:
            level = OPEN
        else:
            m = _LEVEL_REDIRECT.search(r.headers.get("location", ""))
            if not (r.is_redirect and m):
                print(f"  can't tell access level: {thumbnail} -> {r.status_code} "
                      f"{r.headers.get('location', '')}", file=sys.stderr)
                return None
            level = m.group(1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(level)
        return level

    def _download(self, url: str) -> dict | None:
        r = self._request("GET", url)
        if r is None:
            return None
        if r.status_code != 200:
            print(f"  {r.status_code}: {url}", file=sys.stderr)
            return None
        try:
            return r.json()
        except ValueError:
            print(f"  not JSON: {url} ({r.headers.get('content-type')})", file=sys.stderr)
            return None

    def _request(self, method: str, url: str, **kwargs) -> requests.Response | None:
        """Retry transient failures (connection errors, 429, 5xx) with
        exponential backoff, same policy as dataverse-etl's http_get_json;
        return any other response as-is (a 404 won't fix itself). None if
        the retries ran out."""
        for attempt in range(self.retries):
            try:
                r = self.session.request(method, url, timeout=60, **kwargs)
            except requests.exceptions.RequestException as e:
                reason = repr(e)
            else:
                if r.status_code != 429 and r.status_code < 500:
                    return r
                reason = str(r.status_code)
            if attempt < self.retries - 1:
                wait = min(self.backoff * (2 ** attempt), self.max_backoff)
                print(f"  [retry {attempt + 1}] {url} -> {reason}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
        print(f"  giving up: {url} -> {reason}", file=sys.stderr)
        return None
