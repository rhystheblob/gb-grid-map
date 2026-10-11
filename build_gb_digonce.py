"""Dig-once layer: where street works from different utilities could be coordinated.

Source: DfT Street Manager open data archive (England, OGL), opendata.manage-roadworks.service.gov.uk:
    section_58/YYYY/MM.zip  one JSON per Section 58 event. A Section 58 restriction bars new street works on a road
                            for 6 months-3 years after substantial road works (e.g. resurfacing). "proposed" notices
                            are the dig-once window: utilities should get their works in before it starts.
    permit/YYYY/MM.zip      one JSON per permit event, from every utility (electricity, gas, water, telecoms) and
                            highway authority: where and when each street is dug up.

Coordinates are British National Grid WKT (POINT / LINESTRING / POLYGON).

Outputs: data/gb/digonce/s58.geojson     Section 58 restrictions proposed (last 12 months) or in force today
         data/gb/digonce/streets.json    overview: [lon, lat, score, flags] per street with a coordination signal
         data/gb/digonce/cells/<cell>.json  full rows per ~10 km cell: works by sector over the last 12 months,
                                         upcoming works, proposed restriction, nearby signals from the other layers
Usage:   python3 build_gb_digonce.py [--fetch-permits]   (fetch: last 12 monthly permit archives, ~1 GB each,
                                                          summarised to data/raw/streetworks/works.json then deleted)
"""
import datetime
import glob
import json

from dnos.common import ROOT

RAW = ROOT / "data" / "raw" / "streetworks"
OUT = ROOT / "data" / "gb" / "digonce"
_TO_WGS84 = None


def wkt_to_geojson(wkt):
    """BNG WKT -> simplified WGS84 GeoJSON geometry."""
    global _TO_WGS84
    from shapely import wkt as shapely_wkt
    from shapely.geometry import mapping
    from shapely.ops import transform
    if _TO_WGS84 is None:
        from pyproj import Transformer
        _TO_WGS84 = Transformer.from_crs(27700, 4326, always_xy=True).transform
    try:
        g = transform(_TO_WGS84, shapely_wkt.loads(wkt)).simplify(0.00002, preserve_topology=True)
    except Exception:
        return None
    if g.is_empty:
        return None
    return json.loads(json.dumps(mapping(g)), parse_float=lambda x: round(float(x), 5))


def zip_entries(path):
    """(name, bytes) for every entry, read sequentially from local file headers. Many archive files are published
    with a truncated central directory (the index ordinary unzip tools need), but every entry is intact."""
    import struct
    import zlib
    with open(path, "rb") as fh:
        data = fh.read()
    pos = 0
    while True:
        pos = data.find(b"PK\x03\x04", pos)
        if pos < 0:
            return
        flags, method = struct.unpack_from("<HH", data, pos + 6)
        csize, = struct.unpack_from("<I", data, pos + 18)
        nlen, xlen = struct.unpack_from("<HH", data, pos + 26)
        name = data[pos + 30:pos + 30 + nlen].decode("utf-8", "replace")
        start = pos + 30 + nlen + xlen
        if method == 8 and csize and not flags & 8:
            body, end = zlib.decompress(data[start:start + csize], -15), start + csize
        elif method == 8:  # size only in a trailing data descriptor: inflate in chunks until the stream ends
            d, view, at, parts = zlib.decompressobj(-15), memoryview(data), start, []
            while not d.eof and at < len(data):
                chunk = view[at:at + 65536]  # shorter at the end of the file: advance by what was actually fed
                parts.append(d.decompress(chunk))
                at += len(chunk)
            body, end = b"".join(parts), at - len(d.unused_data)
        else:
            body, end = data[start:start + csize], start + csize
        yield name, body
        pos = max(end, pos + 4)


YEAR_AGO = lambda today: (datetime.date.fromisoformat(today) - datetime.timedelta(days=365)).isoformat()
FIVE_YEARS = lambda today: (datetime.date.fromisoformat(today) + datetime.timedelta(days=5 * 365)).isoformat()
ARCHIVE = "https://opendata.manage-roadworks.service.gov.uk"
UTILITIES = ("electricity", "gas", "water", "telecoms")
DEAD = {"cancelled", "refused", "revoked"}


