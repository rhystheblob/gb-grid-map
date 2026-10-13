# GB Grid Capacity Map

Interactive capacity map of Great Britain's electricity distribution networks, built from each network operator's
(DNO's) open data: GSP → BSP → Primary for every operator, plus Secondary → smart meter where available.

Live map: https://dhjqzmvbjasrm.cloudfront.net/

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

### Secondary substations (`build_gb_secondary.py`)

~475k GB secondaries in one shape; the common measure is utilisation = peak demand / transformer rating.

| DNO | Secondaries | With utilisation | Geometry | Source (access) |
|---|---|---|---|---|
| SSEN | 93,467 | none (no ratings published; smart meter peak instead) | supply areas | CKAN `secondary-substation-esa` (key) |
| UKPN | 76,972 | 76,551 (published as bands) | 56,889 supply areas + sites | `ukpn-secondary-sites`, `ukpn_secondary_postcode_area` (login) |
| ENWL | 33,181 | 33,181 | transformer points | `sp-enw-capacity-distribution-tx` (login) |
| NPg | 57,248 | 26,920 (demand redacted at small sites) | 51,460 supply areas + sites | `substation_sites_list` (open), `distribution-substation-service-areas` (login) |
| NGED | 195,717 | 162,022 | site points (55% located from OS grid refs) | `distribution-substations` (login) |
| SPEN | 18,874 | 18,874 (monitored transformers only) | points | `lv_monitor` (login) |

Inputs live in `data/raw/portals/` (git-ignored; login-only files exported via the owner's browser). Outputs:
`data/gb/sec/points_<DNO>.json` (every secondary as `[lon, lat, util%, peak kW]` for the zoomed-out view) and
`data/gb/sec/<DNO>/<cell>.geojson` (full detail per ~10 km cell, loaded from zoom 13). Methods differ: UKPN bands,
NGED's maximum demand method isn't stated, SPEN's monitored subset reads low. ~226 MB of cell files; moving to
vector tiles is part of the AWS step.

### Smart meter peaks (`smart_meter_gb.py`)

Peak half-hour load per secondary from Ofgem-standard aggregated smart meter data, on the same two winter Wednesdays
as SSEN (14 and 21 Jan 2026) so operators share the same weather:

| DNO | Secondaries with peaks | Source |
|---|---|---|
| SSEN | 37,022 | portal Raw files (daily CSVs), see SSEN detail |
| UKPN | 55,945 | UKPN Azure Blob container via the public read-only SAS token in UKPN's "Smart Meter Consumption Data Azure Blob Storage.pdf" (`UKPN_SAS` in `.env`; expires 2026-11-10). Files are named by publication month: `February_2026` holds January. ~14 GB streamed, never stored. |
| NGED | 29,960 | `aggregated-smart-meter-data-secondary-substation` (login): 75 gz parts for Jan 2026, aggregated inside the owner's signed-in browser |
| NPg | 2,074 | `aggregated-smart-metering-data` (login), monthly zips ending July 2025, so Jan 2025 (15 and 22 Jan) |
| ENWL, SPEN | – | ENWL publishes installation counts only; SPEN only single-month summer pilots |

Peaks averaging over 10 kW per meter are treated as bad readings and dropped (~0.05%). Output:
`data/raw/smart/<DNO>.json`, joined by `build_gb_secondary.py` (by substation id; NPg by SPID, else nearest site).

## Gas distribution (`build_gb_gas.py`)

The gas networks publish low/medium-pressure pipes in the UK Gas Network Data Interoperability Standard. Full
networks run to millions of pipe sections (Cadent alone: 2.28M), too much for this static site, so the map shows
**metallic mains** (cast, spun and ductile iron, steel), the pipes the iron mains replacement programme is
removing and so the likeliest to be dug up soon, with pipes in each network's published replacement programme
highlighted. Planning use only, not for excavation (LinesearchbeforeUdig).

| GDN | Area | Status | Source (access) |
|---|---|---|---|
| Cadent | NW England, W Midlands, E England, N London | 309,943 metallic main sections, 19,493 km; 5,316 km in the Mains Replacement Programme | `gas-pipe-infrastructure-gpi_open` (metallic mains filter) + 65 `MRP_Pipe_Infrastructure_<LA>_Open` (login) |
| Northern Gas Networks | North East, Yorkshire, Cumbria | 55,082 metallic main sections, 4,946 km (all 5 zones) + network boundary; no replacement programme published | Live Distribution Mains per zone + Network Boundaries, GeoPackage (login; the portal keeps the session per tab, so downloads are by the owner's own clicks into `data/raw/portals/ngn/` and `ngn_extra/`) |
| SGN | Scotland, Southern England | 136,714 metallic main sections, 13,487 km (114 local authorities); no replacement programme published | public ArcGIS service per local authority on SGN's open data hub (`python3 build_gb_gas.py --fetch-sgn`, no login) |
| Wales & West Utilities | Wales, SW England | no pipe data published yet | network boundary, LTDS workbook |

Outputs: `data/gb/gas/grid.json` (km per ~10 km cell), `data/gb/gas/<GDN>/<cell>.geojson`, `areas.geojson`.

## Water (`build_gb_water.py`)

Water companies don't publish their pipe networks openly; the national register of buried pipes (NUAR) is
invitation-only (asset owners, local and highways authorities). Open via the industry's Stream portal
(streamwaterdata.co.uk, public ArcGIS services, no login):

- **Water company areas**: 432 supply areas incl. inset networks (Stream "Water Company Boundaries").
- **Storm overflow improvements**: all 14,187 overflows in the National Storm Overflows Plan for England, with the
  year improvement works (storage, larger sewers, i.e. excavation) are planned: 4,823 by 2030, 1,943 in 2031-35,
  5,509 in 2036-50; 1,908 already meet targets.

## Telecoms (`build_gb_telecoms.py`)

Telecoms networks (Openreach ducts/poles, altnet routes) aren't open data (NUAR holds them, invitation-only).
Open signals of where fibre still has to be dug in:

- **Ofcom Connected Nations** (Jan 2026, OGL): gigabit availability for all 235,098 GB census output areas, placed
  at ONS 2021 (E&W) / NRS 2022 (Scotland) population-weighted centroids. 3.93M of 31.6M premises (12.4%) lack
  gigabit-capable broadband.
- **Premises in BDUK plans** (May 2026, OGL): Project Gigabit's subsidised build: 805,662 premises in 96,196
  postcodes across 36 contracts (England and Wales), with supplier and final coverage date (2025-2032), placed via
  OS Code-Point Open. `--fetch-bduk` streams the ~390 MB of regional zips and keeps only the postcode summary.

Inputs in `data/raw/telecoms/` (Ofcom zip, centroids, Code-Point). Ofcom and BDUK update a few times a year, so
this is refreshed by hand rather than weekly.

## Dig once (`build_gb_digonce.py`)

Where street works could be coordinated, from the DfT Street Manager open data archive (England, OGL,
`opendata.manage-roadworks.service.gov.uk`, public, no login). Many archive zips are published with a truncated
central directory; `zip_entries()` reads entries from their local headers instead.

- **Section 58 restrictions** (`section_58/YYYY/MM.zip`, from 2022): 70,659 notices; as of 2026-10-11, 23,895 in
  force (no new street works for 6 months-3 years after resurfacing) and 9,516 proposed in the last year (the
  window to dig before the restriction starts).
- **Street works** (`permit/YYYY/MM.zip`, ~1 GB/month; `--fetch-permits` processes the last 12 months then deletes
  them, keeping a 660 MB summary in `data/raw/streetworks/works.json`): 2.76M works on 501,189 streets. Promoters are
  classed into electricity / gas / water / telecoms / highway (`dnos/sectors.py`, 99.8% classified).
- **Per street (USRN)**: utilities that dug it in the last 12 months, utility works already planned, a proposed
  restriction, plus nearby signals from the other layers (gas metallic mains / replacement programme within ~100 m,
  storm overflow works by 2030 within ~500 m, secondaries over 100% within ~200 m, missing gigabit or Project
  Gigabit build). 192,678 streets have a signal; 53,926 were dug by 3+ utilities in a year (12,276 by all four);
  only ~1.4% of works are flagged collaborative.
- Score = utilities that dug in the year + 2 x utilities with works planned + 3 if a restriction is proposed +
  nearby signals (gas replacement programme 2, other gas metallic main 1, overflow works 1, overloaded secondary 1,
  telecoms build 1).

Outputs: `data/gb/digonce/s58.geojson`, `streets.json` (overview points), `cells/<cell>.json` (details).
Refresh monthly (new archive month); not part of the weekly cloud run because the 12-month window needs the local
works summary. Scotland uses the Scottish Road Works Register, not covered.

## Local authorities (`build_gb_boundaries.py`)

Overlay of the 207 GB upper-tier councils (counties and unitary authorities: in England the highway authorities
that permit street works and issue Section 58 restrictions; every Scottish and Welsh council is unitary), from the
ONS "Counties and Unitary Authorities (December 2025) Boundaries UK BGC" service (public, Open Government Licence;
Northern Ireland dropped). Each authority carries its dig-once totals (streets with a signal, dug by 3+ utilities,
works planned, Section 58 proposed / in force, top score), counted by point-in-polygon, so **run it after
`build_gb_digonce.py`**. On the map: Overlays → "Local authorities" draws the boundaries.

**Area filter** ("Show only data within"): pick a local authority and/or a DNO licence area and every layer keeps
only data inside it (inside both when both are picked), the rest of the map is dimmed, and the stats cover the
whole area instead of the view. Points (secondaries, overflows, output areas, dig-once streets) are tested exactly;
areas and pipes count if any vertex falls inside or they enclose the selection; gas totals use the ~10 km squares
touching the area. Licence boundaries are generalised, so a few coastal points can drop out when one is selected.
Output: `data/gb/la.geojson` (~8 MB).

## Weekly cloud refresh: GB build steps

Run after the SSEN steps. All are public sources, so they work in the cloud with no keys:

```
python3 build_gb.py              # DNO capacity layers; login-only operators keep their snapshot
python3 build_gb_gas.py --fetch-sgn   # SGN mains (public ArcGIS); Cadent/NGN inputs are local-only, kept as committed
python3 build_gb_water.py        # water areas + storm overflow plan (Stream)
```

Sanity checks before committing: `data/gb/gas/index.json` SGN `pipes` and `data/gb/water/overflows.geojson`
feature count should not drop by more than 20% from the committed versions. Cloud allowlist additionally needs
`services-eu1.arcgis.com` and `open-data-sharing-portal-sgn-uk.hub.arcgis.com`.

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

The map was served by GitHub Pages until 11 Oct 2026; Pages is now off and the map is served from AWS only.

### AWS (S3 + CloudFront)

The site is served from AWS (project Region eu-north-1): https://dhjqzmvbjasrm.cloudfront.net/

- `infra/hosting.yaml`: CloudFormation stack `gb-grid-map-hosting` = private, encrypted S3 bucket
  `gb-grid-map-site-<account>` readable only by the CloudFront distribution (origin access control), HTTPS only,
  managed CachingOptimized + SecurityHeaders policies, PriceClass_100.
- `infra/deploy.sh`: uploads `index.html` + git-tracked `data/` (never `data/raw/`) and invalidates the cache.
  CloudFront only compresses 1 KB–10 MB responses with listed content types, so `.geojson` is served as
  `application/json` and JSON files over 10 MB are uploaded pre-gzipped (`Content-Encoding: gzip`). Run it after
  each data refresh: `PATH=$HOME/.local/bin:$PATH infra/deploy.sh` (profile `gb-grid-map`, via `aws login`).
- `infra/budget.sh`: the $5/month cost budget with email alerts (80% actual, 100% forecast); kept out of the
  stack because CloudFormation in eu-north-1 has no `AWS::Budgets::Budget`.
- `.github/workflows/deploy.yml` deploys on every push to `main`, but stays skipped until the repo variable
  `AWS_DEPLOY_ROLE_ARN` is set. The role needs the GitHub OIDC provider (`GitHubOidc=true` in the stack), which
  the AWS Free plan's managed SCP denies (`iam:*Provider*`), so it needs the Paid plan.

**Paused 11 Oct 2026** (the map moved to AWS, and the routine only commits to GitHub; it doesn't deploy).
A Claude cloud routine ("GB grid map weekly refresh", Mondays 06:00 UTC) checks out this repo, runs
`fetch_public.py`, `fetch_portal.py --skip-smart-meter` and `build_gb.py`, sanity-checks feature counts, and commits changed `data/` files. `SSEN_API_KEY` is set as an environment variable on the cloud environment, never in the repo.
Manage the routine at https://claude.ai/code/routines/trig_01YYGzTxvU8BEPKmTShbzBgo

Smart meter data is refreshed locally, not by the cloud routine: SSEN's daily raw files are ~800 MB each (too big
for git, and the API needs SSEN verification). Download days from the portal's Raw files tab into
`smart_meter_uploads/` (git-ignored), run `python3 fetch_portal.py --skip-secondary`, and commit
`data/smart_meter_secondary.json`. Files outside the newest-7 window are deleted automatically. Raw files lag
~6 weeks; winter weekdays give the most useful peak. Current data: 14 and 21 Jan 2026 (highest peak of the two).

`run-weekly.sh` runs the same refresh locally (it needs `.env` or `SSEN_API_KEY`).
