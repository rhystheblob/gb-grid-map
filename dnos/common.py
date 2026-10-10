"""Shared helpers for the per-DNO adapters: HTTP, API keys, portal APIs and the common site schema.

Every adapter returns a list of features in one common shape, modelled on the LTDS Capacity Heatmap
that all GB DNOs publish:

    level   "gsp" | "bsp" | "primary"
    name    substation name as the DNO publishes it
    gsp,bsp upstream GSP / BSP names (None where not published)
    dfirm   demand firm capacity, MVA
    dmax    maximum observed demand, MVA
    dhead   estimated demand headroom (available capacity), MVA
    drag    demand RAG: "G" | "A" | "R" | None
    dlim    demand limiting factor / constraint text
    ghead   estimated generation headroom, MW
    grag    generation RAG
    glim    generation limiting factor / constraint text
    nrn     DNO network reference where it links to deeper levels (SSEN primaries -> secondaries)
    id,pid  optional site id / parent site id when the DNO publishes the hierarchy; build_gb.py fills the
            rest by location (child inside parent's supply area) or by name

plus a GeoJSON geometry: the supply-area polygon where the DNO publishes one, otherwise the site point.
"""
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = {"User-Agent": "gb-grid-map/1.0 (+https://github.com/rhystheblob/gb-grid-map)"}
LEVELS = ("gsp", "bsp", "primary")
SIMPLIFY = {"gsp": 0.001, "bsp": 0.0008, "primary": 0.0006}  # degrees, as for the SSEN layers


class MissingKey(Exception):
    """Raised when a DNO portal needs an API key that isn't configured."""


def api_key(name):
    """Key from the environment (cloud runs) or .env (local runs); None if absent."""
    if os.environ.get(name):
        return os.environ[name].strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name and v.strip():
                return v.strip().strip("'\"")
    return None


def get_json(url, headers=None, timeout=300):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ods_geojson(host, dataset, key_name=None, where=None):
    """Whole Opendatasoft dataset as GeoJSON via the export endpoint (no record cap)."""
    headers = {}
    if key_name:
        key = api_key(key_name)
        if not key:
            raise MissingKey(key_name)
        headers["Authorization"] = f"Apikey {key}"
    params = {"where": where} if where else {}
    url = f"https://{host}/api/explore/v2.1/catalog/datasets/{dataset}/exports/geojson"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return get_json(url, headers)


def ckan_package(base, package, key_name=None):
    headers = {}
    if key_name:
        key = api_key(key_name)
        if not key:
            raise MissingKey(key_name)
        headers["Authorization"] = key
    return get_json(f"{base}/api/3/action/package_show?id={package}", headers)["result"]


def rag(value):
    """Normalise the many RAG spellings ('Green', 'green', 'Amber - Fault Level', ...) to G/A/R."""
    if not value:
        return None
    v = str(value).strip().lower()
    for word, code in (("green", "G"), ("amber", "A"), ("red", "R")):
        if v.startswith(word):
            return code
    return None


def num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 2)  # drop NaN


def text(value):
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value if v)
    value = (str(value).strip() if value is not None else "")
    return value or None


def site(level, name, geometry, **fields):
    props = {"level": level, "name": text(name)}
    for k in ("id", "pid", "gsp", "bsp", "dlim", "glim", "nrn"):
        props[k] = text(fields.get(k))
    for k in ("dfirm", "dmax", "dhead", "ghead"):
        props[k] = num(fields.get(k))
    props["drag"] = rag(fields.get("drag"))
    props["grag"] = rag(fields.get("grag"))
    return {"type": "Feature", "properties": props, "geometry": geometry}


def point(lon, lat):
    lon, lat = num(lon), num(lat)
    if lon is None or lat is None:
        return None
    return {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]}


def simplify(geometry, level):
    """Simplify polygons for web display; points pass through."""
    if not geometry or geometry["type"] == "Point":
        return geometry
    from shapely.geometry import mapping, shape
    g = shape(geometry).simplify(SIMPLIFY[level], preserve_topology=True)
    if g.is_empty:
        return None
    return json.loads(json.dumps(mapping(g)), parse_float=lambda x: round(float(x), 5))
