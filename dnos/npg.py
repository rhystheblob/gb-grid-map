"""Northern Powergrid (Northeast + Yorkshire): "Heat Map Data - Substation Areas", open on Opendatasoft,
with supply-area polygons for GSP, BSP and Primary."""
from .common import ods_geojson, site

NAME = "NPg"
TITLE = "Northern Powergrid"
LICENCE = "Northern Powergrid Open Data Licence v1.0"
SOURCE = "https://northernpowergrid.opendatasoft.com/explore/dataset/heatmapsubstationareas/"

LEVEL = {"GSP": "gsp", "BSP": "bsp", "Primary": "primary"}


def fetch():
    out = []
    for f in ods_geojson("northernpowergrid.opendatasoft.com", "heatmapsubstationareas")["features"]:
        p = f["properties"]
        level = LEVEL.get(p.get("type"))
        if not level:
            continue
        # upstreamname is the next site up: a BSP's upstream is its GSP, a primary's is its BSP
        upstream = p.get("upstreamname")
        out.append(site(
            level, p.get("name"), f["geometry"],
            gsp=p.get("gsp_name"), bsp=upstream if level == "primary" else None,
            dfirm=p.get("firm_cap"), dmax=p.get("maxdemand"),
            dhead=p.get("demhr"), drag=p.get("worst_case_constraint_dem_colour"), dlim=p.get("demconstraint"),
            ghead=p.get("genhr"), grag=p.get("worst_case_constraint_gen_colour"), glim=p.get("genconstraint"),
        ))
    return out