def months_back(n, today):
    d = datetime.date.fromisoformat(today).replace(day=1)
    out = []
    for _ in range(n):
        d = (d - datetime.timedelta(days=1)).replace(day=1)
        out.append(f"{d.year}/{d.month:02d}")
    return sorted(out)


def fetch_permits(today):
    """Summarise each work (latest state) from the last 12 monthly permit archives; raw zips are deleted after use."""
    import re
    import sys
    import urllib.request
    from dnos.sectors import SECTORS, sector
    num = re.compile(r"-?\d+(?:\.\d+)?")
    works, promoters = {}, []
    for ym in months_back(12, today):
        path = RAW / "permit" / (ym.replace("/", "-") + ".zip")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(f"{ARCHIVE}/permit/{ym}.zip", headers={"User-Agent": "gb-grid-map/1.0"})
            with urllib.request.urlopen(req, timeout=3600) as r, open(path, "wb") as fh:
                while chunk := r.read(1 << 22):
                    fh.write(chunk)
        n = 0
        for name, body in zip_entries(path):
            d = json.loads(body)
            o = d.get("object_data") or {}
            ref = o.get("work_reference_number")
            if not ref:
                continue
            n += 1
            w = works.get(ref)
            if w is None or d["event_time"] >= w["t"]:
                xs = [float(v) for v in num.findall(o.get("works_location_coordinates") or "")]
                if len(xs) < 2:
                    continue
                org = o.get("promoter_organisation") or ""
                if org not in promoters:
                    promoters.append(org)
                works[ref] = {
                    "t": d["event_time"], "s": SECTORS.index(sector(org)), "p": promoters.index(org),
                    "u": o.get("usrn"), "x": round(sum(xs[0::2]) / len(xs[0::2])), "y": round(sum(xs[1::2]) / len(xs[1::2])),
                    "d": ((o.get("actual_start_date_time") or o.get("proposed_start_date") or "")[:10]),
                    "c": o.get("collaborative_working") == "Yes",
                    "dead": (o.get("permit_status") or "") in DEAD,
                    # interned: millions of works share a few hundred thousand street/town/authority names
                    "st": sys.intern(o.get("street_name") or ""), "tw": sys.intern(o.get("town") or o.get("area_name") or ""),
                    "ha": sys.intern(o.get("highway_authority") or ""),
                }
        print(f"  permits {ym}: {n} events, {len(works)} works so far", flush=True)
        path.unlink()
    (RAW / "works.json").write_text(json.dumps({"promoters": promoters, "works": works}))


