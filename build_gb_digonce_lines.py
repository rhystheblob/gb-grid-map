"""Road lines for the dig-once layer: each street drawn along the road, with the section being dug highlighted.

Street geometry: OS Open USRN (Ordnance Survey, open data, monthly GeoPackage of every street's line keyed by USRN,
the same street id Street Manager uses). Download into data/raw/usrn/ (git-ignored):
    https://api.os.uk/downloads/v1/products/OpenUSRN/downloads?area=GB&format=GeoPackage&redirect
Contains OS data © Crown Copyright and database rights 2026.

Dug sections: the stretches of the street covered by its works in the last 12 months and planned works (each
permit's location centre from data/raw/streetworks/works.json, snapped onto the street line). Works within 100 m of
each other along the line form one section, padded 15 m either side, so a long road dug in a few places shows a few
short sections. Works more than 100 m off the line (mis-keyed USRNs) are ignored.

Run after build_gb_digonce.py (it reads the street rows from data/gb/digonce/cells/).
Output:  data/gb/digonce/lines/<cell>.json  per ~10 km cell: [lon, lat, usrn, street lines, dug sections]
         (lon, lat is the street's dot, so the map can swap the dot for the line)
Usage:   python3 build_gb_digonce_lines.py
"""
import glob
import json
import shutil
import sqlite3
import struct

from dnos.common import ROOT

DIG = ROOT / "data" / "gb" / "digonce"
GPKG = sorted(glob.glob(str(ROOT / "data" / "raw" / "usrn" / "osopenusrn_*.gpkg")))
WORKS = ROOT / "data" / "raw" / "streetworks" / "works.json"
SNAP_M, PAD_M, GAP_M, SIMPLIFY_M = 100, 15, 100, 2


def gpkg_wkb(blob):
    """GeoPackage geometry blob -> WKB (strip the GP header and its envelope)."""
    flags = blob[3]
    env = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
    return blob[8 + env:]


def street_lines(usrns):
    """{usrn: merged BNG line geometry} for the wanted streets."""
    from shapely import from_wkb
    from shapely.ops import linemerge, unary_union
    parts = {}
    con = sqlite3.connect(GPKG[-1])
    for usrn, blob in con.execute("SELECT usrn, geometry FROM openUSRN"):
        if usrn in usrns and blob:
            parts.setdefault(usrn, []).append(from_wkb(gpkg_wkb(blob)))
    out = {}
    for usrn, geoms in parts.items():
        g = unary_union(geoms)
        if g.geom_type == "MultiLineString":
            g = linemerge(g)
        if g.geom_type in ("LineString", "MultiLineString"):
            out[usrn] = g
    return out


def job_points(usrns, as_of):
    """{usrn: [(x, y), ...]} BNG centres of each street's works in the 12 months to as_of, and planned ones."""
    import datetime
    year_ago = (datetime.date.fromisoformat(as_of) - datetime.timedelta(days=365)).isoformat()
    pts = {}
    for w in json.loads(WORKS.read_text())["works"].values():
        if w["dead"] or not w["d"] or w["d"] < year_ago:
            continue
        u = int(w["u"]) if str(w["u"] or "").isdigit() else None
        if u in usrns:
            pts.setdefault(u, []).append((w["x"], w["y"]))
    return pts


def sections(line, points):
    """Stretches of the line covered by the points (per connected part): runs with gaps under GAP_M, padded."""
    from shapely.geometry import Point
    from shapely.ops import substring
    out = []
    for part in getattr(line, "geoms", [line]):
        d = sorted(part.project(Point(p)) for p in points if part.distance(Point(p)) <= SNAP_M)
        runs = []
        for v in d:
            if runs and v - runs[-1][1] <= GAP_M:
                runs[-1][1] = v
            else:
                runs.append([v, v])
        out += [substring(part, max(0, a - PAD_M), min(part.length, b + PAD_M)) for a, b in runs]
    return out


def main():
    from pyproj import Transformer
    from shapely.ops import transform
    if not GPKG:
        raise SystemExit("OS Open USRN GeoPackage not found in data/raw/usrn/ (see the docstring)")
    as_of = json.loads((DIG / "streets.json").read_text())["as_of"]
    cells = {}
    for path in glob.glob(str(DIG / "cells" / "*.json")):
        key = path.rsplit("/", 1)[1][:-5]
        cells[key] = [(r[0], r[1], int(r[9])) for r in json.load(open(path)) if str(r[9] or "").isdigit()]
    usrns = {u for rows in cells.values() for _, _, u in rows}
    lines = street_lines(usrns)
    jobs = job_points(usrns, as_of)
    to_wgs = Transformer.from_crs(27700, 4326, always_xy=True).transform
    coords = lambda g: [[[round(c[0], 5), round(c[1], 5)] for c in part.coords]  # OS lines carry a z value
                        for part in getattr(transform(to_wgs, g), "geoms", [transform(to_wgs, g)])]
    shutil.rmtree(DIG / "lines", ignore_errors=True)
    (DIG / "lines").mkdir()
    n_line = n_sec = 0
    for key, rows in cells.items():
        out = []
        for lon, lat, usrn in rows:
            g = lines.get(usrn)
            if g is None:
                continue
            secs = sections(g, jobs.get(usrn, []))
            out.append([lon, lat, usrn, coords(g.simplify(SIMPLIFY_M)),
                        [c for s in secs if s.length > 0 for c in coords(s.simplify(SIMPLIFY_M))]])
            n_line += 1
            n_sec += bool(out[-1][4])
        if out:
            (DIG / "lines" / f"{key}.json").write_text(json.dumps(out, separators=(",", ":")))
    size = sum(p.stat().st_size for p in (DIG / "lines").glob("*.json"))
    print(f"{n_line:,} of {len(usrns):,} dig-once streets matched to OS Open USRN lines, {n_sec:,} with a dug section; "
          f"{size / 1e6:.0f} MB in {len(list((DIG / 'lines').glob('*.json')))} cells")


if __name__ == "__main__":
    main()
