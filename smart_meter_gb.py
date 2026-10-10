"""Peak smart-meter load per secondary substation for the non-SSEN DNOs, on the same days used for SSEN
(SMART_METER_DAYS), so operators are compared under the same weather.

All DNOs publish Ofgem's standard aggregated smart meter schema (half-hourly Wh per secondary substation or LV
feeder, with the number of meters). Output: data/raw/smart/<DNO>.json = {substation id: [peak kW, meters, peak at]},
which build_gb_secondary.py joins onto the secondaries (optionally [..., lon, lat] for a location fallback).

UKPN: full history is in UKPN's Azure Blob container, opened with the public read-only SAS token UKPN publishes in
"Smart Meter Consumption Data Azure Blob Storage.pdf" (dataset attachments). Files are named by publication month
(the "February_2026" file holds January readings) and run to several GB, so they are streamed, never saved.

Usage:  UKPN_SAS='?sv=...' python3 smart_meter_gb.py ukpn      (or put UKPN_SAS in .env)
"""
import concurrent.futures
import csv
import io
import json
import sys
import urllib.request
import zipfile

from dnos.common import ROOT, api_key

SMART_METER_DAYS = ("2026-01-14", "2026-01-21")  # same winter Wednesdays as the SSEN layer
MAX_WH_PER_METER_HH = 12_000                     # same physical-limit filter as SSEN (fetch_portal.py)
OUT = ROOT / "data" / "raw" / "smart"

NPG_DAYS = ("15/01/2025", "22/01/2025")          # NPg's files stop at July 2025: same weekdays a year earlier
UKPN_BLOB = "https://ukpnoppublicdata001.blob.core.windows.net/odp"
UKPN_FILES = [f"{area}/2025/Substation/scpp_ss_fw_active_reactive_February_2026_final_Substation_{area}.csv"
              for area in ("EPN", "LPN", "SPN")]


def peaks_from_lines(lines, id_col, ts_col, meters_col, wh_col):
    """Peak half-hour kW per substation from standard-schema CSV lines, keeping only SMART_METER_DAYS."""
    peaks = {}
    for line in lines:
        if not any(day in line for day in SMART_METER_DAYS):  # cheap filter before splitting
            continue
        f = line.rstrip("\r\n").split(",")
        try:
            wh, meters = float(f[wh_col]), int(float(f[meters_col]))
        except (ValueError, IndexError):
            continue
        if meters <= 0 or wh > meters * MAX_WH_PER_METER_HH:
            continue
        kw = wh * 2 / 1000  # Wh in a half hour -> average kW
        sid = f[id_col]
        if sid not in peaks or kw > peaks[sid][0]:
            peaks[sid] = [round(kw, 1), meters, f[ts_col][:16]]
    return peaks


def ukpn_file(path, sas):
    url = f"{UKPN_BLOB}/{path}{sas}"
    with urllib.request.urlopen(url, timeout=600) as r:
        text = io.TextIOWrapper(r, encoding="utf-8", newline="")
        header = next(text).rstrip("\r\n").split(",")
        cols = {c: i for i, c in enumerate(header)}
        peaks = peaks_from_lines(text, cols["secondary_substation_id"], cols["data_collection_log_timestamp"],
                                 cols["aggregated_device_count_active"], cols["total_consumption_active_import"])
    print(f"  {path.split('/')[-1]}: {len(peaks)} substations", flush=True)
    return peaks


def ukpn():
    sas = api_key("UKPN_SAS")
    if not sas:
        sys.exit("UKPN_SAS not set: copy the SAS token from UKPN's 'Smart Meter Consumption Data Azure Blob "
                 "Storage.pdf' (it starts '?sv=') into .env as UKPN_SAS=...")
    merged = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for peaks in pool.map(lambda p: ukpn_file(p, sas), UKPN_FILES):
            merged.update(peaks)
    return merged


def npg():
    """NPg publishes monthly zips (login; exported via the owner's browser) that stop at July 2025, so its nearest
    winter is January 2025: the matching Wednesdays a year earlier. Ids are NPg SPIDs; rows carry lat/lon."""
    days = NPG_DAYS
    peaks = {}
    with zipfile.ZipFile(ROOT / "data" / "raw" / "portals" / "npg_substation_january2025.zip") as z:
        with z.open(z.namelist()[0]) as fh:
            for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")):
                ts = r["data_collection_log_timestamp"]
                if not ts.startswith(days):
                    continue
                try:
                    wh, meters = float(r["total_consumption_active_import"]), int(r["aggregated_device_count_active"])
                except ValueError:
                    continue
                if meters <= 0 or wh > meters * MAX_WH_PER_METER_HH:
                    continue
                kw, sid = round(wh * 2 / 1000, 1), r["secondary_substation_id"]
                if sid not in peaks or kw > peaks[sid][0]:
                    lat, _, lon = (r.get("substation_geo_location") or "").partition(",")
                    d, m, y = ts[:10].split("/")
                    peaks[sid] = [kw, meters, f"{y}-{m}-{d} {ts[11:16] or '00:00'}", lon.strip(), lat.strip()]
    return peaks


READERS = {"UKPN": ukpn, "NPg": npg}
DAYS = {"NPg": ("2025-01-15", "2025-01-22")}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name in sys.argv[1:] or list(READERS):
        dno = next(k for k in READERS if k.lower() == name.lower())
        peaks = READERS[dno]()
        (OUT / f"{dno}.json").write_text(json.dumps({"days": DAYS.get(dno, SMART_METER_DAYS), "peaks": peaks}, separators=(",", ":")))
        print(f"{dno}: {len(peaks)} secondary substations with smart meter peaks")


if __name__ == "__main__":
    main()
