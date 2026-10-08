# SSEN Grid Capacity Map

Interactive capacity map of SSEN's distribution network (GSP → BSP → Primary → Secondary → smart meter).

```
python3 fetch_public.py   # refresh public layers into data/
python3 serve.py          # http://localhost:8765
```

## Data sources

| Level | Source | Auth | Status |
|---|---|---|---|
| GSP areas (86) | ArcGIS `GSP_Jan_2026_Final/FeatureServer/131` | none | done (RAG only: SSEN publishes no numeric GSP headroom) |
| BSP areas (102) | ArcGIS `BSP_Jan_2026_Final/FeatureServer/130` | none | done |
| Primary areas (831) | ArcGIS `Primary_Jan_2026_Final/FeatureServer/129` | none | done |
| Substation points (1,035) | ArcGIS `Sub_datapoint__Jan_2026_Final/FeatureServer/128` | none | done |
| Secondary transformers + LV cables | ArcGIS `Secondary_Tx_Location`, `LV_Segment` | none | pilot area only (The Green primary, Southall) |
| Secondary supply areas, network-wide | CKAN `secondary-substation-esa` (SEPD + SHEPD 2025 GeoJSON) | portal API key | `fetch_portal.py` |
| Smart meter LV feeder usage | portal "Raw files" tab: daily CSVs, half-hourly per LV feeder | login (API needs SSEN verification) | manual download → `fetch_portal.py` |

ArcGIS base: `https://services8.arcgis.com/euGIHHXuAe1iLVSR/arcgis/rest/services` (SSEN's own org, behind their
"Centralised Network View"). Capacity fields come from SSEN's January 2026 publication. Re-check service names when
SSEN publishes a new edition (the `_Jan_2026_` suffix will change).

## Portal data (login required)

1. Put your portal API key in `.env` (copy the `apiKey` value from https://data.ssen.co.uk/api/auth/session
   while signed in):
   `SSEN_API_KEY=...`
2. Optionally download smart meter daily CSVs from the Smart Meter LV Feeder Data page → Raw files tab into
   `data/raw/smart_meter/`. Winter weekday evenings show peak load.
3. `python3 fetch_portal.py` downloads about 270 MB of secondary areas once, then writes
   `data/secondary/<primary NRN>.geojson` and `data/smart_meter_secondary.json`.

Smart meter rows join to secondary areas on the first 10 digits of `dataset_id` = `NRN_10_DIGIT`
(4 primary + 3 HV feeder + 3 secondary). The smart meter API (as opposed to manual file downloads) is restricted
to SSEN-verified users; request verification from data@ssen.co.uk to automate it.

## Weekly refresh

`run-weekly.sh` re-pulls the public layers, re-downloads secondary areas only if SSEN republished them, and
recomputes smart meter peaks from the newest 7 daily CSVs. Logs go to `logs/weekly-YYYY-MM-DD.log`.

Schedule it (Mondays 07:00 local) with:
```
cp com.rhys.ssen-grid-map.weekly.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rhys.ssen-grid-map.weekly.plist
```
Until SSEN grants verified API access, the week's smart meter CSVs still have to be downloaded from the portal by
hand before Monday's run.
