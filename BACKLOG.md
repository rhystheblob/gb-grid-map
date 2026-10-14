# GB Grid Map: improvement backlog

The improvement agent (a Claude cloud routine, run on demand) takes the top **Ready** item each run, builds it on a
branch, has it reviewed, and opens a pull request. The owner reviews and merges; deploys to AWS are run by hand
from the owner's Mac (`infra/deploy.sh`). Edit this file freely: reorder, add, or move items to Parked.

## How a run works (instructions for the agent)

1. Read `README.md`, this file and `index.html` first. The live site is https://dhjqzmvbjasrm.cloudfront.net/.
2. Take the first item under **Ready** whose "Needs" line you can satisfy in the cloud. Keep the change small
   enough to review in one sitting; split big items and do the first slice.
3. Work on a new branch `improve/<short-name>`. Never push to `main`, never merge, never force-push.
4. Plan, then implement. Match the existing code style (plain HTML/JS in `index.html`, Python build scripts).
5. Before opening the PR, use subagents:
   - a **reviewer** that reads the diff for bugs, regressions in other layers, and performance with ~475k points;
   - a **tester** that serves the site locally (`python3 -m http.server` from the repo root) and checks the pages
     and data files load, the JavaScript parses (`node --check` on the extracted script if node is available),
     and any Python you changed runs. Fix what they find.
6. Open a PR: what changed, why, how it was tested, anything the owner must do (e.g. run a build locally, deploy).
   End the description with the standard Claude Code attribution line.
7. Update this file in the same PR: move the item to **Done** with the PR link, add follow-ups or new ideas.

**Limits:** the cloud has no access to the owner's Mac. `data/raw/` (portal exports, the 660 MB street works
summary, OS Open USRN) does not exist there, so don't run builds that need it; say in the PR if a local rebuild is
needed. Don't touch `infra/`, AWS, secrets or `.env`. Don't download large files (>100 MB). Public APIs used by
existing build scripts are fine if the cloud environment's network allows them.

## Ready

1. **Top streets list for dig once.** A sidebar panel listing the highest-scoring streets in view (or in the
   selected area): street, town, score, utilities involved; clicking one zooms to it and opens its popup. Respect
   the score slider and area filter. Needs: `index.html` + existing `data/gb/digonce/cells/` files.
2. **Export dig-once streets as CSV** for the selected area (or view): USRN, street, town, highway authority,
   score, works past/planned by sector, restriction date. Needs: `index.html` only.
3. **District councils in the area filter.** Add lower-tier local authority districts (ONS LAD Dec 2025 BGC, public
   ArcGIS) alongside the upper-tier list, e.g. a toggle "County/unitary | District". Needs: extend
   `build_gb_boundaries.py` (public data, no `data/raw/` needed for boundaries; per-district dig-once counts need
   the existing `data/gb/digonce/` outputs, which are in git).
4. **Mobile pass.** Check the layout at phone width (sidebar over map, legend slider, popups); fix anything
   unusable. Needs: `index.html` only.
5. **Vector tiles (first slice).** Plan the move of the heaviest layers to PMTiles with MapLibre (site is ~560 MB);
   first PR: a written plan in `docs/vector-tiles.md` plus a prototype for one layer behind a flag. Needs: research,
   `tippecanoe` may not be available in the cloud: say so if not.
6. **Shareable links, part 2.** Links keep one drill-down level (the sites under one GSP/BSP) but not the full
   breadcrumb trail or an open popup (e.g. a dig-once street by USRN); add those. Needs: `index.html` only.

## Parked (owner decision needed)

- **AI chatbot** that turns questions ("most likely dig-once locations in SSEN's area") into map filters. Needs a
  provider/model choice (Bedrock vs Anthropic API) and safeguards (per-visitor limit, daily spend cap).
- **Exact dig outlines** instead of permit centre points: needs the 12 monthly Street Manager permit archives
  (~12 GB) re-processed on the owner's Mac.
- **Automatic deploys** from GitHub: the workflow exists but needs the AWS Paid plan (OIDC).
- **Scotland roadworks** (SRWR): check whether Scottish Road Works Register data is open; England only today.
- **Wales & West Utilities gas pipes**: none published yet; recheck occasionally.

## Done

- AWS hosting (S3 + CloudFront), local authority boundaries + area filter, dig-once score slider, dots that grow
  with zoom, dig-once streets drawn along the road (Oct 2026).
- Shareable links: layer, colouring, operator, local authority / licence area, dig-once minimum score, drill-down,
  zoom and centre kept in the URL hash, plus a "Copy link to this view" link (https://github.com/rhystheblob/gb-grid-map/pull/1).
