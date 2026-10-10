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
    if v in ("g", "a", "r"):  # already a code (e.g. derived by an adapter)
        return v.upper()
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
    for k in ("id", "pid", "gsp", "bsp", "dlim", "glim", "nrn", "note"):
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


_BUNDLES = {}


def portal_data(host, dataset, key_name, bundle, fmt="geojson"):
    """An Opendatasoft dataset that needs a login: live via API key when one is configured, otherwise from the
    snapshot exported through the owner's signed-in browser (data/raw/portals/<bundle>_bundle.json, git-ignored).
    fmt is "geojson" (FeatureCollection) or "json" (list of records)."""
    key = api_key(key_name)
    if key:
        url = f"https://{host}/api/explore/v2.1/catalog/datasets/{dataset}/exports/{fmt}"
        return get_json(url, {"Authorization": f"Apikey {key}"})
    path = ROOT / "data" / "raw" / "portals" / f"{bundle}_bundle.json"
    if not path.exists():
        raise MissingKey(key_name)
    if bundle not in _BUNDLES:
        _BUNDLES[bundle] = json.loads(path.read_text())
    if dataset not in _BUNDLES[bundle]:
        raise MissingKey(f"{key_name} (snapshot has no {dataset})")
    return _BUNDLES[bundle][dataset]


_WORST = {"R": 3, "A": 2, "G": 1, None: 0}


def merge_points_into_areas(areas, points, prefer_points=True):
    """Attach heatmap site points to the supply-area polygons that contain them.

    One point in an area: its figures colour the area (overriding the area's own when prefer_points).
    Several (e.g. one heatmap row per transformer): headroom and capacity are summed and the worst RAG wins.
    Points outside every published area are returned as point sites so nothing is dropped."""
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    geoms = [shape(a["geometry"]) for a in areas]
    tree = STRtree(geoms)
    groups, loose = {}, []
    for pt in points:
        g = shape(pt["geometry"])
        hit = next((i for i in tree.query(g) if geoms[i].contains(g)), None)
        if hit is None:
            loose.append(pt)
        else:
            groups.setdefault(hit, []).append(pt["properties"])
    for i, pts in groups.items():
        a = areas[i]["properties"]
        if not prefer_points and a.get("drag"):
            continue
        for k in ("dfirm", "dmax", "dhead", "ghead"):
            vals = [p[k] for p in pts if p[k] is not None]
            a[k] = round(sum(vals), 2) if vals else a.get(k)
        for rag_k, lim_k in (("drag", "dlim"), ("grag", "glim")):
            worst = max(pts, key=lambda p: _WORST[p[rag_k]])
            if worst[rag_k]:
                a[rag_k], a[lim_k] = worst[rag_k], worst[lim_k]
        for k in ("gsp", "bsp"):
            a[k] = a.get(k) or pts[0].get(k)
        if len(pts) > 1:
            a["note"] = f"{len(pts)} heatmap entries combined (sum of headroom, worst RAG)"
    return areas + loose
