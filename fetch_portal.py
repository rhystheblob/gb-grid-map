"""Pull login-only SSEN data from the data portal's CKAN API (data-api.ssen.co.uk).

Needs your personal portal API key in ssen-grid-map/.env:
    SSEN_API_KEY=...

Steps:
  1. Secondary substation electricity supply areas (SEPD + SHEPD, latest year) are
     downloaded, simplified, and split into data/secondary/<primary NRN>.geojson so the
     map can load one primary's secondaries at a time.
     Skipped when SSEN hasn't republished since the last run (data/manifest.json).
  2. The newest SMART_METER_DAYS daily smart meter CSVs in smart_meter_uploads/ (from the
     portal's "Raw files" tab) are aggregated to peak load per secondary substation and
     per LV feeder -> data/smart_meter_secondary.json.

The smart meter *API* is restricted to SSEN-verified users, so until access is granted
those files are downloaded by hand from the portal.

Usage:  python3 fetch_portal.py [--skip-secondary] [--skip-smart-meter] [--force]
"""
import collections
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"
API = "https://data-api.ssen.co.uk/api/3/action"
SECONDARY_DATASET = "secondary-substation-esa"
KEEP_PROPS = ["NRN_10_DIGIT", "TX_NRN", "HV_FEEDER_NRN", "PRIMARY_NRN_SPLIT",
              "PRIMARY_NAME_2025", "BSP_NAME", "GSP_NAME"]
SIMPLIFY_DEG = 0.00008  # ~5-9 m; secondary areas are street-scale
SMART_METER_DAYS = 7    # rolling window: the weekly run reports the past week's peak
MANIFEST = DATA / "manifest.json"  # committed, so cloud runs know what was last published
SMART_METER_DIR = ROOT / "smart_meter_uploads"


def api_key():
    if os.environ.get("SSEN_API_KEY"):  # cloud runs pass the key as a secret
        return os.environ["SSEN_API_KEY"].strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "SSEN_API_KEY" and v.strip():
                return v.strip().strip("'\"")
    sys.exit("SSEN_API_KEY not set (environment variable or ssen-grid-map/.env, see README).")


def request(url, key):
    return urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "ssen-grid-map/1.0"})


