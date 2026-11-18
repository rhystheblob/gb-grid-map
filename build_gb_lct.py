"""Low carbon technologies (LCTs) to 2050 on the LV network: building-level pilots.

Every home in a pilot area gets a place in the queue for each LCT (heat pump, home EV charger, rooftop PV, home
battery); the map's year slider and pathway pick how far down each queue uptake has reached, using SSEN's
Distribution Future Energy Scenarios (DFES 2025, Regen for SSEN, yearly to 2050 per primary substation). Homes are
connected to LV feeders and secondary substations, and the page works out feeder and transformer loading for two
ways of running the kit (worst case: everything at full power at once; optimised: smart charging, pre-heating,
batteries and PV working with the network), the upgrades each would need and what they cost.

Pilots:
- Southall (SSEN, The Green primary, London Borough of Ealing): the real LV network. SSEN publishes its LV cables
  (with conductor size, so feeder ratings) and secondary transformers for this area on its public ArcGIS
  (data/lv_pilot.geojson, data/secondary_pilot.geojson via fetch_public.py), and smart meter peaks per LV feeder
  (data/smart_meter_secondary.json).
- Aberfeldy (SSEN, Aberfeldy primary, Perth and Kinross): a rural, off-gas area with a synthetic LV network. The
  secondary substation supply areas are SSEN's (secondary-substation-esa); feeders are routed along roads
  (OpenStreetMap) from a substation placed in each area to its homes, because LV cables aren't published here.

Homes: OS Open UPRN address points (Oct 2026) inside OS OpenMap Local building outlines (both OS open data, OGL),
with OpenStreetMap tags and points of interest used to drop non-domestic buildings. House type comes from the
footprint per address (the outlines merge terraces into blocks). Which homes have an LCT today isn't published at
address level (EPC certificates would give heat pumps and PV per home; see README), so today's DFES baseline is
assigned to the homes most likely to have them.

Inputs (git-ignored, data/raw/lct/): ssen_dfes2025_esa.xlsx -> dfes_pilots.json (rows for the pilot primaries,
extracted with openpyxl), ssen_lct_primary.csv (customers per primary), osm_<pilot>.json (Overpass),
osml_<pilot>.json (OS OpenMap Local buildings clipped to the pilot, BNG), uprn_<pilot>.csv (OS Open UPRN clipped),
aberfeldy_esa.geojson (SSEN secondary supply areas for primary 267). See README for how to refresh them.

Output:  data/gb/lct/index.json, data/gb/lct/<pilot>.json
Usage:   python3 build_gb_lct.py                 # build from data/raw/lct/
         python3 build_gb_lct.py --fetch         # first refresh the inputs (~900 MB of downloads, deleted after;
                                                 # needs SSEN_API_KEY and openpyxl, e.g.
                                                 # uv run --no-project --with openpyxl --with shapely --with pyproj \
                                                 #   python build_gb_lct.py --fetch)
"""
import collections
import csv
import hashlib
import heapq
import json
import math
import random
import shutil
import struct
import sys
import urllib.parse
import urllib.request
import zipfile

from pyproj import Transformer
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, shape
from shapely.ops import linemerge
from shapely.strtree import STRtree

from dnos.common import ROOT

RAW = ROOT / "data" / "raw" / "lct"
OUT = ROOT / "data" / "gb" / "lct"
YEARS = list(range(2025, 2051))
TO_BNG = Transformer.from_crs(4326, 27700, always_xy=True)
TO_LL = Transformer.from_crs(27700, 4326, always_xy=True)

PATHWAYS = {"EE": "Electric Engagement", "HT": "Holistic Transition", "FB": "Falling Behind"}
FULL = "FE"  # this map's own stretch case: every home electrified by 2050

# Average sizes used to turn DFES capacities (MW) into numbers of installations
EV_CHARGER_KW = 7.4
PV_KWP = {"detached": 4.5, "semi": 3.5, "terrace": 3.0, "flat": 0.0}
BATTERY_KW = 3.68
# DFES counts include new-build homes, which connect to new networks rather than the existing feeders. New homes
# are built to the Future Homes Standard, so each takes a heat pump (0.85; the rest heat networks) and ~2.5 kW of
# PV off the DFES totals; chargers, batteries and direct electric heating are split pro rata by number of homes.
NEWBUILD = {"hp": 0.85, "pv_kw": 2.5}

TYPES = ["detached", "semi", "terrace", "flat"]
NON_DOMESTIC_TAGS = {
    "commercial", "retail", "industrial", "warehouse", "school", "church", "mosque", "temple", "gurdwara", "office",
    "hospital", "supermarket", "garage", "garages", "shed", "service", "hotel", "public", "civic", "kindergarten",
    "university", "college", "sports_centre", "train_station", "transportation", "farm_auxiliary", "barn",
    "greenhouse", "roof", "construction", "place_of_worship", "ruins", "stable", "cowshed", "carport", "hut",
    "chapel", "cathedral", "sports_hall", "fire_station", "police", "toilets", "parking", "pavilion", "bunker",
    "storage_tank", "silo", "boathouse", "conservatory", "manufacture", "data_center", "kiosk", "museum",
}
NON_DOMESTIC_LANDUSE = {"industrial", "commercial", "retail", "railway", "education", "religious", "military"}

# LV cable ratings (A, laid direct, distribution cyclic) by aluminium cross-section; copper carries ~28% more.
AL_AMPS = [(35, 120), (70, 175), (95, 210), (120, 240), (150, 270), (185, 300), (240, 350), (300, 395)]
KVA_PER_AMP = math.sqrt(3) * 0.4  # 400 V three-phase
GMT_SIZES = [315, 500, 800, 1000]  # ground-mounted transformers (kVA)
PMT_SIZES = [25, 50, 100, 200]     # pole-mounted transformers (kVA)


def ll(x, y):
    lon, lat = TO_LL.transform(x, y)
    return [round(lon, 6), round(lat, 6)]


def bng(lon, lat):
    return TO_BNG.transform(lon, lat)


