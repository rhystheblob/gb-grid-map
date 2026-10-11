"""GB gas distribution mains, for dig-once planning alongside the electricity layers.

The gas networks publish low/medium-pressure pipes in the UK Gas Network Data Interoperability Standard (pipe
assets). The full networks run to millions of pipe sections, more than a static site can hold, so the map shows
the mains most likely to be dug up soon: metallic mains (cast, spun and ductile iron, steel), which the iron
mains replacement programme is replacing, plus the pipes each network has scheduled for replacement.

    Cadent  gas-pipe-infrastructure-gpi_open (metallic mains export) + MRP_Pipe_Infrastructure_<LA>_Open ids
            (Mains Replacement Programme), network areas. Login portal; exported via the owner's browser.
    NGN     Live Distribution Mains GeoPackage per zone (login portal; downloaded by the owner), no programme.

Inputs in data/raw/portals/ (git-ignored). Outputs:

    data/gb/gas/grid.json                {cell: [km metallic main, km in replacement programme, GDN]}
    data/gb/gas/<GDN>/<cell>.geojson     simplified pipes per ~10 km cell (same cells as the secondaries)
    data/gb/gas/areas.geojson            network areas
    data/gb/gas/index.json               per-GDN counts, cells, source

Usage:  python3 build_gb_gas.py
"""
import json
import math
import pathlib
import re
import shutil

from build_gb_secondary import CELL_LAT, CELL_LON, cell_key
from dnos.common import ROOT

RAW = ROOT / "data" / "raw" / "portals"
OUT = ROOT / "data" / "gb" / "gas"
SIMPLIFY_DEG = 0.00003   # ~3 m: pipes follow streets, keep them recognisable at street zoom
MATERIALS = {"CI": "cast iron", "SI": "spun iron", "DI": "ductile iron", "ST": "steel", "PE": "polyethylene",
             "AS": "asbestos cement", "PV": "PVC"}


def year(value):
    m = re.search(r"(1[89]\d\d|20\d\d)", str(value or ""))
    return int(m.group(1)) if m else None


def diameter_mm(value, unit):
    try:
        d = float(value)
    except (TypeError, ValueError):
        return None
    return round(d * 25.4) if str(unit).upper().startswith("I") else round(d)


def length_km(coords):
    """Approximate length of a WGS84 polyline in km (equirectangular; fine at street scale)."""
    km = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        kx = 111.32 * math.cos(math.radians((y1 + y2) / 2))
        km += math.hypot((x2 - x1) * kx, (y2 - y1) * 110.57)
    return km


def lines_of(geometry):
    if not geometry:
        return []
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    return []


def cadent():
    mrp = json.loads((RAW / "cadent_mrp.json").read_text())
    scheduled = mrp["ids"]  # asset id -> local authority of the replacement project
    fc = json.loads((RAW / "cadent_metallic_mains.geojson").read_text())
    out = []
    for f in fc["features"]:
        p = f["properties"]
        out.append({
            "geometry": f["geometry"],
            "props": {"gdn": "Cadent", "id": p.get("asset_id"), "mat": p.get("material"),
                      "pr": p.get("pressure"), "dia": diameter_mm(p.get("diameter"), p.get("diam_unit")),
                      "yr": year(p.get("inst_date")), "mrp": scheduled.get(p.get("asset_id"))},
        })
    areas = mrp.get("areas", {"features": []})["features"]
    return out, [{"type": "Feature", "properties": {"gdn": "Cadent", "name": a["properties"].get("assetid")},
                  "geometry": a["geometry"]} for a in areas]


METALLIC = {"CI", "SI", "DI", "ST"}


def gpkg_lines(path, table):
    """Rows of a GeoPackage line table (British National Grid) as (properties, WGS84 GeoJSON geometry).
    GeoPackage geometry = 'GP' header (+ optional envelope) followed by standard WKB."""
    import sqlite3
    from pyproj import Transformer
    from shapely import wkb
    from shapely.geometry import mapping
    from shapely.ops import transform
    to_wgs84 = Transformer.from_crs(27700, 4326, always_xy=True).transform
    con = sqlite3.connect(path)
    cur = con.execute(f'SELECT * FROM "{table}"')
    cols = [d[0] for d in cur.description]
    geom_col = next(c for c in cols if c.lower() in ("shape", "geom", "geometry"))
    for row in cur:
        rec = dict(zip(cols, row))
        blob = rec.pop(geom_col)
        if not blob:
            continue
        env = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(blob[3] >> 1) & 0x07]
        g = transform(to_wgs84, wkb.loads(bytes(blob[8 + env:])))
        yield rec, mapping(g)
    con.close()


