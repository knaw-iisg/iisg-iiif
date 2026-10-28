import gzip
import json
from pathlib import Path

from rdflib import RDF, XSD, Graph, Literal, URIRef

from iisg_iiif import cli
from iisg_iiif.fetch import ManifestFetcher
from iisg_iiif.links import ManifestLink, iter_manifest_links
from iisg_iiif.mapper import is_presentation_3, map_manifest
from iisg_iiif.prefixes import IISGV, SDO

FIXTURES = Path(__file__).parent / "fixtures"
RECORD = URIRef("https://iisg.amsterdam/id/collection/COLL00357#2")
HANDLE = "https://hdl.handle.net/10622/COLL00357.2?locatt=view:manifest"
MANIFEST = URIRef("https://access.iisg.amsterdam/iiif/presentation/COLL00357.2/manifest")
CANVAS = URIRef("https://access.iisg.amsterdam/iiif/presentation/COLL00357.2/canvas/1")
IMAGE_ID = "1E64C910-BE3D-11E7-A170-7600EFCD2CB1"

FINDINGAID_NT = f"""\
<{RECORD}> <https://schema.org/name> "Mustafa Bas" .
<{RECORD}> <https://schema.org/contentUrl> <https://access.iisg.amsterdam/universalviewer/#?manifest={HANDLE}> .
<{RECORD}> <https://schema.org/contentUrl> <https://access.iisg.amsterdam/universalviewer/#?manifest={HANDLE}> .
<https://iisg.amsterdam/id/collection/COLL00357> <https://schema.org/contentUrl> <https://example.org/not-a-viewer> .
"""


def load_fixture():
    return json.loads((FIXTURES / "COLL00357.2.json").read_text())


def test_links_are_read_from_findingaid_nt_and_deduplicated(tmp_path):
    nt = tmp_path / "findingaid.nt"
    nt.write_text(FINDINGAID_NT)
    assert list(iter_manifest_links([nt])) == [ManifestLink(str(RECORD), HANDLE)]


def test_map_manifest():
    manifest = load_fixture()
    assert is_presentation_3(manifest)
    g = Graph()
    assert map_manifest(g, RECORD, manifest) == 1

    assert (RECORD, IISGV.iiifManifest, MANIFEST) in g
    assert (RECORD, SDO.thumbnailUrl,
            URIRef("https://access.iisg.amsterdam/iiif/image/COLL00357.2/full/200,/0/default.jpg")) in g
    assert (RECORD, SDO.image, CANVAS) in g

    assert (MANIFEST, RDF.type, SDO.MediaObject) in g
    assert g.value(MANIFEST, SDO.name).startswith("Mustafa Baş")
    assert (MANIFEST, SDO.creditText,
            Literal("Collection International Institute of Social History (Amsterdam)", lang="en")) in g

    assert (CANVAS, RDF.type, SDO.ImageObject) in g
    assert (CANVAS, SDO.isPartOf, MANIFEST) in g
    assert (CANVAS, SDO.position, Literal(1)) in g
    assert (CANVAS, SDO.contentUrl,
            URIRef(f"https://access.iisg.amsterdam/iiif/image/{IMAGE_ID}/full/full/0/default.jpg")) in g
    assert (CANVAS, SDO.width, Literal(3546, datatype=XSD.integer)) in g
    assert (CANVAS, SDO.height, Literal(2415, datatype=XSD.integer)) in g
    assert (CANVAS, SDO.encodingFormat, Literal("image/jpeg")) in g
    assert (CANVAS, IISGV.iiifImageService, URIRef(f"https://access.iisg.amsterdam/iiif/image/{IMAGE_ID}")) in g


def test_presentation_2_is_not_mapped():
    assert not is_presentation_3({"@context": "http://iiif.io/api/presentation/2/context.json",
                                  "@type": "sc:Manifest"})


def test_fetcher_serves_cached_manifest_without_a_request(tmp_path):
    fetcher = ManifestFetcher(tmp_path, session=_NoNetwork())
    path = fetcher.cache_path(HANDLE)
    path.parent.mkdir(parents=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(load_fixture(), f)
    manifest, from_cache = fetcher.get(HANDLE)
    assert from_cache and manifest["id"] == str(MANIFEST)


def test_cli_end_to_end_from_cache(tmp_path, monkeypatch):
    nt = tmp_path / "findingaid.nt"
    nt.write_text(FINDINGAID_NT)
    cache = tmp_path / "cache"
    seed = ManifestFetcher(cache).cache_path(HANDLE)
    seed.parent.mkdir(parents=True)
    with gzip.open(seed, "wt", encoding="utf-8") as f:
        json.dump(load_fixture(), f)
    monkeypatch.setattr("iisg_iiif.fetch.requests.Session", _NoNetwork)

    out = tmp_path / "iiif.nt"
    assert cli.main(["--links-from", str(nt), "--cache-dir", str(cache), "--out", str(out)]) == 0
    g = Graph().parse(out, format="nt")
    assert (RECORD, SDO.image, CANVAS) in g
    assert not out.with_name("iiif.nt.tmp").exists()


class _NoNetwork:
    headers: dict

    def __init__(self):
        self.headers = {}

    def get(self, *args, **kwargs):
        raise AssertionError("unexpected network request")
