"""GB secondary (11kV/LV distribution) substations for every DNO, in one common shape.

Each operator publishes something different, so the common measure is utilisation = peak demand / transformer
rating (%), with the raw figures alongside:

    SSEN  supply areas; no ratings published, so no utilisation (smart meter peak kW instead)
    UKPN  supply areas (2024) + site list (2026): rating, utilisation band, demand headroom band, customers
    ENWL  transformer points: rating, load, utilisation, demand headroom (kVA)
    NPg   site list (open): rating, maximum demand (redacted for small sites), customers; + supply areas if exported
    NGED  site list: rating, day/night maximum demand, customers, LCT and generation counts
    SPEN  monitored transformers only (LV monitoring): maximum measured utilisation

Inputs are files in data/raw/portals/ (login-only portals are exported through the owner's signed-in browser; see
README). Outputs, sized for a static site:

    data/gb/sec/points_<DNO>.json      [[lon, lat, util%|-1, smart-meter peak kW|-1], ...]  zoomed-out view
    data/gb/sec/<DNO>/<cell>.geojson   full detail (supply area or point + properties) per ~10 km grid cell
    data/gb/sec/index.json             {DNO: {"cells": [...], "count": n, "with_util": n, "source": ...}}

Usage:  python3 build_gb_secondary.py [DNO ...]
"""
import csv
import json
import math
import re
import shutil
import sys

from dnos.common import ROOT

RAW = ROOT / "data" / "raw" / "portals"
OUT = ROOT / "data" / "gb" / "sec"
CELL_LON, CELL_LAT = 0.15, 0.1          # ~10 km cells; the map loads the cells in view from zoom 13
SIMPLIFY_DEG = 0.00008                  # as for the SSEN secondary areas
# Domestic aggregates peak around 1 kW per meter (99.9% under 7.5). A substation averaging more than this per meter
# at its peak half-hour is treated as a bad reading (faulty meter or non-domestic load skewing a small group).
MAX_PEAK_KW_PER_METER = 10


def plausible_peak(kw, meters):
    return kw is not None and meters and kw / meters <= MAX_PEAK_KW_PER_METER


def cell_key(lon, lat):
    return f"{math.floor(lon / CELL_LON)}_{math.floor(lat / CELL_LAT)}"


def f(value):
    """Number from the many formats used ('250 kVA', '123.9', 'Redacted', None)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if value != value else float(value)
    m = re.search(r"-?\d+(\.\d+)?", str(value).replace(",", ""))
    return float(m.group()) if m else None


def band_mid(band):
    """'40-60%' -> 50, '120% +' -> 130 (UKPN publishes utilisation as bands)."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", band or "")]
    if not nums:
        return None
    return sum(nums[:2]) / len(nums[:2]) if len(nums) >= 2 else nums[0] + 10


def util(md, rating):
    return round(100 * md / rating, 1) if md is not None and rating else None


def rec(dno, sid, name, lon, lat, geometry=None, **props):
    """Common secondary record. geometry: supply-area polygon if published, else the site point is used."""
    lon, lat = f(lon), f(lat)
    if lon is None or lat is None or not (-9 < lon < 3 and 49 < lat < 61.5):
        return None
    p = {"dno": dno, "id": str(sid), "name": name or None}
    p.update({k: (round(v, 1) if isinstance(v, float) else v) for k, v in props.items() if v not in (None, "")})
    geom = geometry or {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]}
    return {"type": "Feature", "properties": p, "geometry": geom, "_pt": (lon, lat)}


def simplified(geometry):
    from shapely.geometry import mapping, shape
    g = shape(geometry).simplify(SIMPLIFY_DEG, preserve_topology=True)
    if g.is_empty:
        return None
    return json.loads(json.dumps(mapping(g)), parse_float=lambda x: round(float(x), 5))


_OSGB = None


