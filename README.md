# iisg-iiif

Harvests IISG's [IIIF Presentation 3](https://iiif.io/api/presentation/3.0/)
manifests and publishes them as schema.org RDF, attached to the
[findingaid-etl](https://github.com/knaw-iisg/findingaid-etl) and
[biblio-etl](https://github.com/knaw-iisg/biblio-etl) records they
illustrate: per record, its manifest, a thumbnail, and every canvas as an
`sdo:ImageObject` with its full-size image, pixel dimensions, IIIF Image
API base and access level.

Both pipelines already emit each record's manifest, but only as a Universal
Viewer link (`sdo:contentUrl <https://access.iisg.amsterdam/universalviewer/#?manifest=...>`;
findingaid-etl from EAD `<daoloc>`, biblio-etl from MARC 856). Nothing
dereferences it, so the images themselves aren't in the knowledge graph.
This pipeline fills that in.

## Public instance

This pipeline's output is one of the graphs merged into the public
knowledge graph at **https://kb.zijdeman.nl** (SPARQL:
**https://sparql.zijdeman.nl**), in the named graph
`https://iisg.amsterdam/graph/iiif` -- see
[iisg-kg-etl](https://github.com/knaw-iisg/iisg-kg-etl) and
[triplestore](https://github.com/knaw-iisg/triplestore).

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
```

## Usage

```bash
.venv/bin/python -m iisg_iiif.cli \
    --links-from ../iisg-kg-etl/output/findingaid.nt \
    --links-from ../iisg-kg-etl/output/biblio.nt
```

It doesn't harvest EAD or MARC itself: each `--links-from` is another
pipeline's N-Triples output, which it scans for those viewer links. Output goes to `data/iiif.nt` unless you pass
`--out`.

| Flag | Does |
|---|---|
| `--limit 20` | quick test run on the first 20 manifests |
| `--workers 4` | concurrent manifest requests (default 4) |
| `--cache-only` | make no requests: map only the manifests already cached (e.g. while IISG's manifest service is down) |
| `--delay 0.5` | at least this many seconds between requests, across all workers, to go easy on IISG's server |
| `--max-minutes 60` | stop requesting after this long and finish from the cache; the rest is fetched next run |
| `--outage-pause 15` | minutes to stop requesting when IISG's server looks down (5 failed manifests in a row), before trying again |
| `--max-outage 30` | minutes of outage after which the run stops requesting and finishes from the cache |
| `--refresh` | re-fetch every manifest and access level instead of reusing the cache |
| `--cache-dir` | where fetched manifests are kept (default `data/cache/manifests/`) |

### Caching

There are ~112k manifests from the finding aids alone. Every one that's
fetched successfully is kept as gzipped JSON under `data/cache/manifests/`
(gitignored), together with its access level (below), and reused on later
runs. So the first run takes an hour or two, and each nightly run
after that only fetches manifests that are new since the last one. Failed
fetches (404s, timeouts after retries) aren't cached, so they're retried
the next run. If IISG's server goes down mid-run, the run doesn't hang on
timeouts: it pauses (`--outage-pause`), tries again, and after
`--max-outage` finishes with what it has. Use `--refresh` when existing manifests have changed
upstream.

The output file is written to `<out>.tmp` and renamed when the run
finishes, so an interrupted run never leaves a half-written graph behind.

## What it produces

```turtle
<https://iisg.amsterdam/id/collection/COLL00357#2>
    iisgv:iiifManifest <https://access.iisg.amsterdam/iiif/presentation/COLL00357.2/manifest> ;
    sdo:thumbnailUrl   <https://access.iisg.amsterdam/iiif/image/COLL00357.2/full/200,/0/default.jpg> ;
    sdo:image          <https://access.iisg.amsterdam/iiif/presentation/COLL00357.2/canvas/1> .

<https://access.iisg.amsterdam/iiif/presentation/COLL00357.2/manifest>
    a sdo:MediaObject ;
    sdo:name "Mustafa Baş (links) met zangeres Sebahat Eroğlu in een Turkse nachtclub in Den Haag. Den Haag." ;
    sdo:creditText "Collection International Institute of Social History (Amsterdam)"@en ;
    sdo:encodingFormat "application/ld+json;profile=\"http://iiif.io/api/presentation/3/context.json\"" .

<https://access.iisg.amsterdam/iiif/presentation/COLL00357.2/canvas/1>
    a sdo:ImageObject ;
    sdo:isPartOf <https://access.iisg.amsterdam/iiif/presentation/COLL00357.2/manifest> ;
    sdo:position 1 ;
    sdo:contentUrl <https://access.iisg.amsterdam/iiif/image/1E64C910-BE3D-11E7-A170-7600EFCD2CB1/full/full/0/default.jpg> ;
    sdo:thumbnailUrl <https://access.iisg.amsterdam/iiif/image/1E64C910-BE3D-11E7-A170-7600EFCD2CB1/full/200,/0/default.jpg> ;
    sdo:encodingFormat "image/jpeg" ;
    sdo:width  [ a sdo:QuantitativeValue ; sdo:value 3546 ; sdo:unitCode "E37" ] ;   # E37 = pixel
    sdo:height [ a sdo:QuantitativeValue ; sdo:value 2415 ; sdo:unitCode "E37" ] ;
    iisgv:iiifImageService <https://access.iisg.amsterdam/iiif/image/1E64C910-BE3D-11E7-A170-7600EFCD2CB1> ;
    iisgv:accessLevel accessLevel:open .
```

### Sizes and access

An item like a poster has three sizes, and each one belongs to a different
resource:

| Size | Of | Where |
|---|---|---|
| physical, e.g. 78 × 53 cm | the item itself | biblio-etl, from MARC 300 (`iisgv:size`) |
| pixels, e.g. 6637 × 9423 | the master scan (the canvas) | here, `sdo:width`/`sdo:height` with unit `E37` |
| how large the public may view it | not a size: an access rule | here, `iisgv:accessLevel` |

IISG's image server caps the public copy of some images, and only shows
it by redirecting each image to a `{id}__{level}` variant: for example
`…__pictoright` (at most 450 px) or `…__restricted` (at most 1500 px). The
manifest looks the same either way. So the level is read from where the
manifest's thumbnail redirects to: `accessLevel:open` if it doesn't
redirect, otherwise `accessLevel:{level}`, plus a human-readable
`sdo:conditionsOfAccess` for the levels it knows. That's one HEAD request
per manifest, cached. biblio-etl writes the same `accessLevel:` IRIs on the
catalogue item, from MARC 542 $m.

Sound and video canvases get `sdo:AudioObject`/`sdo:VideoObject` (plus
`sdo:duration`) and hang off the record via `sdo:associatedMedia` rather
than `sdo:image`. See `iisg_iiif/mapper.py`'s docstring for the mapping
decisions (why the manifest's own IRIs are reused, why width/height are
plain integers, why the manifest's `metadata` block is skipped).

## Tests

```bash
.venv/bin/python -m pytest
```
