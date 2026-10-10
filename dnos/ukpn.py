"""UK Power Networks (EPN, LPN, SPN). Needs a portal login: live with UKPN_API_KEY, else the browser-exported
snapshot. GSP and Grid (BSP-tier) supply areas, primary supply areas coloured by the LTDS Capacity Heatmap."""
from .common import merge_points_into_areas, point, portal_data, site

NAME = "UKPN"
TITLE = "UK Power Networks"
LICENCE = "CC BY 4.0"
SOURCE = "https://ukpowernetworks.opendatasoft.com/"

HOST, KEY, BUNDLE = "ukpowernetworks.opendatasoft.com", "UKPN_API_KEY", "ukpn"


def _headroom(firm, demand):
    return firm - demand if firm is not None and demand is not None else None


def fetch():
    get = lambda ds, fmt="geojson": portal_data(HOST, ds, KEY, BUNDLE, fmt)
    out = []
    for f in get("ukpn-grid-supply-points")["features"]:
        p = f["properties"]
        out.append(site("gsp", p.get("grid_supply_point"), f["geometry"], id=p.get("grid_supply_point_floc")))
    for f in get("ukpn-grid-postcode-area")["features"]:
        p = f["properties"]
        firm, dem = p.get("firmcapacitywinter"), p.get("demand")
        out.append(site(
            "bsp", p.get("grid_site"), f["geometry"], gsp=p.get("grid_supply_point"),
            dfirm=firm, dmax=dem, dhead=_headroom(firm, dem), drag=p.get("demandrag"),
            dlim=f"{p['seasonofconstraint']} peak" if p.get("seasonofconstraint") else None,
            id=p.get("grid_site_floc"), pid=p.get("grid_supply_point_floc"),
        ))
    areas = []
    for f in get("ukpn_primary_postcode_area")["features"]:
        p = f["properties"]
        firm, dem = p.get("firmcapacitywinter"), p.get("demand")
        areas.append(site(
            "primary", p.get("primary"), f["geometry"], gsp=p.get("grid_supply_point"), bsp=p.get("grid_site"),
            dfirm=firm, dmax=dem, dhead=_headroom(firm, dem), drag=p.get("demandrag"),
            ghead=p.get("generationavailablecapacity"), grag=p.get("generationrag"),
            id=p.get("primary_floc"), pid=p.get("grid_site_floc"),
        ))
    # the LTDS heatmap is the cross-DNO standard, so its figures take precedence inside each primary area
    points = [site(
        "primary", r.get("name"), point(r.get("longitude"), r.get("latitude")), gsp=r.get("gsp"), bsp=r.get("bsp"),
        dfirm=r.get("demandfirmcapacity"), dmax=r.get("demandmaximum"), dhead=r.get("demandavailablecapacity"),
        drag=r.get("demandconstraint"), dlim=r.get("demandconstraintlimitingfactor"),
        ghead=r.get("generationavailablecapacity"), grag=r.get("generationconstraint"),
        glim=r.get("generationconstraintlimitingfactor"),
    ) for r in get("ukpn-capacity-heatmap", "json")]
    return out + merge_points_into_areas(areas, [p for p in points if p["geometry"]])
