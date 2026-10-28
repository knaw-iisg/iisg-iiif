# iisg-iiif

Harvests IISG's [IIIF Presentation 3](https://iiif.io/api/presentation/3.0/)
manifests and publishes them as schema.org RDF, attached to the
[findingaid-etl](https://github.com/knaw-iisg/findingaid-etl) records they
illustrate: per record, its manifest, a thumbnail, and every canvas as an
`sdo:ImageObject` with its full-size image, dimensions and IIIF Image API
base.

findingaid-etl already emits each component's manifest, but only as a
Universal Viewer link (`sdo:contentUrl <https://access.iisg.amsterdam/universalviewer/#?manifest=...>`).
Nothing dereferences it, so the images themselves aren't in the knowledge
graph. This pipeline fills that in.

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
.venv/bin/python -m iisg_iiif.cli --links-from ../iisg-kg-etl/output/findingaid.nt
```

It doesn't harvest EAD itself: `--links-from` is findingaid-etl's
N-Triples output, which it scans for those viewer links (repeatable, for
more than one source file). Output goes to `data/iiif.nt` unless you pass
`--out`.

| Flag | Does |
|---|---|
| `--limit 20` | quick test run on the first 20 manifests |
| `--workers 4` | concurrent manifest requests (default 4) |
| `--refresh` | re-fetch every manifest instead of reusing the cache |
| `--cache-dir` | where fetched manifests are kept (default `data/cache/manifests/`) |

### Caching

There are ~112k manifests. Every one that's fetched successfully is kept
as gzipped JSON under `data/cache/manifests/` (gitignored), and reused on
later runs. So the first run takes an hour or two, and each nightly run
after that only fetches manifests that are new since the last one. Failed
fetches (404s, timeouts after retries) aren't cached, so they're retried
the next run. Use `--refresh` when existing manifests have changed
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
    sdo:width 3546 ;
    sdo:height 2415 ;
    iisgv:iiifImageService <https://access.iisg.amsterdam/iiif/image/1E64C910-BE3D-11E7-A170-7600EFCD2CB1> .
```

Sound and video canvases get `sdo:AudioObject`/`sdo:VideoObject` (plus
`sdo:duration`) and hang off the record via `sdo:associatedMedia` rather
than `sdo:image`. See `iisg_iiif/mapper.py`'s docstring for the mapping
decisions (why the manifest's own IRIs are reused, why width/height are
plain integers, why the manifest's `metadata` block is skipped).

## Tests

```bash
.venv/bin/python -m pytest
```
