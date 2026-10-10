"""SSEN (SEPD + SHEPD): reuses the layers fetch_public.py pulls from SSEN's public ArcGIS services."""
import json

from .common import ROOT, site

NAME = "SSEN"
TITLE = "Scottish and Southern Electricity Networks"
LICENCE = "SSEN open data (CC BY 4.0)"
SOURCE = "https://data.ssen.co.uk/"

NAME_FIELD = {"gsp": "GSP_NAME", "bsp": "BSP_NAME", "primary": "PRIMARY_NAME_2025"}
ID_FIELD = {"gsp": "GSP_ID", "bsp": "BSP_ID", "primary": "PRIMARY_ID"}


def fetch():
    out = []
    for level, name_field in NAME_FIELD.items():
        path = ROOT / "data" / f"{level}.geojson"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing: run fetch_public.py first")
        for f in json.loads(path.read_text())["features"]:
            p = f["properties"]
            out.append(site(
                level, p.get(name_field) or p.get("Substation"), f["geometry"],
                gsp=p.get("Upstream_GSP"), bsp=p.get("Upstream_BSP"),
                dmax=p.get("Maximum_Observed_Gross_Demand__MVA_"),
                dhead=p.get("Estimated_Demand_Headroom__MVA_"), drag=p.get("Substation_Demand_RAG_Status"),
                dlim=p.get("Demand_Constraint"),
                ghead=p.get("Estimated_Generation_Headroom__MW_"), grag=p.get("Substation_Generation_RAG_Status"),
                glim=p.get("Generation_Constraint"),
                nrn=p.get("PRIMARY_NRN_SPLIT") if level == "primary" else None,
                id=p.get(ID_FIELD[level]),
                # SSEN publishes the hierarchy as ids; a primary with no BSP hangs off its GSP
                pid=None if level == "gsp" else (p.get("Up_BSP_ID") if level == "primary" and p.get("Up_BSP_ID")
                                                 else p.get("Up_GSP_ID")),
            ))
    return out
