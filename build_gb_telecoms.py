"""GB telecoms layer for dig-once planning.

Telecoms networks (Openreach ducts/poles, altnet routes) aren't open data. What is open and signals where fibre
still has to be dug in:

    Ofcom Connected Nations (Jan 2026, OGL)    gigabit availability per census output area -> premises still
                                               without gigabit = remaining build
    Premises in BDUK plans (May 2026, OGL)     Project Gigabit: premises in subsidised (GIS) build contracts, with
                                               supplier, contract and final coverage date (England and Wales)

Locations: output areas via ONS 2021 (E&W) and NRS 2022 (Scotland) population-weighted centroids; BDUK premises
via their postcode and OS Code-Point Open. All public, no login.

Inputs: data/raw/telecoms/ (Ofcom zip, centroids, Code-Point, BDUK zips fetched by --fetch-bduk)
Outputs: data/gb/telecoms/oa.json        [[lon, lat, % without gigabit, premises without, premises], ...]
         data/gb/telecoms/gigabit.json   {"suppliers": [...], "contracts": [...],
                                          "postcodes": [[lon, lat, premises in contract, deferred, year, supplier i,
                                                         contract i, postcode], ...]}
Usage:   python3 build_gb_telecoms.py [--fetch-bduk]
"""
import csv
import glob
import io
import json
import sys
import urllib.request
import zipfile

from dnos.common import ROOT

RAW = ROOT / "data" / "raw" / "telecoms"
OUT = ROOT / "data" / "gb" / "telecoms"
OFCOM_OA = "202601_fixed_broadband_coverage_and_full_fibre_take-up (v2)/202601_fixed_oa_coverage_r1/202601_fixed_oa_coverage_r1.csv"
BDUK_ZIPS = [  # May 2026 release, https://www.gov.uk/government/publications/may-2026-omr-and-premises-in-bduk-plans-england-and-wales
    "https://assets.publishing.service.gov.uk/media/6aa952fd2f29b388848184f2/202605_north_east.zip",
    "https://assets.publishing.service.gov.uk/media/6aa9a9c70420ac660805f878/2026-09-10_zipped_files_release_north_west.zip",
    "https://assets.publishing.service.gov.uk/media/6aa9a68af1f8d2a39605f86e/202605_yorkshire_and_the_humber.zip",
    "https://assets.publishing.service.gov.uk/media/6aa9a56cce3f006bd4346aec/202605_east_midlands.zip",
    "https://assets.publishing.service.gov.uk/media/6aa9a6c3f1f8d2a39605f86f/2026-09-10_zipped_files_release_west_midlands.zip",
    "https://assets.publishing.service.gov.uk/media/6aa9a78b086ce7c57b818529/2026-09-10_zipped_files_release_east_of_england.zip",
    "https://assets.publishing.service.gov.uk/media/6aa9aa09f1f8d2a39605f870/2026-09-10_zipped_files_release_london.zip",
    "https://assets.publishing.service.gov.uk/media/6aa9aa85086ce7c57b81852a/2026-09-10_zipped_files_release_south_east.zip",
    "https://assets.publishing.service.gov.uk/media/6aa9a7f1ce3f006bd4346aef/2026-09-10_zipped_files_release_south_west.zip",
    "https://assets.publishing.service.gov.uk/media/6aa9541dce3f006bd4346ab3/202605_wales.zip",
]
UA = {"User-Agent": "gb-grid-map/1.0 (+https://github.com/rhystheblob/gb-grid-map)"}


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def output_areas():
    centroids = json.loads((RAW / "oa21_ew_centroids.json").read_text())
    centroids.update(json.loads((RAW / "oa22_scot_centroids.json").read_text()))
    with zipfile.ZipFile(RAW / "ofcom_fixed_2026.zip") as z, z.open(OFCOM_OA) as fh:
        rows = list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))
    out, missing = [], 0
    for r in rows:
        c = centroids.get(r["output_area"])
        if not c:
            missing += r["output_area"][:1] in "ESW"  # Northern Ireland is out of scope (GB map)
            continue
        premises = int(num(r["All Premises"]) or 0)
        with_gig = int(num(r["Number of premises with Gigabit availability"]) or 0)
        without = max(premises - with_gig, 0)
        out.append([c[0], c[1], round(100 * without / premises) if premises else -1, without, premises])
    print(f"output areas: {len(out)} placed, {missing} GB areas without a centroid")
    return out


def postcode_locations():
    from pyproj import Transformer
    to_wgs84 = Transformer.from_crs(27700, 4326, always_xy=True)
    locs = {}
    for path in glob.glob(str(RAW / "codepo" / "Data" / "CSV" / "*.csv")):
        with open(path, newline="") as fh:
            for row in csv.reader(fh):
                e, n = num(row[2]), num(row[3])
                if e and n:
                    lon, lat = to_wgs84.transform(e, n)
                    locs[row[0].replace(" ", "")] = (round(lon, 5), round(lat, 5))
    return locs


def fetch_bduk():
    """Aggregate the UPRN-level BDUK files to postcodes as they download (~390 MB of zips, not kept)."""
    agg = {}
    for url in BDUK_ZIPS:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
            blob = io.BytesIO(r.read())
        with zipfile.ZipFile(blob) as z:
            for name in z.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                with z.open(name) as fh:
                    for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")):
                        if row.get("bduk_gis", "").strip().lower() != "true":
                            continue
                        pc = (row.get("postcode") or "").replace(" ", "").upper()
                        a = agg.setdefault(pc, {"n": 0, "deferred": 0, "date": "", "supplier": "", "contract": ""})
                        a["n"] += 1
                        a["deferred"] += (row.get("bduk_gis_contract_scope") or "").strip().lower() == "deferred"
                        a["date"] = max(a["date"], row.get("bduk_gis_final_coverage_date") or "")
                        a["supplier"] = a["supplier"] or (row.get("bduk_gis_supplier") or "").strip()
                        a["contract"] = a["contract"] or (row.get("bduk_gis_contract_name") or "").strip()
        print(f"  {url.rsplit('/', 1)[-1]}: {len(agg)} postcodes with Project Gigabit premises so far", flush=True)
    (RAW / "bduk_gis_by_postcode.json").write_text(json.dumps(agg))


def gigabit_build():
    agg = json.loads((RAW / "bduk_gis_by_postcode.json").read_text())
    locs = postcode_locations()
    suppliers, contracts, rows, unplaced = [], [], [], 0
    for pc, a in agg.items():
        loc = locs.get(pc)
        if not loc:
            unplaced += a["n"]
            continue
        for lst, key in ((suppliers, "supplier"), (contracts, "contract")):
            if a[key] not in lst:
                lst.append(a[key])
        year = int(a["date"][:4]) if a["date"][:4].isdigit() else -1
        rows.append([loc[0], loc[1], a["n"], a["deferred"], year, suppliers.index(a["supplier"]),
                     contracts.index(a["contract"]), pc])
    total = sum(r[2] for r in rows)
    print(f"Project Gigabit: {total} premises in {len(rows)} postcodes, {len(contracts)} contracts "
          f"({unplaced} premises in postcodes Code-Point can't place)")
    return {"suppliers": suppliers, "contracts": contracts, "postcodes": rows}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if "--fetch-bduk" in sys.argv:
        fetch_bduk()
    (OUT / "oa.json").write_text(json.dumps(output_areas(), separators=(",", ":")))
    (OUT / "gigabit.json").write_text(json.dumps(gigabit_build(), separators=(",", ":")))


if __name__ == "__main__":
    main()