def stable_rand(*key):
    """A repeatable random number in [0, 1) for a key (so rebuilds keep the same homes in the same order)."""
    h = hashlib.sha1("|".join(map(str, key)).encode()).hexdigest()
    return int(h[:12], 16) / 16 ** 12


def amps(size_mm2, copper):
    a = AL_AMPS[0][1]
    for (s0, a0), (s1, a1) in zip(AL_AMPS, AL_AMPS[1:]):
        if size_mm2 <= s0:
            a = a0 * size_mm2 / s0 if s0 == 35 else a0
            break
        if size_mm2 <= s1:
            a = a0 + (a1 - a0) * (size_mm2 - s0) / (s1 - s0)
            break
    else:
        a = AL_AMPS[-1][1] * (size_mm2 / 300) ** 0.6
    return a * (1.28 if copper else 1.0)


def ohms_per_km(size_mm2, copper):
    return (18.5 if copper else 30.3) / size_mm2


def parse_size(size_val, material):
    """'185sqmm' / '185' (mm2) or '0.1' (square inches, old copper) -> mm2, copper?"""
    s = (size_val or "").replace("sqmm", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None, None
    if v <= 0:
        return None, None
    mm2 = v * 645.16 if v < 1 else v
    return mm2, (material or "").strip().lower().startswith("copper")


def cable_label(head):
    mm2, cu, raw, material = head
    raw = raw.replace("sqmm", "").strip()
    size = f"{raw} sq in ({round(mm2)} mm²)" if float(raw) < 1 else f"{round(mm2)} mm²"
    return f"{size} {'copper' if cu else 'aluminium'}"


# ---------------------------------------------------------------- homes

def split_block(block, n):
    """Cut a terrace or semi-detached block into n house plots of equal area, across its long axis.

    OS OpenMap Local merges a row of houses into one outline, so party walls are estimated: plots are cut
    perpendicular to the block's longest side. Returns (plots in order along the row, unit axis vector)."""
    rect = block.minimum_rotated_rectangle
    cs = list(rect.exterior.coords)[:4]
    edges = [(cs[i], cs[(i + 1) % 4]) for i in range(4)]
    (x0, y0), (x1, y1) = max(edges, key=lambda e: math.hypot(e[1][0] - e[0][0], e[1][1] - e[0][1]))
    L_ = math.hypot(x1 - x0, y1 - y0) or 1
    ux, uy = (x1 - x0) / L_, (y1 - y0) / L_
    vx, vy = -uy, ux
    proj = [x * ux + y * uy for x, y in cs]
    t0, t1 = min(proj), max(proj)
    cv = block.centroid.x * vx + block.centroid.y * vy  # the block's position across the axis
    lo_v, hi_v = cv - 1e3, cv + 1e3

    def strip(a, b):  # the band between positions a and b along the axis, around the block
        return Polygon([(a * ux + lo_v * vx, a * uy + lo_v * vy), (b * ux + lo_v * vx, b * uy + lo_v * vy),
                        (b * ux + hi_v * vx, b * uy + hi_v * vy), (a * ux + hi_v * vx, a * uy + hi_v * vy)])

    total, cuts = block.area, [t0]
    for k in range(1, n):  # bisect for the cut that leaves k/n of the area behind it
        lo, hi = cuts[-1], t1
        for _ in range(30):
            mid = (lo + hi) / 2
            if block.intersection(strip(t0 - 1, mid)).area < total * k / n:
                lo = mid
            else:
                hi = mid
        cuts.append((lo + hi) / 2)
    cuts.append(t1)
    plots = []
    for a, b in zip(cuts, cuts[1:]):
        g = block.intersection(strip(a, b))
        if g.geom_type != "Polygon":
            polys = [q for q in getattr(g, "geoms", []) if q.geom_type == "Polygon"]
            g = max(polys, key=lambda q: q.area) if polys else None
        plots.append(g if g is not None and not g.is_empty and g.area > 1 else None)
    return plots, (ux, uy)


def load_osm(pilot):
    els = json.load(open(RAW / f"osm_{pilot}.json"))["elements"]
    nondom, roads, pois, landuse = [], [], [], []
    for e in els:
        t = e.get("tags", {})
        if e["type"] == "way" and "geometry" in e:
            pts = [bng(g["lon"], g["lat"]) for g in e["geometry"]]
            if t.get("building") in NON_DOMESTIC_TAGS and len(pts) >= 4:
                nondom.append(Polygon(pts).buffer(0))
            elif t.get("highway") and t["highway"] not in ("proposed", "construction", "abandoned") and len(pts) >= 2:
                roads.append(LineString(pts))
            elif t.get("landuse") in NON_DOMESTIC_LANDUSE and len(pts) >= 4:
                landuse.append(Polygon(pts).buffer(0))
        elif e["type"] == "node" and (t.get("shop") or t.get("office") or t.get("tourism") in ("hotel", "guest_house")
                                       or t.get("amenity") not in (None, "bench", "waste_basket", "post_box", "bicycle_parking",
                                                                   "parking", "telephone", "recycling", "vending_machine")):
            pois.append(Point(bng(e["lon"], e["lat"])))
    return nondom, roads, pois, landuse


def load_homes(pilot, rural, scale=1.0):
    """Dwellings = address points inside domestic building blocks, typed by footprint per address.

    Address points overcount homes (parent records for converted houses and flat blocks, garages, annexes), so
    blocks with several addresses keep `scale` of them, set so the pilot matches SSEN's customer count."""
    nondom, roads, pois, landuse = load_osm(pilot)
    blocks = []
    for rings in json.load(open(RAW / f"osml_{pilot}.json")):
        p = Polygon(rings[0], rings[1:]).buffer(0)
        if p.area >= 20:
            blocks.append(p)
    tree = STRtree(blocks)
    uprns = collections.defaultdict(list)
    with open(RAW / f"uprn_{pilot}.csv") as f:
        for r in csv.DictReader(f):
            x, y = bng(float(r["lon"]), float(r["lat"]))
            idx = tree.query(Point(x, y), predicate="intersects")
            if len(idx):
                uprns[int(idx[0])].append((x, y, r["uprn"]))
    nd_tree, lu_tree, poi_tree = STRtree(nondom), STRtree(landuse), STRtree(pois)
    homes, kept_blocks = [], []
    for bi, pts in uprns.items():
        b = blocks[bi]
        shops = len(poi_tree.query(b, predicate="intersects"))
        nd_area = sum(nondom[j].intersection(b).area for j in nd_tree.query(b, predicate="intersects"))
        if nd_area > 0.5 * b.area:
            continue
        if any(landuse[j].contains(b.representative_point()) for j in lu_tree.query(b, predicate="intersects")) and len(pts) < 4:
            continue
        n = len(pts) - shops
        if n <= 0:
            continue
        if n >= 2:
            n = max(1, round(n * scale))
        if b.area > 600 and n <= 2:  # a big shed with an address or two: farm, works, hall
            continue
        if n >= 3 and len(pts) > n:  # drop parent-shell / shop addresses first
            pts = pts[shops:]
        if n >= 3 and b.area / n < 32:
            typ = "flat"
            n_units = n
        elif n == 1:
            typ = "detached"
            n_units = 1
        elif n == 2 and b.area / n >= 35:
            typ = "semi"
            n_units = 2
        else:
            typ = "terrace" if b.area / n >= 32 else "flat"
            n_units = n
        pts = sorted(pts, key=lambda p: p[2])[:n_units]
        kept_blocks.append(b)
        plots = [None] * len(pts)
        if typ in ("terrace", "semi") and len(pts) >= 2:  # one plot per house, homes in order along the row
            cut, (ux, uy) = split_block(b, len(pts))
            pts = sorted(pts, key=lambda p: p[0] * ux + p[1] * uy)
            plots = cut
        for (x, y, u), plot in zip(pts, plots):
            if plot is not None:  # the home's dot sits inside its own plot (else it keeps its address point)
                c = plot.representative_point()
                x, y = c.x, c.y
            homes.append({"x": x, "y": y, "uprn": u, "type": typ, "block": len(kept_blocks) - 1,
                          "plot": b.area / n_units, "single": n_units == 1 and len(pts) == 1, "house": plot})
    print(f"  {pilot}: {len(homes)} dwellings in {len(kept_blocks)} domestic blocks "
          f"({collections.Counter(h['type'] for h in homes)})")
    return homes, kept_blocks, roads


# ---------------------------------------------------------------- networks

def graph_from_lines(lines, snap=1.0):
    """Undirected graph from line geometries; vertices within `snap` metres merge."""
    key = lambda x, y: (round(x / snap), round(y / snap))
    adj, pos = collections.defaultdict(dict), {}
    for ln in lines:
        cs = list(ln.coords)
        for (x0, y0), (x1, y1) in zip(cs, cs[1:]):
            a, b = key(x0, y0), key(x1, y1)
            if a == b:
                continue
            pos[a], pos[b] = (x0, y0), (x1, y1)
            d = math.hypot(x1 - x0, y1 - y0)
            adj[a][b] = min(d, adj[a].get(b, 1e18))
            adj[b][a] = adj[a][b]
    return adj, pos, key


def dijkstra(adj, src, limit=1e18):
    dist, prev, pq = {src: 0.0}, {}, [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18) or d > limit:
            continue
        for v, w in adj[u].items():
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    return dist, prev


def feeder_capacity(size_mm2, copper, length_km, overhead=False):
    """Thermal rating and an equivalent loading limit for voltage (distributed load: half the feeder length)."""
    a = amps(size_mm2, copper) * (0.75 if overhead else 1.0)
    z = ohms_per_km(size_mm2, copper) * (1.1 if overhead else 1.0)
    return {"kva": round(a * KVA_PER_AMP), "z": round(z, 4), "leff": round(max(0.02, length_km / 2), 3)}


def southall_network(homes):
    lv = json.load(open(ROOT / "data" / "lv_pilot.geojson"))["features"]
    subs_raw = json.load(open(ROOT / "data" / "secondary_pilot.geojson"))["features"]
    smart = json.load(open(ROOT / "data" / "smart_meter_secondary.json"))["secondaries"]
    subs, sub_idx = [], {}
    for s in subs_raw:
        p = s["properties"]
        nrn = str(p["NRN_Dis"]).zfill(10)
        x, y = bng(*s["geometry"]["coordinates"])
        sm = smart.get(nrn) or {}
        sub_idx[nrn] = len(subs)
        subs.append({"nrn": nrn, "name": (p.get("transforme") or "").title(), "x": x, "y": y,
                     "peak": sm.get("peak_kw"), "meters": sm.get("meters"),
                     "feeders_sm": {f["id"][-2:]: f for f in sm.get("feeders", [])}, "kind": "GMT",
                     "postcode": p.get("transfor_7")})
    circuits = collections.defaultdict(list)
    dropped = 0
    for f in lv:
        p = f["properties"]
        parts = p["circuit_id"].strip().split(".")
        if len(parts) != 4:
            dropped += 1
            continue
        nrn = parts[0].zfill(4) + parts[1].zfill(3) + parts[2].zfill(3)
        if nrn not in sub_idx:
            dropped += 1
            continue
        geom = LineString([bng(*c) for c in f["geometry"]["coordinates"]])
        circuits[(nrn, parts[3].zfill(2))].append((geom, p))
    print(f"  southall: {len(circuits)} LV circuits, {dropped} cable segments dropped (no published substation)")

    feeders, seg_geoms, seg_feeder, seg_service = [], [], [], []
    for (nrn, ff), segs in sorted(circuits.items()):
        s = subs[sub_idx[nrn]]
        sp = Point(s["x"], s["y"])
        mains = [(g, p) for g, p in segs if p["usage"] == "Main"] or segs
        adj, pos, key = graph_from_lines([g for g, _ in segs], snap=1.0)
        src = min(pos, key=lambda k: math.hypot(pos[k][0] - s["x"], pos[k][1] - s["y"]))
        dist, _ = dijkstra(adj, src)
        far = max(dist.values()) if dist else 0
        # rating from the cable leaving the substation: the largest sized main within 40 m of it, else the
        # nearest main with a size
        head = None
        sized = []
        for g, p in mains:
            mm2, cu = parse_size(p["size_val"], p["material"])
            if mm2:
                sized.append((g.distance(sp), amps(mm2, cu), (mm2, cu, p["size_val"].strip(), (p["material"] or "").strip())))
        near = [t for t in sized if t[0] <= 40]
        if near:
            head = max(near, key=lambda t: t[1])[2]
        elif sized:
            head = min(sized, key=lambda t: t[0])[2]
        if head and amps(head[0], head[1]) < 150 and sized:  # a service-size head on a street feeder: likely mis-recorded
            head = max(sized, key=lambda t: t[1])[2]
        assumed = head is None
        if assumed:
            head = (185, False, "185", "Aluminium")
        cap = feeder_capacity(head[0], head[1], far / 1000)
        smf = s["feeders_sm"].get(ff) or {}
        fi = len(feeders)
        feeders.append({
            "sub": sub_idx[nrn], "id": f"{nrn}{ff}", "name": smf.get("name") or f"Feeder {int(ff)}",
            **cap, "cable": cable_label(head) + (" (assumed)" if assumed else ""),
            "main_m": round(sum(g.length for g, p in mains)), "far_m": round(far), "oh": 0, "real": 1,
            "peak": smf.get("peak_kw"), "meters": smf.get("meters"),
            "mains": [[ll(*c) for c in g.coords] for g, p in mains],
            "svc": [[ll(*c) for c in g.coords] for g, p in segs if p["usage"] == "Service"],
        })
        stub = sum(g.length for g, _ in segs) < 25  # a stub at the substation, not a street feeder
        for g, p in segs:
            if stub:
                continue
            seg_geoms.append(g)
            seg_feeder.append(fi)
            seg_service.append(p["usage"] == "Service")

    # each home to the nearest service cable (where a house's service ends), else the nearest main
    tree = STRtree(seg_geoms)
    svc_idx = [i for i, sv in enumerate(seg_service) if sv]
    svc_tree = STRtree([seg_geoms[i] for i in svc_idx])
    kept = []
    for h in homes:
        pt = Point(h["x"], h["y"])
        j, d = svc_tree.query_nearest(pt, max_distance=30, return_distance=True)
        if len(j):
            h["feeder"] = seg_feeder[svc_idx[int(j[0])]]
        else:
            j, d = tree.query_nearest(pt, max_distance=45, return_distance=True)
            if not len(j):
                continue
            h["feeder"] = seg_feeder[int(j[0])]
        kept.append(h)
    return subs, feeders, kept, "real"


def aberfeldy_network(homes, roads):
    esas = json.load(open(RAW / "aberfeldy_esa.geojson"))["features"]
    smart = json.load(open(ROOT / "data" / "smart_meter_secondary.json"))["secondaries"]
    polys = []
    for f in esas:
        g = shape(f["geometry"])
        polys.append(Polygon([bng(*c) for c in g.exterior.coords]) if g.geom_type == "Polygon" else
                     MultiPolygon([Polygon([bng(*c) for c in p.exterior.coords]) for p in g.geoms]))
    polys = [p.buffer(0) for p in polys]
    ptree = STRtree(polys)
    by_esa = collections.defaultdict(list)
    for h in homes:
        j = ptree.query(Point(h["x"], h["y"]), predicate="intersects")
        if len(j):
            by_esa[int(j[0])].append(h)
    # road graph, densified so homes can join it near where they are
    dense = []
    for r in roads:  # keep every original vertex (junctions), adding points every ~25 m between them
        cs, pts = list(r.coords), []
        for (x0, y0), (x1, y1) in zip(cs, cs[1:]):
            n = max(1, int(math.hypot(x1 - x0, y1 - y0) // 25))
            pts += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
        dense.append(LineString(pts + [cs[-1]]))
    adj, pos, key = graph_from_lines(dense, snap=1.0)
    nodes = list(pos)
    ntree = STRtree([Point(pos[k]) for k in nodes])

    subs, feeders, kept = [], [], []
    for ei, hs in sorted(by_esa.items()):
        p = esas[ei]["properties"]
        nrn = str(p["NRN_10_DIGIT"]).zfill(10)
        sm = smart.get(nrn) or {}
        cx = sum(h["x"] for h in hs) / len(hs)
        cy = sum(h["y"] for h in hs) / len(hs)
        # substation: the road point nearest the homes' centre (inside the area if possible)
        j = ntree.query_nearest(Point(cx, cy), max_distance=400)
        src = nodes[int(j[0])] if len(j) else None
        sx, sy = pos[src] if src else (cx, cy)
        n_dw = len(hs)
        town = n_dw > 60
        kind = "GMT" if town else "PMT"
        si = len(subs)
        subs.append({"nrn": nrn, "name": f"Secondary {nrn[-6:-3]}/{nrn[-3:]}", "x": sx, "y": sy,
                     "peak": sm.get("peak_kw"), "meters": sm.get("meters"), "kind": kind})
        dist, prev = dijkstra(adj, src, limit=4000) if src else ({}, {})
        branches = collections.defaultdict(lambda: {"homes": [], "edges": set(), "far": 0.0, "spurs": []})
        for h in hs:
            pt = Point(h["x"], h["y"])
            j, d = ntree.query_nearest(pt, max_distance=250, return_distance=True)
            node = nodes[int(j[0])] if len(j) else None
            if node is not None and node in dist and dist[node] < 4000:
                path, u = [], node
                while u != src:
                    path.append(u)
                    u = prev[u]
                first = path[-1] if path else "at-sub"
                br = branches[first]
                for a, b in zip([src] + path[::-1], path[::-1]):
                    br["edges"].add((a, b))
                br["far"] = max(br["far"], dist[node] + float(d[0]))
                h["svc"] = [ll(h["x"], h["y"]), ll(*pos[node])]
            else:  # no road nearby: an overhead spur straight from the substation
                ang = math.atan2(h["y"] - sy, h["x"] - sx)
                br = branches["spur" + str(int((ang + math.pi) // (math.pi / 2)))]
                br["spurs"].append([ll(sx, sy), ll(h["x"], h["y"])])
                br["far"] = max(br["far"], math.hypot(h["x"] - sx, h["y"] - sy))
                h["svc"] = None
            br["homes"].append(h)
        # at most 4 feeders per substation: merge the smallest branches
        groups = sorted(branches.values(), key=lambda b: -len(b["homes"]))
        while len(groups) > 4:
            a = groups.pop()
            b = min(groups, key=lambda g: len(g["homes"]))
            b["homes"] += a["homes"]; b["edges"] |= a["edges"]; b["spurs"] += a["spurs"]; b["far"] = max(a["far"], b["far"])
            groups.sort(key=lambda g: -len(g["homes"]))
        for k, g in enumerate(groups):
            segs = [LineString([pos[a], pos[b]]) for a, b in g["edges"]]
            merged = linemerge(segs) if segs else None
            lines = [] if merged is None else ([merged] if merged.geom_type == "LineString" else list(merged.geoms))
            mains = [[ll(*c) for c in m.simplify(2).coords] for m in lines] + g["spurs"]
            main_m = sum(m.length for m in lines) + sum(
                math.hypot(*(a - b for a, b in zip(bng(*s[1]), (sx, sy)))) for s in g["spurs"])
            size, cu = (185, False) if town else (95, False)
            cap = feeder_capacity(size, cu, g["far"] / 1000, overhead=not town)
            fi = len(feeders)
            feeders.append({"sub": si, "id": f"{nrn}{k + 1:02d}", "name": f"Feeder {k + 1}", **cap,
                            "cable": ("185 aluminium underground" if town else "95 aluminium overhead") + " (synthetic)",
                            "main_m": round(main_m), "far_m": round(g["far"]), "oh": 0 if town else 1, "real": 0,
                            "peak": None, "meters": None, "mains": mains,
                            "svc": [h["svc"] for h in g["homes"] if h.get("svc")]})
            for h in g["homes"]:
                h["feeder"] = fi
                kept.append(h)
    print(f"  aberfeldy: {len(subs)} secondaries, {len(feeders)} synthetic feeders")
    return subs, feeders, kept, "synthetic"


def transformer_size(sub, dwellings):
    if sub["kind"] == "PMT":
        need = max(dwellings * 3.5, (sub["peak"] or 0) * 1.4)  # rural, off-gas: designed for electric heating
        return next((s for s in PMT_SIZES if s >= need), 315)
    base = 315 if dwellings <= 120 else 500 if dwellings <= 300 else 800 if dwellings <= 550 else 1000
    need = (sub["peak"] or 0) * 1.25
    return max(base, next((s for s in GMT_SIZES if s >= need), 1000))


# ---------------------------------------------------------------- uptake

def dfes_curves(primary, n_homes, eligible, primary_customers):
    """Fraction of eligible pilot homes with each LCT, per year and pathway (from DFES counts for the primary)."""
    rows = [r for r in json.load(open(RAW / "dfes_pilots.json"))["rows"] if r["Primary_name"] == primary]
    share = n_homes / primary_customers

    def series(scen, tech, sub=None):
        out = [0.0] * len(YEARS)
        for r in rows:
            if r["Scenario"] != scen or r["Technology"] != tech or (sub and r["Subtechnology"] != sub):
                continue
            for i, y in enumerate(YEARS):
                out[i] += float(r["Baseline" if y == 2025 else str(y)] or 0)
        return out

    curves = {}
    for code, scen in PATHWAYS.items():
        nb = series(scen, "New developments", "Domestic")
        hp = series(scen, "Domestic heat pumps")
        ev = series(scen, "EV chargers", "Domestic off-street with charger")
        os_ = series(scen, "EV chargers", "Domestic on-street")
        pv = series(scen, "Solar PV", "<10 kW")
        bat = series(scen, "Battery storage", "Domestic_storage")
        de = series(scen, "Domestic direct electric heating")
        c = {"hp": [], "ev": [], "pv": [], "bat": [], "de": [], "os": []}
        for i in range(len(YEARS)):
            existing = primary_customers / (primary_customers + nb[i])  # existing homes' share of all homes
            hp_n = max(0.0, hp[i] - NEWBUILD["hp"] * nb[i]) * share
            ev_n = ev[i] * 1000 / EV_CHARGER_KW * existing * share
            pv_n = max(0.0, pv[i] * 1000 - NEWBUILD["pv_kw"] * nb[i]) / eligible["kwp_avg"] * share
            bat_n = bat[i] * 1000 / BATTERY_KW * existing * share
            de_n = max(0.0, de[i] - de[0]) * existing * share  # today's electric heating is already in the base load
            os_n = os_[i] * 1000 / EV_CHARGER_KW * existing * share
            os_n += max(0.0, ev_n - eligible["ev"])  # more home chargers than homes with parking: the rest charge on-street
            c["hp"].append(round(min(1, hp_n / max(1, eligible["hp"])), 4))
            c["ev"].append(round(min(1, ev_n / max(1, eligible["ev"])), 4))
            c["pv"].append(round(min(1, pv_n / max(1, eligible["pv"])), 4))
            c["bat"].append(round(min(1, bat_n / max(1, eligible["bat"])), 4))
            c["de"].append(round(min(1, de_n / max(1, eligible["hp"])), 4))
            c["os"].append(round(os_n))
        for k in ("hp", "ev", "pv", "bat", "de", "os"):  # uptake never goes backwards on the map (installations stay)
            for i in range(1, len(YEARS)):
                c[k][i] = max(c[k][i], c[k][i - 1])
        curves[code] = c

    # Full electrification by 2050: S-curves from today's baseline to every home electrified (heat pumps 100%,
    # a charger at every home with parking, PV on 60% and batteries on 45% of houses), never below Electric Engagement
    ee = curves["EE"]
    target = {"hp": 1.0, "ev": 1.0, "pv": 0.6, "bat": 0.45}
    fe = {}
    for k, tgt in target.items():
        base = ee[k][0]
        fe[k] = [round(max(ee[k][i], base + (tgt - base) / (1 + math.exp(-(y - 2036) / 3.2))
                           * (1 if y < 2050 else 1 / (1 / (1 + math.exp(-(2050 - 2036) / 3.2))))), 4)
                 for i, y in enumerate(YEARS)]
        fe[k][-1] = max(fe[k][-1], tgt)
    fe["de"] = ee["de"]
    os_target = eligible["onstreet_homes"] * 0.25  # one on-street charger per four homes without parking
    fe["os"] = [round(max(ee["os"][i], ee["os"][0] + (os_target - ee["os"][0]) / (1 + math.exp(-(y - 2036) / 3.2))))
                for i, y in enumerate(YEARS)]
    curves[FULL] = fe
    return curves


def queue_ranks(homes, rural):
    """Each home's place in the queue (0 = first, 999 = never) for heat pump, home charger, PV and battery."""
    for h in homes:
        t = h["type"]
        h["offstreet"] = t in ("detached", "semi") or (t == "terrace" and stable_rand(h["uprn"], "park") < (0.6 if rural else 0.2))
        h["kwp"] = PV_KWP[t] * (1.15 if rural and t != "flat" else 1.0)
    score = {
        "hp": lambda h: {"detached": 1.0, "semi": 0.8, "terrace": 0.6, "flat": 0.35}[h["type"]] + 0.9 * stable_rand(h["uprn"], "hp"),
        "ev": lambda h: {"detached": 1.0, "semi": 0.8, "terrace": 0.6, "flat": 0}[h["type"]] + 0.9 * stable_rand(h["uprn"], "ev"),
        "pv": lambda h: min(1.0, h["plot"] / 120) + 0.9 * stable_rand(h["uprn"], "pv"),
    }
    elig = {"hp": lambda h: True, "ev": lambda h: h["offstreet"], "pv": lambda h: h["type"] != "flat",
            "bat": lambda h: h["type"] != "flat"}
    score["de"] = lambda h: {"flat": 1.0, "terrace": 0.5}.get(h["type"], 0.2) + 0.6 * stable_rand(h["uprn"], "de")
    elig["de"] = lambda h: True
    for tech in ("hp", "ev", "pv", "de"):
        el = sorted((h for h in homes if elig[tech](h)), key=lambda h: -score[tech](h))
        for i, h in enumerate(el):
            h["r_" + tech] = int(999 * i / max(1, len(el)))
        for h in homes:
            h.setdefault("r_" + tech, 999)
    # batteries mostly go where PV already is
    el = sorted((h for h in homes if elig["bat"](h)), key=lambda h: 0.7 * h["r_pv"] / 999 + 0.3 * stable_rand(h["uprn"], "bat"))
    for i, h in enumerate(el):
        h["r_bat"] = int(999 * i / max(1, len(el)))
    for h in homes:
        h.setdefault("r_bat", 999)
    return {"hp": len(homes), "ev": sum(1 for h in homes if h["offstreet"]), "pv": sum(1 for h in homes if h["type"] != "flat"),
            "bat": sum(1 for h in homes if h["type"] != "flat"),
            "kwp_avg": sum(h["kwp"] for h in homes if h["type"] != "flat") / max(1, sum(1 for h in homes if h["type"] != "flat")),
            "onstreet_homes": sum(1 for h in homes if not h["offstreet"])}


def onstreet_sites(feeders, homes, n_max):
    """Candidate on-street charger spots: along mains, next to homes without off-street parking."""
    no_park = [h for h in homes if not h["offstreet"]]
    rnd = random.Random(7)
    rnd.shuffle(no_park)
    sites, used = [], set()
    for h in no_park:
        cell = (h["feeder"], round(h["x"] / 40), round(h["y"] / 40))  # at most one spot per ~40 m of street
        if cell in used:
            continue
        used.add(cell)
        sites.append([*ll(h["x"] + 4, h["y"] + 4), h["feeder"]])
        if len(sites) >= n_max:
            break
    rnd.shuffle(sites)
    return sites


# ---------------------------------------------------------------- build

PILOTS = [
    {"id": "southall", "name": "Southall (The Green)", "primary": "THE GREEN", "nrn": "2504", "la": "Ealing",
     "rural": False, "admd_default": None,
     "note": "Real LV network: SSEN publishes the LV cables and secondary transformers for this area, and smart "
             "meter peaks per feeder. Transformer ratings are assumed (SSEN publishes none)."},
    {"id": "aberfeldy", "name": "Aberfeldy (rural Perthshire)", "primary": "ABERFELDY", "nrn": "267", "la": "Perth and Kinross",
     "rural": True, "admd_default": None,
     "note": "Synthetic LV network: SSEN's secondary supply areas are real, but LV cables aren't published here, so "
             "feeders follow the roads from a substation placed in each area. Off the gas grid, so oil, LPG and "
             "storage heaters are being replaced."},
]


# ---------------------------------------------------------------- inputs (--fetch)

BOXES = {"southall": (-0.3915, 51.4935, -0.3600, 51.5160), "aberfeldy": (-3.9390, 56.5590, -3.7110, 56.6940)}
OS_TILES = {"southall": "TQ", "aberfeldy": "NN"}
UA = {"User-Agent": "gb-grid-map/1.0 (+https://github.com/rhystheblob/gb-grid-map)"}


def download(url, path, headers=None):
    class NoAuthRedirect(urllib.request.HTTPRedirectHandler):  # SSEN's R2 links reject the Authorization header
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return urllib.request.Request(newurl, headers=UA)
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.build_opener(NoAuthRedirect).open(req, timeout=600) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)


def fetch_inputs():
    import fetch_portal  # SSEN portal key and CKAN helper
    import openpyxl
    RAW.mkdir(parents=True, exist_ok=True)
    key = fetch_portal.api_key()
    print("SSEN DFES 2025 and LCT connections")
    res = {r["name"].strip(): r["url"] for n in ("low_carbon_technologies", "low-carbon-technology-connections")
           for r in fetch_portal.ckan("package_show", key, id=n)["resources"]}
    download(res["SSEN DFES 2025 - Results by licence area and ESA"], RAW / "ssen_dfes2025_esa.xlsx", {"Authorization": key})
    download(res["LCT - Primary Substation Level (CSV)"], RAW / "ssen_lct_primary.csv", {"Authorization": key})
    ws = openpyxl.load_workbook(RAW / "ssen_dfes2025_esa.xlsx", read_only=True)["05_ESA_projections"]
    rows = ws.iter_rows(values_only=True)
    hdr = [str(h) for h in next(rows)]
    keep = [dict(zip(hdr, r)) for r in rows if r[hdr.index("Primary_name")] in {p["primary"] for p in PILOTS}]
    json.dump({"header": hdr, "rows": keep}, open(RAW / "dfes_pilots.json", "w"))
    print("SSEN secondary supply areas for Aberfeldy (from data/raw/secondary_shepd.geojson, see fetch_portal.py)")
    esa = json.load(open(ROOT / "data" / "raw" / "secondary_shepd.geojson"))["features"]
    json.dump({"type": "FeatureCollection", "features": [f for f in esa if str(f["properties"]["NRN_10_DIGIT"]).zfill(10).startswith("0267")]},
              open(RAW / "aberfeldy_esa.geojson", "w"))
    for pid, (w, s, e, n) in BOXES.items():
        print(f"OpenStreetMap for {pid} (Overpass)")
        bb = f"{s},{w},{n},{e}"
        q = (f"[out:json][timeout:180];(way[building]({bb});relation[building]({bb});way[highway]({bb});node[shop]({bb});"
             f"node[amenity]({bb});node[office]({bb});node[tourism]({bb});way[landuse]({bb}););out geom;")
        for host in ("https://overpass-api.de", "https://overpass.kumi.systems"):
            try:
                download(f"{host}/api/interpreter?" + urllib.parse.urlencode({"data": q}), RAW / f"osm_{pid}.json")
                json.load(open(RAW / f"osm_{pid}.json"))
                break
            except Exception as ex:  # busy server: try the mirror
                print("  ", host, ex)
    print("OS Open UPRN (GB, ~620 MB, deleted after clipping)")
    tmp = RAW / "osopenuprn.zip"
    download("https://api.os.uk/downloads/v1/products/OpenUPRN/downloads?area=GB&format=CSV&redirect", tmp)
    z = zipfile.ZipFile(tmp)
    outs = {k: open(RAW / f"uprn_{k}.csv", "w") for k in BOXES}
    for f in outs.values():
        f.write("uprn,lon,lat\n")
    with z.open([m for m in z.namelist() if m.lower().endswith(".csv")][0]) as fh:
        import io
        r = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
        hdr = next(r)
        iu, ila, ilo = hdr.index("UPRN"), hdr.index("LATITUDE"), hdr.index("LONGITUDE")
        for row in r:
            lat, lon = float(row[ila]), float(row[ilo])
            for k, (w, s, e, n) in BOXES.items():
                if w <= lon <= e and s <= lat <= n:
                    outs[k].write(f"{row[iu]},{lon},{lat}\n")
    for f in outs.values():
        f.close()
    tmp.unlink()
    for pid, tile in OS_TILES.items():
        print(f"OS OpenMap Local buildings, tile {tile}")
        tmp = RAW / f"opmplc_{tile}.zip"
        download(f"https://api.os.uk/downloads/v1/products/OpenMapLocal/downloads?area={tile}&format=ESRI%C2%AE+Shapefile&redirect", tmp)
        json.dump(clip_buildings(tmp, tile, BOXES[pid]), open(RAW / f"osml_{pid}.json", "w"))
        tmp.unlink()


def clip_buildings(zip_path, tile, box):
    """Building polygons from an OS OpenMap Local shapefile zip, clipped to a lon/lat box, as BNG rings."""
    w, s, e, n = box
    xs, ys = zip(*[bng(x, y) for x, y in [(w, s), (w, n), (e, s), (e, n)]])
    X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)
    z = zipfile.ZipFile(zip_path)
    buf = z.read([m for m in z.namelist() if m.endswith(f"/{tile}_Building.shp")][0])
    pos, rings = 100, []
    while pos < len(buf):
        _, clen = struct.unpack(">ii", buf[pos:pos + 8])
        pos += 8
        rec = buf[pos:pos + clen * 2]
        pos += clen * 2
        if struct.unpack("<i", rec[:4])[0] != 5:  # polygons only
            continue
        bx0, by0, bx1, by1 = struct.unpack("<4d", rec[4:36])
        if bx1 < X0 or bx0 > X1 or by1 < Y0 or by0 > Y1:
            continue
        np_, npts = struct.unpack("<ii", rec[36:44])
        parts = list(struct.unpack(f"<{np_}i", rec[44:44 + 4 * np_])) + [npts]
        pts = struct.unpack(f"<{2 * npts}d", rec[44 + 4 * np_:44 + 4 * np_ + 16 * npts])
        xy = [(round(pts[2 * i], 1), round(pts[2 * i + 1], 1)) for i in range(npts)]
        rings.append([xy[parts[k]:parts[k + 1]] for k in range(np_)])
    return rings


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    customers = {}
    with open(RAW / "ssen_lct_primary.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            customers[r["PRIMARY_NAME"]] = int(r["TOTAL_CUSTOMERS"])
    index = []
    for P in PILOTS:
        print(P["name"])
        # the pilots cover (nearly) the whole primary, so calibrate homes to its customer count
        scale = 1.0
        for _ in range(4):
            homes, blocks, roads = load_homes(P["id"], P["rural"], scale)
            subs, feeders, homes, kind = (southall_network(homes) if P["id"] == "southall" else aberfeldy_network(homes, roads))
            target = customers[P["primary"]]
            if abs(len(homes) - target) < 0.02 * target or len(homes) < target and scale >= 1:
                break
            singles = sum(1 for h in homes if h["single"])
            scale = min(1.0, scale * max(0.05, (target - singles)) / max(1, len(homes) - singles))
        print(f"  homes calibrated to {len(homes)} (SSEN: {customers[P['primary']]} customers on the primary, scale {scale:.2f})")
        eligible = queue_ranks(homes, P["rural"])
        curves = dfes_curves(P["primary"], len(homes), eligible, customers[P["primary"]])
        n_os = max(max(c["os"]) for c in curves.values())
        sites = onstreet_sites(feeders, homes, n_os)

        # base (non-LCT) peak per feeder: the measured smart meter peak where SSEN publishes one, plus a typical home's
        # peak for the homes without smart meter data (measured peaks can include shops, so they aren't scaled up)
        per_home = [f["peak"] / f["meters"] for f in feeders if f["peak"] and f["meters"] and f["meters"] >= 10]
        if len(per_home) < 5:
            per_home += [s["peak"] / s["meters"] for s in subs if s["peak"] and s["meters"] and s["meters"] >= 10]
        default_admd = min(2.5, max(0.6, sorted(per_home)[len(per_home) // 2])) if per_home else 1.0
        n_by_feeder = collections.Counter(h["feeder"] for h in homes)
        n_by_sub0 = collections.Counter(feeders[h["feeder"]]["sub"] for h in homes)
        for fi, f in enumerate(feeders):
            n = n_by_feeder[fi]
            s = subs[f["sub"]]
            if f["peak"] and f["meters"]:
                base, src = f["peak"] + max(0, n - f["meters"]) * default_admd, "feeder smart meters"
            elif s["peak"] and s["meters"] and n_by_sub0[f["sub"]]:
                sub_base = s["peak"] + max(0, n_by_sub0[f["sub"]] - s["meters"]) * default_admd
                base, src = sub_base * n / n_by_sub0[f["sub"]], "substation smart meters"
            else:
                base, src = n * default_admd, "typical home"
            f["base"] = round(base, 1)
            f["admd"] = round(base / n, 2) if n else 0
            f["base_src"] = src
            f["homes"] = n
        # keep only feeders and substations that serve at least one home
        n_by_sub = collections.Counter(feeders[h["feeder"]]["sub"] for h in homes)
        old_ids = sorted(n_by_feeder)
        fmap = {old: new for new, old in enumerate(old_ids)}
        used_subs = sorted({feeders[fi]["sub"] for fi in old_ids})
        remap = {si: i for i, si in enumerate(used_subs)}
        feeders = [dict(feeders[fi], sub=remap[feeders[fi]["sub"]]) for fi in old_ids]
        out_subs = []
        for si in used_subs:
            s = subs[si]
            kva = transformer_size(s, n_by_sub[si])
            out_subs.append([*ll(s["x"], s["y"]), s["name"], kva, s["kind"], s["peak"], s["meters"], s["nrn"]])
        # blocks drawn under the homes (only those with a home kept)
        block_ids = sorted({h["block"] for h in homes})
        bmap = {b: i for i, b in enumerate(block_ids)}
        out_blocks = []
        for b in block_ids:
            g = blocks[b].simplify(0.8)
            g = g if g.geom_type == "Polygon" else max(g.geoms, key=lambda p: p.area)
            out_blocks.append([ll(*c) for c in g.exterior.coords])
        # estimated house plots inside terrace / semi blocks (party walls)
        out_houses = []
        for h in homes:
            if h.get("house") is not None:
                g = h["house"].simplify(0.3)
                out_houses.append([ll(*c) for c in g.exterior.coords])
        out_homes = [[*ll(h["x"], h["y"]), fmap[h["feeder"]], TYPES.index(h["type"]), int(h["offstreet"]),
                      h["r_hp"], h["r_ev"], h["r_pv"], h["r_bat"], bmap[h["block"]], h["r_de"]] for h in homes]
        sites = [[s[0], s[1], fmap[s[2]]] for s in sites if s[2] in fmap]
        lons = [h[0] for h in out_homes]
        lats = [h[1] for h in out_homes]
        doc = {
            "id": P["id"], "name": P["name"], "dno": "SSEN", "primary": P["primary"].title(), "la": P["la"],
            "network": kind, "note": P["note"], "rural": P["rural"], "years": YEARS,
            "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
            "primary_customers": customers[P["primary"]], "eligible": eligible, "curves": curves,
            "subs": out_subs, "feeders": feeders, "homes": out_homes, "blocks": out_blocks, "houses": out_houses, "onstreet": sites,
            "fields": {"homes": "lon, lat, feeder, type (0 detached, 1 semi, 2 terrace, 3 flat), off-street parking, "
                                "queue place 0-999 for heat pump, home charger, PV, battery (999 = never), block, "
                                "queue place for direct electric heating",
                       "subs": "lon, lat, name, assumed kVA, GMT/PMT, smart meter peak kW, meters, NRN"},
        }
        with open(OUT / f"{P['id']}.json", "w") as fh:
            json.dump(doc, fh, separators=(",", ":"))
        index.append({"id": P["id"], "name": P["name"], "network": kind, "bounds": doc["bounds"],
                      "centre": [round(sum(lats) / len(lats), 5), round(sum(lons) / len(lons), 5)],
                      "homes": len(out_homes), "feeders": len(feeders), "subs": len(out_subs), "la": P["la"],
                      "primary": doc["primary"]})
        print(f"  -> {len(out_homes)} homes, {len(feeders)} feeders, {len(out_subs)} substations, "
              f"{len(sites)} on-street charger spots; base ADMD median {default_admd:.2f} kW")
    json.dump({"pilots": index, "source": "SSEN DFES 2025 (Regen), SSEN LV network and smart meter open data, "
               "OS Open UPRN, OS OpenMap Local, OpenStreetMap"}, open(OUT / "index.json", "w"), indent=1)


if __name__ == "__main__":
    if "--fetch" in sys.argv:
        fetch_inputs()
    main()