def osgb_ref(ref):
    """OS grid reference ('SK905710631540') -> (lon, lat). NGED gives only this for ~55% of its substations."""
    global _OSGB
    m = re.fullmatch(r"([HJNOST])([A-HJ-Z])(\d+)", (ref or "").strip().upper())
    if not m or len(m.group(3)) % 2:
        return None, None
    l1, l2 = (ord(c) - ord("A") - (c > "I") for c in m.group(1, 2))  # grid letters skip I
    e100 = ((l1 - 2) % 5) * 5 + (l2 % 5)
    n100 = (19 - (l1 // 5) * 5) - (l2 // 5)
    digits = m.group(3)
    half = len(digits) // 2
    east = e100 * 100000 + int(digits[:half]) * 10 ** (5 - half)
    north = n100 * 100000 + int(digits[half:]) * 10 ** (5 - half)
    if _OSGB is None:
        from pyproj import Transformer
        _OSGB = Transformer.from_crs(27700, 4326, always_xy=True)
    return _OSGB.transform(east, north)


def rep_point(geometry):
    from shapely.geometry import shape
    pt = shape(geometry).representative_point()
    return pt.x, pt.y


# ---------------------------------------------------------------- per-DNO readers

def ssen():
    smart = json.loads((ROOT / "data" / "smart_meter_secondary.json").read_text())["secondaries"]
    out = []
    for path in (ROOT / "data" / "secondary").glob("*.geojson"):
        for feat in json.loads(path.read_text())["features"]:
            p = feat["properties"]
            lon, lat = rep_point(feat["geometry"])
            sm = smart.get(p["NRN_10_DIGIT"], {})
            if not plausible_peak(sm.get("peak_kw"), sm.get("meters")):
                sm = {"name": sm.get("name")}
            out.append(rec("SSEN", p["NRN_10_DIGIT"], sm.get("name"), lon, lat, feat["geometry"],
                           primary=p.get("PRIMARY_NAME_2025"), peak_kw=sm.get("peak_kw"), meters=sm.get("meters")))
    return out


def ukpn():
    sites = json.loads((RAW / "ukpn-secondary-sites.json").read_text())
    by_floc = {}
    for s in sites:
        by_floc[s["functionallocation"]] = s
    areas = {}
    area_path = RAW / "ukpn_secondary_postcode_area.geojson"
    if area_path.exists():
        for feat in json.loads(area_path.read_text())["features"]:
            p = feat["properties"]
            if feat.get("geometry"):
                areas[p.get("sitefunctionallocation") or p.get("sitefunctionallocationclean")] = (feat["geometry"], p)
    out = []
    for floc, s in by_floc.items():
        geom, ap = areas.pop(floc, (None, {}))
        out.append(rec("UKPN", floc, s.get("substationalias"), s.get("longitude"), s.get("latitude"),
                       simplified(geom) if geom else None,
                       util=band_mid(s.get("utilisation_band")), uband=s.get("utilisation_band"),
                       rating=f(s.get("onanrating")), headroom=s.get("demand_headroom"),
                       cust=s.get("customer_count"), primary=s.get("primaryfeeder"),
                       reinf=ap.get("predicted_year_of_reinforcement")))
    for floc, (geom, p) in areas.items():  # areas with no 2026 site record: keep the 2024 figures
        lon, lat = rep_point(geom)
        out.append(rec("UKPN", floc, None, lon, lat, simplified(geom),
                       util=band_mid(p.get("utilisation_band")), uband=p.get("utilisation_band"),
                       rating=f(p.get("onanrating")), headroom=p.get("demand_headroom"),
                       cust=p.get("customer_count"), reinf=p.get("predicted_year_of_reinforcement")))
    return out


def enwl():
    out = []
    for feat in json.loads((RAW / "enwl-capacity-distribution-tx.geojson").read_text())["features"]:
        p, g = feat["properties"], feat.get("geometry")
        if not g:
            continue
        lon, lat = (p["geo_point_2d"]["lon"], p["geo_point_2d"]["lat"]) if p.get("geo_point_2d") else rep_point(g)
        rating, load = f(p.get("distribution_tx_rating_kva")), f(p.get("distribution_tx_load_kva"))
        u = f(p.get("distribution_tx_utilisation"))
        out.append(rec("ENWL", p.get("distribution_tx_number") or p.get("distribution_number"),
                       f"Substation {p.get('distribution_number')}", lon, lat,
                       simplified(g) if g["type"] != "Point" else None,
                       util=u if u is not None else util(load, rating), rating=rating, md=load,
                       headroom=f(p.get("distribution_tx_demand_headroom_kva")),
                       gen_headroom=f(p.get("distribution_tx_generation_headroom_kva")),
                       primary=p.get("primary_number"), uband=p.get("distribution_tx_utilisation_category")))
    return out


def npg():
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree

    sites = [x for x in json.loads((RAW / "npg_substation_sites_list.json").read_text())
             if x.get("site_level") == "Distribution" and x.get("latlong")]
    pts = [Point(x["latlong"]["lon"], x["latlong"]["lat"]) for x in sites]
    area_of = {}
    area_path = RAW / "npg-distribution-substation-service-areas.geojson"
    if area_path.exists():
        # the area file shares no id with the site list: match each area to the site point inside it, preferring
        # the one whose name matches the area's 'feederss' (which is the substation name)
        tree = STRtree(pts)
        for feat in json.loads(area_path.read_text())["features"]:
            g = feat.get("geometry")
            if not g:
                continue
            poly = shape(g)
            inside = [i for i in tree.query(poly) if poly.contains(pts[i])]
            name = (feat["properties"].get("feederss") or "").strip().upper()
            named = [i for i in inside if (sites[i].get("site_name") or "").strip().upper() == name]
            pick = named[0] if named else inside[0] if len(inside) == 1 else None
            if pick is not None and pick not in area_of:
                area_of[pick] = (g, str(feat["properties"].get("source_site_spid") or ""))
    out = []
    for i, s in enumerate(sites):
        rating, md = f(s.get("transformer_rating_kva")), f(s.get("maximum_demand"))
        geom, spid = area_of.get(i, (None, None))
        out.append(rec("NPg", s.get("substation_id"), s.get("site_name"), s["latlong"]["lon"], s["latlong"]["lat"],
                       simplified(geom) if geom else None, spid=spid,
                       util=util(md, rating), rating=rating, md=md, cust=s.get("customer_numbers"),
                       primary=s.get("upstream_substation"), kind=s.get("site_type")))
    return out


def nged():
    out = []
    with open(RAW / "nged-distribution-substations.csv", newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            rating = f(r.get("Substation Rating"))
            md = max([v for v in (f(r.get("Day Max Demand")), f(r.get("Night Max Demand"))) if v is not None], default=None)
            lon, lat = r.get("Longitude"), r.get("Latitude")
            if not lon or not lat:
                lon, lat = osgb_ref(r.get("Grid Reference"))
            out.append(rec("NGED", r.get("Substation Number"), r.get("Substation Name"), lon, lat,
                           util=util(md, rating), rating=rating, md=md, cust=f(r.get("Customers")),
                           primary=r.get("Primary Name"), lct=f(r.get("LCT Count Total")),
                           gen_kw=f(r.get("Total Generation Capacity")), kind=r.get("Substation Type"),
                           note="Peak demand is NGED's published maximum (larger of day and night); method not stated"))
    return out


def spen():
    rows = json.loads((RAW / "spen-lv_monitor.json").read_text())
    best = {}
    for r in rows:  # maximum measured utilisation over all published months, per monitored transformer
        u = f(r.get("maximum_capacity_utilisation"))
        tid = r.get("transformer_enid")
        if u is None or not tid or not r.get("geo_points"):
            continue
        if tid not in best or u > best[tid][0]:
            best[tid] = (u, r)
    out = []
    for tid, (u, r) in best.items():
        g = r["geo_points"]
        out.append(rec("SPEN", tid, r.get("district"), g.get("lon"), g.get("lat"), util=u,
                       month=str(r.get("year_month"))[:7], note="monitored transformers only"))
    return out


def attach_smart(dno, records):
    """Join peaks from smart_meter_gb.py (data/raw/smart/<DNO>.json) by substation id (or NPg SPID); peaks that
    carry coordinates but match no id go to the nearest secondary within ~50 m."""
    path = ROOT / "data" / "raw" / "smart" / f"{dno}.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    days = " and ".join(data.get("days", []))
    peaks = {k: v for k, v in data["peaks"].items() if plausible_peak(v[0], v[1])}
    if len(peaks) < len(data["peaks"]):
        print(f"  smart meter: dropped {len(data['peaks']) - len(peaks)} implausible peaks (> {MAX_PEAK_KW_PER_METER} kW/meter)")
    matched = set()
    for r in records:
        if not r:
            continue
        p = r["properties"]
        key = p["id"] if p["id"] in peaks else p.get("spid") if p.get("spid") in peaks else None
        if key:
            pk = peaks[key]
            p.update(peak_kw=pk[0], meters=pk[1], peak_at=pk[2], peak_days=days)
            matched.add(key)
    grid = {}
    for r in records:
        if r and "peak_kw" not in r["properties"]:
            lon, lat = r["_pt"]
            grid.setdefault((round(lon, 3), round(lat, 3)), []).append(r)
    by_location = 0
    for key, pk in peaks.items():
        if key in matched or len(pk) < 5 or not pk[3] or not pk[4]:
            continue
        lon, lat = float(pk[3]), float(pk[4])
        near = [c for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                for c in grid.get((round(lon, 3) + dx / 1000, round(lat, 3) + dy / 1000), [])]
        best = min(near, key=lambda c: (c["_pt"][0] - lon) ** 2 + (c["_pt"][1] - lat) ** 2, default=None)
        if best and ((best["_pt"][0] - lon) * 0.6) ** 2 + (best["_pt"][1] - lat) ** 2 < 0.00045 ** 2:
            best["properties"].update(peak_kw=pk[0], meters=pk[1], peak_at=pk[2], peak_days=days)
            by_location += 1
    print(f"  smart meter: {len(matched)} by id, {by_location} by location, of {len(peaks)} published")


READERS = {"SSEN": ssen, "UKPN": ukpn, "ENWL": enwl, "NPg": npg, "NGED": nged, "SPEN": spen}


def write(dno, records):
    records = [r for r in records if r and r["geometry"]]
    shutil.rmtree(OUT / dno, ignore_errors=True)
    (OUT / dno).mkdir(parents=True)
    cells, points = {}, []
    for r in records:
        lon, lat = r.pop("_pt")
        u, pk = r["properties"].get("util"), r["properties"].get("peak_kw")
        points.append([round(lon, 4), round(lat, 4), round(u) if u is not None else -1,
                       round(pk) if pk is not None else -1])
        cells.setdefault(cell_key(lon, lat), []).append(r)
    for key, feats in cells.items():
        (OUT / dno / f"{key}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    (OUT / f"points_{dno}.json").write_text(json.dumps(points, separators=(",", ":")))
    with_util = sum(1 for p in points if p[2] >= 0)
    polys = sum(1 for r in records if r["geometry"]["type"] != "Point")
    print(f"{dno}: {len(records)} secondaries, {with_util} with utilisation, {polys} supply areas, {len(cells)} cells")
    smart = sum(1 for p in points if p[3] >= 0)
    return {"cells": sorted(cells), "count": len(records), "with_util": with_util, "areas": polys, "smart": smart}


def main():
    index_path = OUT / "index.json"
    OUT.mkdir(parents=True, exist_ok=True)
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    wanted = sys.argv[1:] or list(READERS)
    for dno in wanted:
        dno = next(k for k in READERS if k.lower() == dno.lower())
        try:
            records = READERS[dno]()
        except FileNotFoundError as e:
            print(f"{dno}: input missing ({e.filename}) - keeping previous data")
            continue
        attach_smart(dno, records)
        index[dno] = write(dno, records)
    index_path.write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