def ckan(action, key, **params):
    url = f"{API}/{action}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(request(url, key), timeout=120) as r:
        body = json.load(r)
    if not body.get("success"):
        raise RuntimeError(body.get("error"))
    return body["result"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def signed_url(url, key):
    """Resource downloads 302 to a pre-signed Cloudflare R2 URL, which rejects requests that
    still carry the Authorization header, so resolve the redirect ourselves."""
    try:
        urllib.request.build_opener(_NoRedirect).open(request(url, key), timeout=120).close()
        return url
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
            return e.headers["Location"]
        raise


def download(url, dest, key):
    if dest.exists():
        print(f"  cached {dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    target = signed_url(url, key)
    req = urllib.request.Request(target, headers={"User-Agent": "ssen-grid-map/1.0"})
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {dest.name}: {done / 1e6:6.0f} / {total / 1e6:.0f} MB", end="", flush=True)
    tmp.rename(dest)
    print()


def latest_geojson_resources(key):
    """Newest-year GeoJSON resource for each licence area (SEPD, SHEPD)."""
    best = {}
    for res in ckan("package_show", key, id=SECONDARY_DATASET)["resources"]:
        if (res.get("format") or "").lower() != "geojson":
            continue
        name = res["name"].strip()
        area = "SHEPD" if "SHEPD" in name.upper() else "SEPD"
        year = int(max(re.findall(r"20\d\d", name), default="0"))
        if year > best.get(area, (0, None))[0]:
            best[area] = (year, res)
    return {area: res for area, (year, res) in best.items()}


def build_secondary(key, force=False):
    from shapely.geometry import mapping, shape

    out_dir = DATA / "secondary"
    resources = latest_geojson_resources(key)
    current = {area: {"id": r["id"], "modified": r.get("last_modified") or r.get("metadata_modified")}
               for area, r in resources.items()}
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    if not force and manifest.get("secondary") == current and (out_dir / "index.json").exists():
        print("secondary: unchanged since last run - skipping")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    by_primary = collections.defaultdict(list)
    for area, res in resources.items():
        print(f"{area}: {res['name'].strip()}")
        src = RAW / f"secondary_{area.lower()}.geojson"
        previous = manifest.get("secondary", {}).get(area)
        if previous and previous != current[area]:
            src.unlink(missing_ok=True)  # republished: fetch the new file
        download(res["url"], src, key)
        print("  simplifying…")
        for feat in json.loads(src.read_text())["features"]:
            props = feat["properties"]
            geom = shape(feat["geometry"]).simplify(SIMPLIFY_DEG, preserve_topology=True)
            if geom.is_empty:
                continue
            gj = json.loads(json.dumps(mapping(geom)), parse_float=lambda x: round(float(x), 5))
            p = {k: props.get(k) for k in KEEP_PROPS}
            p["LICENCE_AREA"] = area
            # stored as a number upstream; restore leading zeros so it matches smart meter dataset_id[:10]
            p["NRN_10_DIGIT"] = str(p["NRN_10_DIGIT"]).zfill(10)
            by_primary[str(props["PRIMARY_NRN_SPLIT"])].append({"type": "Feature", "properties": p, "geometry": gj})

    for old in out_dir.glob("*.geojson"):
        old.unlink()
    index = {}  # primary NRN -> file stem (NRNs can contain "/", e.g. "947-T1/T2")
    for nrn, feats in by_primary.items():
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", nrn)
        (out_dir / f"{stem}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
        index[nrn] = stem
    (out_dir / "index.json").write_text(json.dumps(index))
    MANIFEST.write_text(json.dumps({**manifest, "secondary": current}, indent=1))
    print(f"secondary: {sum(map(len, by_primary.values()))} areas across {len(index)} primaries -> {out_dir}")


def build_smart_meter():
    import pandas as pd

    # daily files are named by date (e.g. 2026-09-28.csv), so a name sort is chronological
    files = sorted(SMART_METER_DIR.glob("*.csv"))[-SMART_METER_DAYS:]
    if not files:
        print("smart meter: no CSVs in smart_meter_uploads/ - skipping")
        return
    cols = ["dataset_id", "secondary_substation_name", "lv_feeder_name", "aggregated_device_count_active",
            "total_consumption_active_import", "data_collection_log_timestamp"]
    sec_ts = collections.Counter()           # (nrn10, ts) -> Wh, for coincident secondary peak
    feeders = {}                             # dataset_id -> stats
    for f in files:
        print(f"smart meter: reading {f.name}")
        for chunk in pd.read_csv(f, usecols=lambda c: c in cols, dtype={"dataset_id": str}, chunksize=500_000):
            chunk["nrn10"] = chunk["dataset_id"].str[:10]
            for (n, ts), wh in chunk.groupby(["nrn10", "data_collection_log_timestamp"])["total_consumption_active_import"].sum().items():
                sec_ts[(n, ts)] += wh
            g = chunk.groupby("dataset_id").agg(
                peak=("total_consumption_active_import", "max"), total=("total_consumption_active_import", "sum"),
                rows=("total_consumption_active_import", "size"), meters=("aggregated_device_count_active", "max"),
                name=("lv_feeder_name", "first"), sub=("secondary_substation_name", "first"))
            for did, r in g.iterrows():
                s = feeders.setdefault(did, {"peak": 0, "total": 0, "rows": 0, "meters": 0, "name": r["name"], "sub": r["sub"]})
                s["peak"] = max(s["peak"], r["peak"]); s["total"] += r["total"]; s["rows"] += r["rows"]
                s["meters"] = max(s["meters"], r["meters"])

    secondary = {}
    for (n, ts), wh in sec_ts.items():
        kw = wh * 2 / 1000  # Wh per half hour -> average kW over the half hour
        s = secondary.setdefault(n, {"peak_kw": 0, "peak_at": None, "meters": 0, "feeders": []})
        if kw > s["peak_kw"]:
            s["peak_kw"], s["peak_at"] = round(kw, 1), ts
    for did, f in feeders.items():
        s = secondary.setdefault(did[:10], {"peak_kw": 0, "peak_at": None, "meters": 0, "feeders": []})
        s["name"] = f["sub"]
        s["meters"] += int(f["meters"])
        s["feeders"].append({"id": did, "name": f["name"], "meters": int(f["meters"]),
                             "peak_kw": round(f["peak"] * 2 / 1000, 1),
                             "avg_kw": round(f["total"] * 2 / 1000 / max(f["rows"], 1), 1)})
    meta = {"files": [f.name for f in files], "unit": "kW", "secondaries": secondary}
    (DATA / "smart_meter_secondary.json").write_text(json.dumps(meta, separators=(",", ":")))
    print(f"smart meter: {len(feeders)} LV feeders across {len(secondary)} secondary substations")


def main():
    if "--skip-secondary" not in sys.argv:
        build_secondary(api_key(), force="--force" in sys.argv)
    if "--skip-smart-meter" not in sys.argv:
        build_smart_meter()


if __name__ == "__main__":
    main()
