# GB Grid Capacity Map

Interactive capacity map of Great Britain's electricity distribution networks, built from each network operator's
(DNO's) open data: GSP → BSP → Primary for every operator, plus Secondary → smart meter where available.

Live map: https://rhystheblob.github.io/gb-grid-map/

```
python3 fetch_public.py   # SSEN layers (public ArcGIS)
python3 build_gb.py       # all DNOs -> data/gb/ (common schema, see dnos/common.py)
python3 serve.py          # http://localhost:8765
```

## GB operators

| DNO | Licence areas | Source | Access | GSP / BSP / Primary | Geometry |
|---|---|---|---|---|---|
| SSEN | North Scotland, Southern England | data.ssen.co.uk + SSEN ArcGIS | open (`SSEN_API_KEY` for secondary) | 86 / 102 / 831 | supply areas |
| NGED | E Midlands, W Midlands, S Wales, SW England | `ltds-capacity-heatmap` (CKAN) | open | – / 242 / 940 | site points |
| Northern Powergrid | North East, Yorkshire | `heatmapsubstationareas` | open | 41 / 100 / 542 | supply areas |
| UKPN | London, East, South East | GSP/grid/primary areas + `ukpn-capacity-heatmap` | login (`UKPN_API_KEY` or snapshot) | 58 / 177 / 785 | supply areas |
| ENWL | North West | `enwl-{gsp,bsp,pry}-heatmap` + hierarchy polygons | login (`ENWL_API_KEY` or snapshot) | 20 / 70 / 377 | supply areas, numbers only |
| SPEN | Central/South Scotland, Merseyside & N Wales | `distribution-capacity-heatmaps-{spd,spm}` + open polygons | login for heatmaps (`SPEN_API_KEY` or snapshot) | 101 / 147 / 740 | supply areas + BSP points |

Login-only portals: with an API key in the environment the adapters fetch live. Without one they read a snapshot
exported through the owner's signed-in browser into `data/raw/portals/<dno>_bundle.json` (git-ignored); the cloud
routine has neither, so those operators keep their last committed layers until refreshed locally. To refresh a
snapshot, export the datasets each adapter lists (Opendatasoft `/exports/geojson` or `/exports/json`) while signed
in, save as that bundle (a JSON object keyed by dataset id), and run `python3 build_gb.py ukpn enwl spen`.

Caveats: headroom is each operator's own estimate, so methods differ; RAG is the most comparable measure. ENWL
publishes no RAG or substation names, so its RAG is derived by this map (red: no headroom, amber: under 2 MW) and
sites are labelled by number. NGED's supply-area polygons need a login, so its sites are points.

Northern Ireland (NIE Networks) is out of scope: no open-data API.

`build_gb.py` normalises every operator to one schema modelled on the LTDS Capacity Heatmap (demand/generation
headroom, firm capacity, max demand, RAG, limiting factor) and links each site to its parent (DNO ids where
published, else the parent supply area that contains it, else name match). Output: `data/gb/<DNO>_<level>.geojson`,
`data/gb/licence_areas.geojson`, `data/gb/manifest.json`. A DNO that fails, lacks its key, or shrinks by more than
20% keeps its previous files. Opendatasoft keys go in the `Authorization: Apikey <key>` header; NGED's CKAN takes
the raw token. Keys come from environment variables (cloud) or `.env` (local).

Cloud network allowlist needs: `services8.arcgis.com`, `data-api.ssen.co.uk`, `*.r2.cloudflarestorage.com`,
`connecteddata.nationalgrid.co.uk`, `*.opendatasoft.com` (SPEN's open polygons are fetched even without a key).

## SSEN detail

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

The map is served by GitHub Pages from `main`: https://rhystheblob.github.io/gb-grid-map/ (the old
ssen-grid-map address redirects here).

A Claude cloud routine ("GB grid map weekly refresh", Mondays 06:00 UTC) checks out this repo, runs
`fetch_public.py`, `fetch_portal.py --skip-smart-meter` and `build_gb.py`, sanity-checks feature counts, and commits changed `data/` files, which
republishes the map. `SSEN_API_KEY` is set as an environment variable on the cloud environment, never in the repo.
Manage the routine at https://claude.ai/code/routines/trig_01YYGzTxvU8BEPKmTShbzBgo

Smart meter data is refreshed locally, not by the cloud routine: SSEN's daily raw files are ~800 MB each (too big
for git, and the API needs SSEN verification). Download days from the portal's Raw files tab into
`smart_meter_uploads/` (git-ignored), run `python3 fetch_portal.py --skip-secondary`, and commit
`data/smart_meter_secondary.json`. Files outside the newest-7 window are deleted automatically. Raw files lag
~6 weeks; winter weekdays give the most useful peak. Current data: 14 and 21 Jan 2026 (highest peak of the two).

`run-weekly.sh` runs the same refresh locally (it needs `.env` or `SSEN_API_KEY`).
