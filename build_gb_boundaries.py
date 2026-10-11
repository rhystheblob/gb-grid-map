"""Local authority boundaries, with a dig-once summary per authority.

Uses the upper tier (counties and unitary authorities): in England they are the highway authorities that issue
street works permits and Section 58 restrictions, and in Scotland and Wales every council is unitary. Source: ONS
Open Geography Portal, "Counties and Unitary Authorities (December 2025) Boundaries UK BGC" (generalised to
20 m, public ArcGIS service, no login; Open Government Licence), Northern Ireland dropped (no network data here).

Each authority gets counts from the dig-once layer (run build_gb_digonce.py first): streets with a signal, dug by
3+ utilities in the year, utility works planned, Section 58 restrictions proposed / in force, and the top score.
Dig-once data covers England only (DfT Street Manager), so Scottish and Welsh councils show none.

Output:  data/gb/la.geojson
Usage:   python3 build_gb_boundaries.py
"""
import json
import urllib.parse
import urllib.request

from dnos.common import ROOT

OUT = ROOT / "data" / "gb" / "la.geojson"
DIG = ROOT / "data" / "gb" / "digonce"
CTYUA = ("https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
         "Counties_and_Unitary_Authorities_December_2025_Boundaries_UK_BGC/FeatureServer/0")
UA = {"User-Agent": "gb-grid-map/1.0 (+https://github.com/rhystheblob/gb-grid-map)"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
        return json.load(r)


def boundaries():
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode({
            "where": "CTYUA25CD NOT LIKE 'N%'", "outFields": "CTYUA25CD,CTYUA25NM", "outSR": 4326, "f": "geojson",
            "geometryPrecision": 5, "maxAllowableOffset": 0.0002,  # ~15 m, invisible at map scale
            "resultOffset": offset, "resultRecordCount": 50})
        batch = get(f"{CTYUA}/query?{q}").get("features", [])
        feats += batch
        if len(batch) < 50:
            return feats
        offset += len(batch)


def dig_counts(geoms):
    """Per-authority dig-once counts by point-in-polygon (Section 58 notices by their centroid)."""
    from shapely import STRtree, points
    from shapely.geometry import shape
    tree = STRtree(geoms)
    blank = lambda: {"streets": 0, "multi": 0, "planned": 0, "s58p": 0, "s58f": 0, "top": 0}
    counts = [blank() for _ in geoms]
    st_path, s58_path = DIG / "streets.json", DIG / "s58.geojson"
    if st_path.exists():
        rows = json.load(open(st_path))["points"]  # [lon, lat, score, flags]
        pts = points([[r[0], r[1]] for r in rows])
        for pi, gi in zip(*tree.query(pts, predicate="within")):
            c, (_, _, score, flags) = counts[gi], rows[pi]
            c["streets"] += 1
            c["multi"] += bool(flags & 1)
            c["planned"] += bool(flags & 2)
            c["top"] = max(c["top"], score)
    if s58_path.exists():
        s58 = json.load(open(s58_path))["features"]
        pts = [shape(f["geometry"]).representative_point() for f in s58]
        for pi, gi in zip(*tree.query(pts, predicate="within")):
            counts[gi]["s58p" if s58[pi]["properties"]["status"] == "proposed" else "s58f"] += 1
    return counts


def main():
    from shapely import make_valid
    from shapely.geometry import mapping, shape
    feats = boundaries()
    # server-side generalisation can leave self-touching rings, which break the point-in-polygon tests
    geoms = [g if g.is_valid else make_valid(g) for g in (shape(f["geometry"]) for f in feats)]
    counts = dig_counts(geoms)
    out = [{"type": "Feature", "geometry": mapping(g),
            "properties": {"code": f["properties"]["CTYUA25CD"], "name": f["properties"]["CTYUA25NM"], **c}}
           for f, g, c in zip(feats, geoms, counts)]
    out.sort(key=lambda f: f["properties"]["name"])
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": out}, separators=(",", ":")))
    print(f"{len(out)} authorities, {sum(c['streets'] for c in counts):,} dig-once streets assigned, "
          f"{OUT.stat().st_size / 1e6:.1f} MB -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
