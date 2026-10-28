import gzip
import json
from pathlib import Path

import requests

from rdflib import RDF, XSD, Graph, Literal, URIRef

from iisg_iiif import cli
from iisg_iiif.fetch import ManifestFetcher
from iisg_iiif.links import ManifestLink, iter_manifest_links
from iisg_iiif.mapper import is_presentation_3, map_manifest
from iisg_iiif.prefixes import ACCESS_LEVEL, IISGV, SDO

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
    for prop, pixels in ((SDO.width, 3546), (SDO.height, 2415)):
        size = g.value(CANVAS, prop)
        assert (size, RDF.type, SDO.QuantitativeValue) in g
        assert (size, SDO.value, Literal(pixels, datatype=XSD.integer)) in g
        assert (size, SDO.unitCode, Literal("E37")) in g
    assert (CANVAS, SDO.encodingFormat, Literal("image/jpeg")) in g
    assert (CANVAS, IISGV.iiifImageService, URIRef(f"https://access.iisg.amsterdam/iiif/image/{IMAGE_ID}")) in g
    assert g.value(CANVAS, IISGV.accessLevel) is None  # not given, so not asserted


def test_pixel_size_nodes_are_stable_across_runs():
    a, b = Graph(), Graph()
    map_manifest(a, RECORD, load_fixture())
    map_manifest(b, RECORD, load_fixture())
    assert a.value(CANVAS, SDO.width) == b.value(CANVAS, SDO.width)


def test_access_level_on_canvases():
    g = Graph()
    map_manifest(g, RECORD, load_fixture(), access_level="pictoright")
    assert (CANVAS, IISGV.accessLevel, ACCESS_LEVEL.pictoright) in g
    assert "Pictoright" in g.value(CANVAS, SDO.conditionsOfAccess)

    g = Graph()
    map_manifest(g, RECORD, load_fixture(), access_level="open")
    assert (CANVAS, IISGV.accessLevel, ACCESS_LEVEL.open) in g
    assert g.value(CANVAS, SDO.conditionsOfAccess) is None


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


THUMB = "https://access.iisg.amsterdam/iiif/image/N30051001508412/full/200,/0/default.jpg"


def test_access_level_from_thumbnail_redirect(tmp_path):
    session = _FakeSession({THUMB: (302, "/iiif/image/N30051001508412__pictoright/full/200,/0/default.jpg")})
    fetcher = ManifestFetcher(tmp_path, session=session)
    assert fetcher.access_level(HANDLE, THUMB) == "pictoright"
    # cached: a second call makes no request
    fetcher.session = _NoNetwork()
    assert fetcher.access_level(HANDLE, THUMB) == "pictoright"


def test_access_level_open_when_not_redirected(tmp_path):
    fetcher = ManifestFetcher(tmp_path, session=_FakeSession({THUMB: (200, None)}))
    assert fetcher.access_level(HANDLE, THUMB) == "open"


def test_access_level_unknown_is_not_cached(tmp_path):
    fetcher = ManifestFetcher(tmp_path, session=_FakeSession({THUMB: (404, None)}))
    assert fetcher.access_level(HANDLE, THUMB) is None
    assert not fetcher.cache_path(HANDLE, ".access").exists()


def test_cli_end_to_end_from_cache(tmp_path, monkeypatch):
    nt = tmp_path / "findingaid.nt"
    nt.write_text(FINDINGAID_NT)
    cache = tmp_path / "cache"
    seed = ManifestFetcher(cache).cache_path(HANDLE)
    seed.parent.mkdir(parents=True)
    with gzip.open(seed, "wt", encoding="utf-8") as f:
        json.dump(load_fixture(), f)
    ManifestFetcher(cache).cache_path(HANDLE, ".access").write_text("restricted")
    monkeypatch.setattr("iisg_iiif.fetch.requests.Session", _NoNetwork)

    out = tmp_path / "iiif.nt"
    assert cli.main(["--links-from", str(nt), "--cache-dir", str(cache), "--out", str(out)]) == 0
    g = Graph().parse(out, format="nt")
    assert (RECORD, SDO.image, CANVAS) in g
    assert (CANVAS, IISGV.accessLevel, ACCESS_LEVEL.restricted) in g
    assert not out.with_name("iiif.nt.tmp").exists()


def test_cli_cache_only_skips_uncached_without_a_request(tmp_path, monkeypatch):
    nt = tmp_path / "findingaid.nt"
    other = "https://hdl.handle.net/10622/COLL00357.3?locatt=view:manifest"
    nt.write_text(FINDINGAID_NT + f"<{RECORD}3> <https://schema.org/contentUrl> "
                  f"<https://access.iisg.amsterdam/universalviewer/#?manifest={other}> .\n")
    cache = tmp_path / "cache"
    seed = ManifestFetcher(cache).cache_path(HANDLE)
    seed.parent.mkdir(parents=True)
    with gzip.open(seed, "wt", encoding="utf-8") as f:
        json.dump(load_fixture(), f)  # no .access file: must not be looked up either
    monkeypatch.setattr("iisg_iiif.fetch.requests.Session", _NoNetwork)

    out = tmp_path / "iiif.nt"
    assert cli.main(["--links-from", str(nt), "--cache-dir", str(cache), "--out", str(out), "--cache-only"]) == 0
    g = Graph().parse(out, format="nt")
    assert (RECORD, SDO.image, CANVAS) in g
    assert g.value(CANVAS, IISGV.accessLevel) is None


def test_fetcher_goes_offline_when_the_service_stays_down(tmp_path):
    session = _DownSession()
    fetcher = ManifestFetcher(tmp_path, session=session, retries=2, backoff=0,
                              outage_after=2, outage_pause=0, max_outage=0)
    for i in range(2):
        assert fetcher.get(f"{HANDLE}&n={i}") == (None, False)
    assert fetcher.offline and session.calls == 4  # 2 URLs x 2 attempts
    assert fetcher.get(f"{HANDLE}&n=2") == (None, False)
    assert session.calls == 4  # offline: no further requests


def test_fetcher_pauses_then_resumes_when_the_service_comes_back(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr("iisg_iiif.fetch.time.sleep", slept.append)
    session = _DownSession()
    fetcher = ManifestFetcher(tmp_path, session=session, retries=1, backoff=0,
                              outage_after=1, outage_pause=900, max_outage=3600)
    assert fetcher.get(HANDLE) == (None, False)  # trips the breaker
    session.down = False
    fetcher.access_level(HANDLE, THUMB)  # waits out the pause, then succeeds
    assert slept and slept[0] > 800
    assert not fetcher.offline and fetcher._down_since is None


class _NoNetwork:
    headers: dict

    def __init__(self):
        self.headers = {}

    def request(self, *args, **kwargs):
        raise AssertionError("unexpected network request")


class _FakeResponse:
    def __init__(self, status_code, location):
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self.is_redirect = status_code in (301, 302, 303, 307, 308) and bool(location)


class _FakeSession(_NoNetwork):
    def __init__(self, responses):
        super().__init__()
        self.responses = responses

    def request(self, method, url, **kwargs):
        return _FakeResponse(*self.responses[url])


class _DownSession(_NoNetwork):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.down = True

    def request(self, method, url, **kwargs):
        self.calls += 1
        if self.down:
            raise requests.exceptions.ConnectionError("down")
        return _FakeResponse(200, None)
