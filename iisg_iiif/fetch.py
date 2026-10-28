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

When the service goes down mid-run, every request would otherwise sit
through its timeout and retries, and the run would hang for hours. So
after ``outage_after`` URLs in a row have failed for good (connection
errors, 429, 5xx -- not 404s), the service counts as down: all workers
stop requesting for ``outage_pause`` seconds, then try again with a single
attempt per request. Once it has been down for ``max_outage`` seconds the
fetcher goes ``offline`` for the rest of the run, so the run still
finishes, with whatever was cached or fetched before the outage.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
import threading
import time
from pathlib import Path

import requests

USER_AGENT = "iisg-iiif/0.1 (+https://github.com/knaw-iisg/iisg-iiif)"

# What an un-capped image is recorded as.
OPEN = "open"
_LEVEL_REDIRECT = re.compile(r"/iiif/image/[^/]+__([a-z0-9-]+)/")


class ManifestFetcher:
    def __init__(self, cache_dir: Path, refresh: bool = False, retries: int = 4, backoff: float = 5,
                 max_backoff: float = 60, session: requests.Session | None = None, offline: bool = False,
                 outage_after: int = 5, outage_pause: float = 15 * 60, max_outage: float = 30 * 60):
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.offline = offline
        self.outage_after = outage_after
        self.outage_pause = outage_pause
        self.max_outage = max_outage
        self._lock = threading.Lock()
        self._failures_in_a_row = 0
        self._down_since: float | None = None
        self._resume_at = 0.0
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
        the retries ran out, or the service is down (see the module
        docstring)."""
        if not self._await_service():
            return None
        # While the service is down, a request is just a probe: one attempt.
        retries = 1 if self._down_since is not None else self.retries
        for attempt in range(retries):
            try:
                r = self.session.request(method, url, timeout=60, **kwargs)
            except requests.exceptions.RequestException as e:
                reason = repr(e)
            else:
                if r.status_code != 429 and r.status_code < 500:
                    self._service_up()
                    return r
                reason = str(r.status_code)
            if attempt < retries - 1:
                wait = min(self.backoff * (2 ** attempt), self.max_backoff)
                print(f"  [retry {attempt + 1}] {url} -> {reason}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
        print(f"  giving up: {url} -> {reason}", file=sys.stderr)
        self._service_failed()
        return None

    def _await_service(self) -> bool:
        """Wait out a pause after an outage; False once offline."""
        if self.offline:
            return False
        with self._lock:
            wait = self._resume_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        return not self.offline

    def _service_up(self) -> None:
        with self._lock:
            if self._down_since is not None:
                print("  service is back, resuming", file=sys.stderr)
            self._failures_in_a_row = 0
            self._down_since = None
            self._resume_at = 0.0

    def _service_failed(self) -> None:
        with self._lock:
            self._failures_in_a_row += 1
            if self._failures_in_a_row < self.outage_after or self.offline:
                return
            now = time.monotonic()
            if self._down_since is None:
                self._down_since = now
            down = now - self._down_since
            if down >= self.max_outage:
                self.offline = True
                print(f"  service down for {down / 60:.0f} min, giving up on it: "
                      f"the rest of this run uses only what's cached", file=sys.stderr)
            elif self._resume_at <= now:
                self._resume_at = now + self.outage_pause
                print(f"  {self._failures_in_a_row} requests in a row failed, service looks down: "
                      f"pausing {self.outage_pause / 60:.0f} min", file=sys.stderr)
