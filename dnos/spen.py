"""SP Energy Networks (SP Distribution: central & southern Scotland; SP Manweb: Merseyside, Cheshire, N Wales).
The LTDS Capacity Heatmaps need a portal login (SPEN_API_KEY, else the browser-exported snapshot); the GSP and
primary supply-area polygons are open."""
from .common import merge_points_into_areas, ods_geojson, point, portal_data, site

NAME = "SPEN"
TITLE = "SP Energy Networks"
LICENCE = "CC BY 4.0"
SOURCE = "https://spenergynetworks.opendatasoft.com/"

HOST, KEY, BUNDLE = "spenergynetworks.opendatasoft.com", "SPEN_API_KEY", "spen"


def fetch():
    out, areas, points = [], [], []
    for lic in ("spd", "spm"):
        for f in ods_geojson(HOST, f"{lic}-dfes-substation-polygons-gsp")["features"]:
            g = f["properties"].get("gsp")
            out.append(site("gsp", g, f["geometry"], id=g))
        for f in ods_geojson(HOST, f"{lic}-dfes-substation-polygons-pss")["features"]:
            p = f["properties"]
            areas.append(site("primary", p.get("primary_su"), f["geometry"], gsp=p.get("gsp"), pid=p.get("gsp")))
        for r in portal_data(HOST, f"distribution-capacity-heatmaps-{lic}", KEY, BUNDLE, "json"):
            level = "bsp" if r.get("type") == "BSP" else "primary"
            pt = point(r.get("lon", r.get("longitude")), r.get("lat", r.get("latitude")))
            if not pt:
                continue
            points.append(site(
                level, r.get("name"), pt, gsp=r.get("gsp") or r.get("grid_supply_point"), bsp=r.get("bsp"),
                dfirm=r.get("demandfirmcapacity"), dmax=r.get("demandmaximum"), dhead=r.get("demandavailablecapacity"),
                drag=r.get("demandconstraint"), dlim=r.get("demandconstraintlimitingfactor"),
                ghead=r.get("generationavailablecapacity"), grag=r.get("generationconstraint"),
                glim=r.get("generationconstraintlimitingfactor"),
            ))
    bsps = [p for p in points if p["properties"]["level"] == "bsp"]
    prims = [p for p in points if p["properties"]["level"] == "primary"]
    return out + bsps + merge_points_into_areas(areas, prims)
