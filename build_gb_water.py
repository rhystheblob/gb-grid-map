"""GB water layer for dig-once planning.

Water companies don't publish their pipe networks openly (security; the authoritative register of buried pipes is
NUAR, which is invitation-only, mainly for asset owners and local/highways authorities). What is open, via the
industry's Stream portal (streamwaterdata.co.uk, ArcGIS Hub, public services, no login), and useful for dig-once:

    Water company areas         combined supply boundaries incl. inset networks (Stream "Water Company Boundaries")
    Storm overflow improvements National Storm Overflows Plan for England: every overflow with its planned
                                improvement year (new storage, larger sewers = excavation) and spill history

Outputs:  data/gb/water/areas.geojson, data/gb/water/overflows.geojson
Usage:    python3 build_gb_water.py
"""
import json
import urllib.parse
import urllib.request

from dnos.common import ROOT

OUT = ROOT / "data" / "gb" / "water"
BOUNDARIES = "https://services-eu1.arcgis.com/XxS6FebPX29TRGDJ/arcgis/rest/services/UC2/FeatureServer/0"
NSOP = "https://services-eu1.arcgis.com/XxS6FebPX29TRGDJ/arcgis/rest/services/National_Plan_Data_/FeatureServer"
UA = {"User-Agent": "gb-grid-map/1.0 (+https://github.com/rhystheblob/gb-grid-map)"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
        return json.load(r)


def query_all(layer, fmt="geojson", **params):
    """All rows of an ArcGIS layer/table, paging past maxRecordCount."""
    rows, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "f": fmt, "resultOffset": offset,
                                    "resultRecordCount": 2000, **params})
        page = get(f"{layer}/query?{q}")
        batch = page.get("features", [])
        rows += batch
        more = page.get("exceededTransferLimit") or page.get("properties", {}).get("exceededTransferLimit")
        if not batch or (not more and len(batch) < 2000):
            return rows
        offset += len(batch)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def year(v):
    n = num(v)
    return int(n) if n and 1990 < n < 2100 else None


def areas():
    from shapely.geometry import mapping, shape
    out = []
    for f in query_all(BOUNDARIES, outSR=4326):
        if not f.get("geometry"):
            continue
        p = f["properties"]
        g = shape(f["geometry"]).simplify(0.0005, preserve_topology=True)
        out.append({"type": "Feature",
                    "properties": {"company": p.get("COMPANY"), "area": p.get("AreaServed"), "code": p.get("Acronym")},
                    "geometry": json.loads(json.dumps(mapping(g)), parse_float=lambda x: round(float(x), 4))})
    return out


def overflows():
    svc = get(f"{NSOP}?f=json")
    table = (svc.get("tables") or svc.get("layers"))[0]["id"]
    out = []
    for f in query_all(f"{NSOP}/{table}", fmt="json"):
        a = f["attributes"]
        lon, lat = num(a.get("Longitude")), num(a.get("Latitude"))
        if lon is None or lat is None:
            continue
        spills = {y: num(a.get(f"Number_of_Reported_Spills__EDM____{y}")) for y in (2020, 2021, 2022)}
        meets_now = a.get("Does_this_overflow_already_meet_all_applicable_targets_in_the_Storm_Overflows_Discharge_Reduction_Plan_") == "Y"
        planned = year(a.get("Spill_improvements_planned_"))
        props = {
            "id": a.get("New_Unique_ID") or a.get("Unique_ID"), "company": a.get("Water_company"),
            "site": a.get("EA_site_name"), "la": a.get("Local_Authority"), "water": a.get("Water_Body"),
            "planned": None if meets_now else planned, "meets": meets_now,
            "works": (a.get("Improvements_List") or "").strip() or None,
            "spills": {str(k): int(v) for k, v in spills.items() if v is not None},
            "after": num(a.get("Predicted_average_annual_spill_frequency_following_scheme_implementation_where_applicable")),
        }
        out.append({"type": "Feature", "properties": {k: v for k, v in props.items() if v not in (None, {}, "")},
                    "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]}})
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    a = areas()
    (OUT / "areas.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": a}, separators=(",", ":")))
    o = overflows()
    (OUT / "overflows.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": o}, separators=(",", ":")))
    by = {}
    for f in o:
        y = f["properties"].get("planned")
        k = "no works needed" if f["properties"].get("meets") else "unknown" if not y else "by 2030" if y <= 2030 else "2031-2035" if y <= 2035 else "2036-2050"
        by[k] = by.get(k, 0) + 1
    print(f"water areas: {len(a)} | storm overflows: {len(o)} {by}")


if __name__ == "__main__":
    main()
