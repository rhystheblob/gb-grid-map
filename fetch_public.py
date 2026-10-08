"""Pull SSEN network capacity layers from SSEN's public ArcGIS feature services.

These are the services behind SSEN's "Centralised Network View"
(https://experience.arcgis.com/experience/763db55663e845c7b6697995a32c32b5),
so no login is needed. Output is GeoJSON (WGS84) in ./data/.

Usage:  python3 fetch_public.py
"""
import json
import pathlib
import urllib.parse
import urllib.request

BASE = "https://services8.arcgis.com/euGIHHXuAe1iLVSR/arcgis/rest/services"
OUT = pathlib.Path(__file__).parent / "data"

# name -> (service path, simplification tolerance in degrees; None = points)
LAYERS = {
    "gsp": ("GSP_Jan_2026_Final/FeatureServer/131", 0.001),
    "bsp": ("BSP_Jan_2026_Final/FeatureServer/130", 0.0008),
    "primary": ("Primary_Jan_2026_Final/FeatureServer/129", 0.0006),
    "substations": ("Sub_datapoint__Jan_2026_Final/FeatureServer/128", None),
    "licence_area": ("SSEN_Boundary/FeatureServer/56", 0.01),
    # Secondary / LV detail is only published publicly for a pilot area.
    "secondary_pilot": ("Secondary_Tx_Location/FeatureServer/0", None),
    "lv_pilot": ("LV_Segment/FeatureServer/0", 0.00002),
}


def get_json(url, params):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(full, timeout=120) as r:
        return json.load(r)


def fetch_layer(path, tolerance):
    url = f"{BASE}/{path}/query"
    params = {
        "where": "1=1",
        "outFields": "*",
        "outSR": 4326,
        "geometryPrecision": 6,
        "f": "geojson",
        "resultRecordCount": 1000,
    }
    if tolerance:
        params["maxAllowableOffset"] = tolerance
    features, offset = [], 0
    while True:
        page = get_json(url, {**params, "resultOffset": offset})
        batch = page.get("features", [])
        features += batch
        if not batch or not page.get("properties", {}).get("exceededTransferLimit"):
            break
        offset += len(batch)
    return {"type": "FeatureCollection", "features": features}


def main():
    OUT.mkdir(exist_ok=True)
    for name, (path, tol) in LAYERS.items():
        fc = fetch_layer(path, tol)
        dest = OUT / f"{name}.geojson"
        dest.write_text(json.dumps(fc, separators=(",", ":")))
        print(f"{name:16s} {len(fc['features']):5d} features  {dest.stat().st_size / 1e6:6.2f} MB")


if __name__ == "__main__":
    main()
