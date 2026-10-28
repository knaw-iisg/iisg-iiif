"""IIIF Presentation 3 manifest -> schema.org triples.

Per manifest, attached to the findingaid-etl record that links to it:

    <record> iisgv:iiifManifest <manifest> ;
             sdo:thumbnailUrl   <manifest's thumbnail> ;
             sdo:image          <canvas 1>, <canvas 2>, ... .   # Image canvases
             # (sdo:associatedMedia instead, for Sound/Video canvases)

    <manifest> a sdo:MediaObject ;
               sdo:name, sdo:encodingFormat, sdo:creditText (requiredStatement),
               sdo:license (rights, when present) .

    <canvas> a sdo:ImageObject ;          # or AudioObject / VideoObject
             sdo:isPartOf <manifest> ; sdo:position n ;
             sdo:contentUrl <full-size image> ; sdo:thumbnailUrl ;
             sdo:encodingFormat ; sdo:width ; sdo:height ; sdo:duration ;
             iisgv:iiifImageService <IIIF Image API base> .

Design decisions:
  - The manifest's and canvases' own IRIs (``id``, on access.iisg.amsterdam)
    are used as-is rather than minting iisg.amsterdam/id/... ones: they
    already resolve, to the canonical JSON-LD.
  - sdo:width/sdo:height are plain xsd:integer pixel counts rather than
    sdo:QuantitativeValue nodes -- they're always pixels here, and a bare
    number is what a viewer actually wants to read.
  - iisgv:iiifImageService (the Image API base, not a fixed-size JPEG) is
    what lets a viewer request any crop/size it needs: ``{base}/full/400,/0/default.jpg``.
  - The manifest's ``metadata`` block isn't mapped: it repeats what
    findingaid-etl already says about the record (date, inventory number,
    handle), only as display strings.
  - Only Presentation 3 is mapped. IISG's manifest service serves v3
    throughout; anything else is counted as unsupported in the report
    rather than half-mapped.
"""
from __future__ import annotations

import re

from rdflib import RDF, XSD, Graph, Literal, URIRef

from .prefixes import IISGV, SDO

PRESENTATION_3_CONTEXT = "http://iiif.io/api/presentation/3/context.json"
MANIFEST_FORMAT = f'application/ld+json;profile="{PRESENTATION_3_CONTEXT}"'

BODY_TYPES = {
    "Image": SDO.ImageObject,
    "Sound": SDO.AudioObject,
    "Video": SDO.VideoObject,
}

_TAG = re.compile(r"<[^>]+>")


def is_presentation_3(manifest: dict) -> bool:
    context = manifest.get("@context")
    contexts = context if isinstance(context, list) else [context]
    return PRESENTATION_3_CONTEXT in contexts and manifest.get("type") == "Manifest"


def language_literals(value: dict | None, strip_html: bool = False) -> list[Literal]:
    """A IIIF language map (``{"en": ["..."], "none": ["..."]}``) -> literals; ``none`` gets no tag."""
    out = []
    for lang, strings in (value or {}).items():
        for s in strings:
            if strip_html:
                s = _TAG.sub("", s)
            s = s.strip()
            if s:
                out.append(Literal(s) if lang == "none" else Literal(s, lang=lang))
    return out


def _first_id(resources: list[dict] | None) -> str | None:
    for r in resources or []:
        if r.get("id"):
            return r["id"]
    return None


def _service_id(resource: dict) -> str | None:
    # IISG's v3 manifests embed v2 image services, which keep the v2 "@id" key.
    for service in resource.get("service") or []:
        sid = service.get("id") or service.get("@id")
        if sid:
            return sid
    return None


def _painting_bodies(canvas: dict):
    for page in canvas.get("items") or []:
        for annotation in page.get("items") or []:
            if annotation.get("motivation") != "painting":
                continue
            body = annotation.get("body")
            for b in body if isinstance(body, list) else [body]:
                if isinstance(b, dict) and b.get("id"):
                    yield b


def map_manifest(g: Graph, record: URIRef, manifest: dict) -> int:
    """Add ``manifest``'s triples to ``g``, attached to ``record``. Returns the number of canvases mapped."""
    m = URIRef(manifest["id"])
    g.add((record, IISGV.iiifManifest, m))
    g.add((m, RDF.type, SDO.MediaObject))
    g.add((m, SDO.encodingFormat, Literal(MANIFEST_FORMAT)))
    for label in language_literals(manifest.get("label")):
        g.add((m, SDO.name, label))
    for credit in language_literals((manifest.get("requiredStatement") or {}).get("value"), strip_html=True):
        g.add((m, SDO.creditText, credit))
    if manifest.get("rights"):
        g.add((m, SDO.license, URIRef(manifest["rights"])))

    thumbnail = _first_id(manifest.get("thumbnail"))
    if thumbnail:
        g.add((record, SDO.thumbnailUrl, URIRef(thumbnail)))

    canvases = 0
    for position, canvas in enumerate(manifest.get("items") or [], start=1):
        if canvas.get("type") != "Canvas" or not canvas.get("id"):
            continue
        bodies = list(_painting_bodies(canvas))
        if not bodies:
            continue
        body = bodies[0]  # IISG canvases carry a single painting body
        c = URIRef(canvas["id"])
        media_type = BODY_TYPES.get(body.get("type"), SDO.MediaObject)
        g.add((record, SDO.image if media_type == SDO.ImageObject else SDO.associatedMedia, c))
        g.add((c, RDF.type, media_type))
        g.add((c, SDO.isPartOf, m))
        g.add((c, SDO.position, Literal(position)))
        g.add((c, SDO.contentUrl, URIRef(body["id"])))
        if body.get("format"):
            g.add((c, SDO.encodingFormat, Literal(body["format"])))
        for key in ("width", "height"):
            value = canvas.get(key) or body.get(key)
            if isinstance(value, int):
                g.add((c, SDO[key], Literal(value, datatype=XSD.integer)))
        duration = canvas.get("duration") or body.get("duration")
        if isinstance(duration, (int, float)):
            g.add((c, SDO.duration, Literal(f"PT{duration:g}S", datatype=XSD.duration)))
        canvas_thumbnail = _first_id(canvas.get("thumbnail"))
        if canvas_thumbnail:
            g.add((c, SDO.thumbnailUrl, URIRef(canvas_thumbnail)))
        service = _service_id(body)
        if service:
            g.add((c, IISGV.iiifImageService, URIRef(service)))
        canvases += 1
    return canvases
