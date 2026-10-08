# SSEN Grid Capacity Map

Interactive capacity map of SSEN's distribution network (GSP → BSP → Primary → Secondary → smart meter).

```
python3 fetch_public.py   # refresh public layers into data/
python3 serve.py          # http://localhost:8765 (or use the hosted map below)
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
| Smart meter LV feeder usage | portal "Raw files" tab: daily CSVs (~800 MB), half-hourly per LV feeder | login (API needs SSEN verification) | done for 14 + 21 Jan 2026 (local refresh) |

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

## Hosting and weekly refresh

The map is served by GitHub Pages from `main`: https://rhystheblob.github.io/ssen-grid-map/

A Claude cloud routine ("SSEN grid map weekly refresh", Mondays 06:00 UTC) checks out this repo, runs
`fetch_public.py` and `fetch_portal.py`, sanity-checks feature counts, and commits changed `data/` files, which
republishes the map. `SSEN_API_KEY` is set as an environment variable on the cloud environment, never in the repo.
Manage the routine at https://claude.ai/code/routines/trig_01YYGzTxvU8BEPKmTShbzBgo

Smart meter data is refreshed locally, not by the cloud routine: SSEN's daily raw files are ~800 MB each (too big
for git, and the API needs SSEN verification). Download days from the portal's Raw files tab into
`smart_meter_uploads/` (git-ignored), run `python3 fetch_portal.py --skip-secondary`, and commit
`data/smart_meter_secondary.json`. Files outside the newest-7 window are deleted automatically. Raw files lag
~6 weeks; winter weekdays give the most useful peak. Current data: 14 and 21 Jan 2026 (highest peak of the two).

`run-weekly.sh` runs the same refresh locally (it needs `.env` or `SSEN_API_KEY`).
