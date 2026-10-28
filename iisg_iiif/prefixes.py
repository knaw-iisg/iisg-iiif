"""Namespace/prefix declarations. Reuses findingaid-etl's ``iisgv:``/``sdo:``
namespaces so this graph's triples land on the same record IRIs."""

from rdflib import Namespace

BASE = "https://iisg.amsterdam/"
IISGV = Namespace(BASE + "vocab/")

SDO = Namespace("https://schema.org/")

DEFAULT_GRAPH = "https://iisg.amsterdam/graph/iiif"

# What findingaid-etl wraps each manifest URL in when it emits sdo:contentUrl
# (see findingaid_etl/component.py's _map_digital_objects).
UNIVERSAL_VIEWER_PREFIX = "https://access.iisg.amsterdam/universalviewer/#?manifest="

NAMESPACE_BINDINGS = {
    "iisgv": IISGV,
    "sdo": SDO,
}