def streets(today, s58):
    """Per street: who dug it in the last year, who plans to, and whether a resurfacing restriction is coming."""
    from pyproj import Transformer
    from dnos.sectors import SECTORS
    to_wgs84 = Transformer.from_crs(27700, 4326, always_xy=True)
    data = json.loads((RAW / "works.json").read_text())
    year_ago = YEAR_AGO(today)
    agg = {}
    for w in data["works"].values():
        if w["dead"] or not w["u"] or not w["d"]:
            continue
        a = agg.setdefault(w["u"], {"past": [0] * len(SECTORS), "next": [0] * len(SECTORS), "prom": set(),
                                   "collab": 0, "x": [], "y": [], "first_next": "", "st": w["st"], "tw": w["tw"],
                                   "ha": w["ha"]})
        if w["d"] >= today:
            a["next"][w["s"]] += 1
            a["first_next"] = min(a["first_next"] or w["d"], w["d"])
        elif w["d"] >= year_ago:
            a["past"][w["s"]] += 1
        else:
            continue
        a["prom"].add(w["p"])
        a["collab"] += w["c"]
        a["x"].append(w["x"]); a["y"].append(w["y"])
    proposed = {}
    for f in s58:
        p = f["properties"]
        if p["status"] == "proposed" and p.get("usrn"):
            proposed[p["usrn"]] = min(proposed.get(p["usrn"], "9999"), p["start"])
    util = [SECTORS.index(u) for u in UTILITIES]
    rows = []
    for usrn, a in agg.items():
        past_u = sum(1 for i in util if a["past"][i])
        next_u = sum(1 for i in util if a["next"][i])
        restriction = proposed.get(usrn)
        # a coordination signal: several utilities digging, utility works planned, or a restriction coming
        if past_u < 2 and not next_u and not (restriction and past_u + next_u):
            continue
        score = past_u + 2 * next_u + (3 if restriction else 0)
        lon, lat = to_wgs84.transform(sum(a["x"]) / len(a["x"]), sum(a["y"]) / len(a["y"]))
        rows.append([round(lon, 5), round(lat, 5), score, a["past"], a["next"], len(a["prom"]), a["collab"],
                     a["first_next"] or None, restriction, usrn, a["st"], a["tw"], a["ha"]])
    enrich(rows)
    print(f"streets: {len(agg)} with works in the last year or planned; {len(rows)} with a dig-once signal "
          f"({sum(1 for r in rows if r[8])} with a resurfacing restriction proposed)")
    return {"as_of": today, "sectors": list(SECTORS), "promoters": len(data["promoters"]), "streets": rows}


def enrich(rows):
    """Link each street to the other utility layers on the map (all already built under data/gb/):
    gas metallic mains nearby (and whether in a replacement programme), storm overflow works planned by 2030,
    overloaded secondary substations, and missing gigabit / Project Gigabit build. Each adds to the score."""
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree
    gb = ROOT / "data" / "gb"
    # gas: metallic mains (lines) within ~100 m
    pipes, mrp = [], []
    for path in glob.glob(str(gb / "gas" / "*" / "*.geojson")):
        for f in json.loads(open(path).read())["features"]:
            pipes.append(shape(f["geometry"])); mrp.append(bool(f["properties"].get("mrp")))
    gas_tree = STRtree(pipes) if pipes else None
    # water: storm overflows with improvement works planned by 2030 (points) within ~500 m
    ovf = [f for f in json.loads((gb / "water" / "overflows.geojson").read_text())["features"]
           if (f["properties"].get("planned") or 9999) <= 2030]
    ovf_tree = STRtree([shape(f["geometry"]) for f in ovf]) if ovf else None
    # electricity: secondaries over 100% utilisation within ~200 m
    hot = [Point(p[0], p[1]) for path in glob.glob(str(gb / "sec" / "points_*.json"))
           for p in json.loads(open(path).read()) if p[2] > 100]
    hot_tree = STRtree(hot) if hot else None
    # telecoms: nearest output area's share without gigabit; Project Gigabit postcodes within ~200 m
    oa = json.loads((gb / "telecoms" / "oa.json").read_text())
    oa_tree = STRtree([Point(p[0], p[1]) for p in oa])
    gig = json.loads((gb / "telecoms" / "gigabit.json").read_text())["postcodes"]
    gig_tree = STRtree([Point(p[0], p[1]) for p in gig])

    from shapely.geometry import box as _box

    def box(pt, dx, dy):
        return _box(pt.x - dx, pt.y - dy, pt.x + dx, pt.y + dy)

    for r in rows:
        pt = Point(r[0], r[1])
        g = gas_tree.query(box(pt, 0.0015, 0.0009)) if gas_tree is not None else []
        gas_km = round(sum(pipes[i].length for i in g) * 80, 2)  # degrees -> ~km at GB latitudes (rough)
        gas_mrp = any(mrp[i] for i in g)
        o = ovf_tree.query(box(pt, 0.0075, 0.0045)) if ovf_tree is not None else []
        ovf_year = min((ovf[i]["properties"]["planned"] for i in o), default=None)
        e = len(hot_tree.query(box(pt, 0.003, 0.0018))) if hot_tree is not None else 0
        nearest_oa = oa[oa_tree.nearest(pt)]
        nogig = nearest_oa[2] if nearest_oa[2] >= 0 else None
        pg = gig_tree.query(box(pt, 0.003, 0.0018))
        pg_year = min((gig[i][4] for i in pg if gig[i][4] > 0), default=None)
        extra = (2 if gas_mrp else 1 if gas_km else 0) + (1 if ovf_year else 0) + (1 if e else 0) + \
                (1 if (nogig or 0) >= 25 or pg_year else 0)
        r[2] += extra
        r.append({"gas_km": gas_km or None, "gas_mrp": gas_mrp or None, "ovf": ovf_year, "hot_sec": e or None,
                  "nogig": nogig, "pg": pg_year})
    rows.sort(key=lambda r: -r[2])


