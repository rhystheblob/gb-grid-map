"""NGED (East Midlands, West Midlands, South Wales, South West): LTDS Capacity Heatmap, open JSON on
connecteddata.nationalgrid.co.uk. Points only (BSP + Primary); supply-area polygons need a portal login."""
from .common import ckan_package, get_json, point, site

NAME = "NGED"
TITLE = "National Grid Electricity Distribution"
LICENCE = "NGED (WPD) Open Data Licence"
SOURCE = "https://connecteddata.nationalgrid.co.uk/dataset/ltds-capacity-heatmap"

BASE = "https://connecteddata.nationalgrid.co.uk"
LEVEL = {"BSP": "bsp", "Primary": "primary", "GSP": "gsp"}


def fetch():
    out = []
    for res in ckan_package(BASE, "ltds-capacity-heatmap")["resources"]:
        if (res.get("format") or "").upper() != "JSON":
            continue
        for r in get_json(res["url"]):
            level = LEVEL.get(r.get("type"))
            if not level:
                continue
            out.append(site(
                level, r.get("name"), point(r.get("longitude"), r.get("latitude")),
                gsp=r.get("GSP"), bsp=r.get("BSP"),
                dfirm=r.get("demandFirmCapacity"), dmax=r.get("demandMaximum"),
                dhead=r.get("demandAvailableCapacity"), drag=r.get("demandConstraint"),
                dlim=r.get("demandConstraintLimitingFactor"),
                ghead=r.get("generationAvailableCapacity"), grag=r.get("generationConstraint"),
                glim=r.get("generationConstraintLimitingFactor"),
            ))
    return out
