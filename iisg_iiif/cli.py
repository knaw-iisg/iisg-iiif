"""Command-line entry point: ``python -m iisg_iiif.cli``.

Reads manifest links out of findingaid-etl's and biblio-etl's N-Triples
(``--links-from``), fetches each manifest and its access level (both
cached, see fetch.py), and streams schema.org
N-Triples to ``--out``. Output is written to a temporary file and only
moved into place once the run finishes, so a crashed run never leaves a
half-written graph where the triplestore would pick it up.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path

from rdflib import Graph, URIRef

from .fetch import ManifestFetcher
from .links import iter_manifest_links
from .mapper import is_presentation_3, manifest_thumbnail, map_manifest

THIS_REPO = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = THIS_REPO / "data" / "cache" / "manifests"
DEFAULT_OUT = THIS_REPO / "data" / "iiif.nt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IISG IIIF manifests -> schema.org N-Triples")
    parser.add_argument("--links-from", type=Path, action="append", required=True,
                        help="N-Triples file to read manifest links from (findingaid-etl's "
                             "findingaid.nt, biblio-etl's biblio.nt); repeatable")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output N-Triples path (default: {DEFAULT_OUT.relative_to(THIS_REPO)})")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                        help=f"where fetched manifests are kept (default: {DEFAULT_CACHE_DIR.relative_to(THIS_REPO)})")
    parser.add_argument("--refresh", action="store_true",
                        help="re-fetch every manifest and access level instead of reusing cached copies")
    parser.add_argument("--cache-only", action="store_true",
                        help="make no requests: map only the manifests already cached, skip the rest")
    parser.add_argument("--workers", type=int, default=4,
                        help="concurrent manifest requests (default: 4)")
    parser.add_argument("--limit", type=int, help="stop after this many manifests (for a quick test run)")
    args = parser.parse_args(argv)

    # A missing source (say biblio-etl has never produced output on this box)
    # shouldn't stop the others' manifests from being harvested.
    sources = [p for p in args.links_from if p.exists()]
    for path in sorted(set(args.links_from) - set(sources)):
        print(f"warning: --links-from file not found, skipped: {path}", file=sys.stderr)
    if not sources:
        parser.error("none of the --links-from files exist")

    if args.cache_only and args.refresh:
        parser.error("--cache-only and --refresh exclude each other")

    links = list(islice(iter_manifest_links(sources), args.limit))

    fetcher = ManifestFetcher(args.cache_dir, refresh=args.refresh, offline=args.cache_only)
    if args.cache_only:
        cached = [link for link in links if fetcher.cache_path(link.manifest_url).exists()]
        print(f"--cache-only: {len(cached)} of {len(links)} manifests cached, skipping the rest", file=sys.stderr)
        links = cached
    stats = {"manifests": 0, "fetched": 0, "cached": 0, "failed": 0, "unsupported": 0, "canvases": 0, "triples": 0}
    access_levels: Counter[str] = Counter()

    def fetch(url: str):
        manifest, from_cache = fetcher.get(url)
        if manifest is None or not is_presentation_3(manifest):
            return manifest, from_cache, None
        return manifest, from_cache, fetcher.access_level(url, manifest_thumbnail(manifest))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = args.out.with_name(args.out.name + ".tmp")
    with ThreadPoolExecutor(max_workers=args.workers) as pool, tmp_out.open("w", encoding="utf-8") as out:
        # map() keeps input order, so the output is stable from run to run.
        results = pool.map(fetch, (link.manifest_url for link in links))
        for link, (manifest, from_cache, access_level) in zip(links, results):
            stats["manifests"] += 1
            if manifest is None:
                stats["failed"] += 1
                continue
            stats["cached" if from_cache else "fetched"] += 1
            if not is_presentation_3(manifest):
                stats["unsupported"] += 1
                print(f"  not a IIIF Presentation 3 manifest, skipped: {link.manifest_url}", file=sys.stderr)
                continue
            g = Graph()
            stats["canvases"] += map_manifest(g, URIRef(link.record), manifest, access_level)
            access_levels[access_level or "unknown"] += 1
            stats["triples"] += len(g)
            out.write(g.serialize(format="nt"))
            if stats["manifests"] % 1000 == 0:
                print(f"  {stats['manifests']} manifests ({stats['fetched']} fetched, "
                      f"{stats['failed']} failed)", file=sys.stderr)
    tmp_out.replace(args.out)

    print(", ".join(f"{k}: {v}" for k, v in stats.items()))
    print("access levels: " + ", ".join(f"{k}: {v}" for k, v in access_levels.most_common()))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
