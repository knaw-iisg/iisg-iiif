"""Find the IIIF manifests to harvest, by reading them out of another
pipeline's N-Triples output rather than harvesting EAD a second time.

findingaid-etl emits, for every component with a ``manifest``-labeled
``<daoloc>``::

    <https://iisg.amsterdam/id/collection/COLL00357#2>
        <https://schema.org/contentUrl>
        <https://access.iisg.amsterdam/universalviewer/#?manifest=https://hdl.handle.net/10622/COLL00357.2?locatt=view:manifest> .

biblio-etl emits the same shape from MARC 856 for digitised catalogue
items (``<https://iisg.amsterdam/id/item/978601>``). The record IRI is what
this pipeline attaches its triples to; the part after ``manifest=`` is the
manifest URL to fetch.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, NamedTuple

from .prefixes import SDO, UNIVERSAL_VIEWER_PREFIX

_LINE = re.compile(
    r"^<([^>]+)> <" + re.escape(str(SDO.contentUrl)) + r"> <"
    + re.escape(UNIVERSAL_VIEWER_PREFIX) + r"([^>]+)> \.\s*$"
)


class ManifestLink(NamedTuple):
    record: str  # the findingaid-etl subject IRI
    manifest_url: str


def iter_manifest_links(paths: list[Path]) -> Iterator[ManifestLink]:
    """Yield each distinct (record, manifest) pair across ``paths``, in file order.

    A plain line scan rather than an rdflib parse: findingaid.nt is ~600 MB,
    and only this one predicate/object shape matters here.
    """
    seen: set[ManifestLink] = set()
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if UNIVERSAL_VIEWER_PREFIX not in line:
                    continue
                m = _LINE.match(line)
                if not m:
                    continue
                link = ManifestLink(m.group(1), m.group(2))
                if link not in seen:
                    seen.add(link)
                    yield link