def write_streets(result):
    """Overview dots (points.json: [lon, lat, score]) plus full street rows per ~10 km cell, loaded on click."""
    import shutil
    from build_gb_secondary import cell_key
    shutil.rmtree(OUT / "cells", ignore_errors=True)
    (OUT / "cells").mkdir(parents=True)
    cells = {}
    for r in result["streets"]:
        cells.setdefault(cell_key(r[0], r[1]), []).append(r)
    for key, rows in cells.items():
        (OUT / "cells" / f"{key}.json").write_text(json.dumps(rows, separators=(",", ":")))
    meta = {k: v for k, v in result.items() if k != "streets"}
    util = [result["sectors"].index(u) for u in UTILITIES]

    def flags(r):  # 1: dug by 3+ utilities in the year · 2: utility works planned · 4: restriction proposed
        return ((sum(1 for i in util if r[3][i]) >= 3) * 1) | (any(r[4][i] for i in util) * 2) | (bool(r[8]) * 4)
    meta["points"] = [[r[0], r[1], r[2], flags(r)] for r in result["streets"]]
    (OUT / "streets.json").write_text(json.dumps(meta, separators=(",", ":")))


def section58(today):
    """Latest state of every Section 58 notice, keeping those proposed or in force today."""
    latest = {}
    for path in sorted(glob.glob(str(RAW / "s58" / "*.zip"))):
        for name, body in zip_entries(path):
            if name.endswith(".json"):
                d = json.loads(body)
                ref = d.get("object_reference")
                if ref and (ref not in latest or d["event_time"] > latest[ref]["event_time"]):
                    latest[ref] = d
    out = []
    for ref, d in latest.items():
        o = d["object_data"]
        status, end = o.get("section_58_status"), (o.get("end_date") or "")[:10]
        if status not in ("proposed", "in_force") or (end and end < today):
            continue
        start = (o.get("start_date") or "")[:10]
        if status == "proposed" and not (YEAR_AGO(today) <= start <= FIVE_YEARS(today)):
            continue  # stale proposals (never completed or withdrawn) and mis-keyed dates
        geom = wkt_to_geojson(o.get("section_58_coordinates") or "")
        if not geom:
            continue
        out.append({"type": "Feature", "geometry": geom, "properties": {
            "ref": ref, "status": status, "start": (o.get("start_date") or "")[:10], "end": end,
            "duration": (o.get("section_58_duration") or "").replace("_", " "),
            "extent": (o.get("section_58_extent") or "").replace("_", " "), "ha": o.get("highway_authority"),
            "street": o.get("street_name"), "town": o.get("town") or o.get("area_name"), "usrn": o.get("usrn")}})
    print(f"Section 58: {len(latest)} notices in the archive; {sum(f['properties']['status'] == 'in_force' for f in out)} "
          f"in force and {sum(f['properties']['status'] == 'proposed' for f in out)} proposed as of {today}")
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    s58 = section58(today)
    import sys
    if "--fetch-permits" in sys.argv:
        fetch_permits(today)
    if (RAW / "works.json").exists():
        write_streets(streets(today, s58))
    (OUT / "s58.geojson").write_text(json.dumps({"type": "FeatureCollection", "as_of": today, "features": s58},
                                                separators=(",", ":")))


if __name__ == "__main__":
    main()
