"""Electricity North West (SP Electricity North West). Needs a portal login: live with ENWL_API_KEY, else the
browser-exported snapshot. GSP/BSP/Primary heatmaps carry supply-area polygons and headroom, but ENWL publishes
neither substation names (only numbers) nor a RAG rating, so the map derives a RAG from headroom and says so."""
from .common import portal_data, site

NAME = "ENWL"
TITLE = "Electricity North West"
LICENCE = "CC BY 4.0"
SOURCE = "https://electricitynorthwest.opendatasoft.com/"

HOST, KEY, BUNDLE = "electricitynorthwest.opendatasoft.com", "ENWL_API_KEY", "enwl"
NOTE = ("ENWL publishes headroom (in MW) but no RAG rating or substation names; the RAG shown is derived by this map "
        "(red: no headroom, amber: under 2 MW)")


def _rag(headroom):
    if headroom is None:
        return None
    return "R" if headroom <= 0 else "A" if headroom < 2 else "G"


def fetch():
    get = lambda ds: portal_data(HOST, ds, KEY, BUNDLE)["features"]
    bsp_gsp = {f["properties"]["bsp_number"]: f["properties"]["gsp_number"] for f in get("enwl-substation-bsp-polygons")}
    pry_bsp = {f["properties"]["pry_number"]: f["properties"]["bsp_number"] for f in get("enwl-substation-primary-polygons")}
    out = []
    for f in get("enwl-gsp-heatmap"):
        n = f["properties"].get("gsp_number")
        out.append(site("gsp", f"GSP {n}", f["geometry"], id=n))
    for level, ds, key, parent in (("bsp", "enwl-bsp-heatmap", "bsp_number", bsp_gsp),
                                   ("primary", "enwl-pry-heatmap", "pry_number", pry_bsp)):
        for f in get(ds):
            p = f["properties"]
            n, dh, gh = p.get(key), p.get("dem_hr_firm_mw"), p.get("gen_hr_inverter_mw")
            out.append(site(
                level, f"{'BSP' if level == 'bsp' else 'Primary'} {n}", f["geometry"],
                dhead=dh, drag=_rag(dh), ghead=gh, grag=_rag(gh),
                dlim="firm demand headroom", glim="inverter-connected generation headroom",
                id=n, pid=parent.get(n), note=NOTE,
            ))
    return out