def ngn():
    """Northern Gas Networks: Live Distribution Mains per zone (GeoPackage zips, CC BY 4.0, login portal), placed in
    data/raw/portals/ngn/. Same interoperability schema as Cadent. NGN publishes no replacement programme."""
    import glob
    import zipfile
    zips = sorted(glob.glob(str(RAW / "ngn" / "*.zip")))
    if not zips:
        raise FileNotFoundError(str(RAW / "ngn" / "*.zip"))
    out, zones = [], []
    for z in zips:
        target = pathlib.Path(z).with_suffix("")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(target)
        for g in target.glob("*.gpkg"):
            import sqlite3
            table = sqlite3.connect(g).execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'").fetchone()[0]
            zones.append(table)
            for rec, geom in gpkg_lines(g, table):
                if rec.get("TYPE") != "Main Pipe" or rec.get("MATERIAL") not in METALLIC:
                    continue
                out.append({"geometry": geom,
                            "props": {"gdn": "NGN", "id": rec.get("ASSET_ID"), "mat": rec.get("MATERIAL"),
                                      "pr": rec.get("PRESSURE"), "dia": diameter_mm(rec.get("DIAMETER"), rec.get("DIAM_UNIT")),
                                      "yr": year(rec.get("INST_DATE")), "mrp": None}})
    print(f"  NGN zones: {', '.join(zones)}")
    return out, []


READERS = {"Cadent": cadent, "NGN": ngn}


def main():
    from shapely.geometry import LineString, mapping

    OUT.mkdir(parents=True, exist_ok=True)
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    grid = json.loads((OUT / "grid.json").read_text()) if (OUT / "grid.json").exists() else {}
    all_areas = []
    for gdn, reader in READERS.items():
        try:
            pipes, areas = reader()
        except FileNotFoundError as e:
            print(f"{gdn}: input missing ({e.filename}) - keeping previous data")
            continue
        grid = {k: v for k, v in grid.items() if v[2] != gdn}
        shutil.rmtree(OUT / gdn, ignore_errors=True)
        (OUT / gdn).mkdir(parents=True)
        cells, km_total, km_mrp = {}, 0.0, 0.0
        for pipe in pipes:
            for coords in lines_of(pipe["geometry"]):
                if len(coords) < 2:
                    continue
                km = length_km(coords)
                mid = coords[len(coords) // 2]
                key = cell_key(mid[0], mid[1])
                g = LineString(coords).simplify(SIMPLIFY_DEG, preserve_topology=False)
                geom = json.loads(json.dumps(mapping(g)), parse_float=lambda x: round(float(x), 5))
                props = {k: v for k, v in pipe["props"].items() if v is not None}
                props["km"] = round(km, 3)
                cells.setdefault(key, []).append({"type": "Feature", "properties": props, "geometry": geom})
                cell = grid.setdefault(key, [0.0, 0.0, gdn])
                cell[0] += km
                km_total += km
                if props.get("mrp"):
                    cell[1] += km
                    km_mrp += km
        for key, feats in cells.items():
            (OUT / gdn / f"{key}.geojson").write_text(
                json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
        all_areas += areas
        index[gdn] = {"cells": sorted(cells), "pipes": sum(len(v) for v in cells.values()),
                      "km_metallic": round(km_total), "km_replacement": round(km_mrp)}
        print(f"{gdn}: {index[gdn]['pipes']} metallic main sections, {index[gdn]['km_metallic']} km, "
              f"{index[gdn]['km_replacement']} km in the replacement programme, {len(cells)} cells")
    (OUT / "grid.json").write_text(json.dumps({k: [round(v[0], 1), round(v[1], 1), v[2]] for k, v in grid.items()},
                                              separators=(",", ":")))
    if all_areas:
        from shapely.geometry import shape
        for a in all_areas:  # network areas are only an outline overlay: ~100 m detail is plenty
            a["geometry"] = json.loads(json.dumps(mapping(shape(a["geometry"]).simplify(0.001, preserve_topology=True))),
                                       parse_float=lambda x: round(float(x), 4))
        (OUT / "areas.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": all_areas},
                                                      separators=(",", ":")))
    index_path.write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
