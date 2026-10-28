"""Namespace/prefix declarations. Reuses findingaid-etl's ``iisgv:``/``sdo:``
namespaces so this graph's triples land on the same record IRIs."""

from rdflib import Namespace

BASE = "https://iisg.amsterdam/"
IISGV = Namespace(BASE + "vocab/")
# Values of iisgv:accessLevel; shared with biblio-etl's MARC 542 $m mapping.
ACCESS_LEVEL = Namespace(BASE + "vocab/accessLevel/")

SDO = Namespace("https://schema.org/")

DEFAULT_GRAPH = "https://iisg.amsterdam/graph/iiif"

# What findingaid-etl and biblio-etl wrap each manifest URL in when they emit
# sdo:contentUrl (findingaid_etl/component.py's _map_digital_objects,
# biblio_etl/datafields/f856.py).
UNIVERSAL_VIEWER_PREFIX = "https://access.iisg.amsterdam/universalviewer/#?manifest="

NAMESPACE_BINDINGS = {
    "iisgv": IISGV,
    "accessLevel": ACCESS_LEVEL,
    "sdo": SDO,
}
