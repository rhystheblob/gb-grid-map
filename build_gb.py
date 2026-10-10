"""Build the GB-wide capacity layers from every DNO adapter in dnos/.

Writes data/gb/<DNO>_<level>.geojson (common schema, see dnos/common.py), data/gb/licence_areas.geojson and
data/gb/manifest.json (what each DNO contributed, when, and under which licence).

A DNO whose portal fails, needs a key that isn't configured, or returns far fewer sites than last time keeps its
previous files, so one bad source never blanks part of the map.

Usage:  python3 build_gb.py [DNO ...]      (default: all; run fetch_public.py first for SSEN)
"""
import datetime
import importlib
import json
import re
import sys
import urllib.error

from dnos.common import LEVELS, ROOT, MissingKey, ods_geojson, simplify

ADAPTERS = ["ssen", "nged", "npg", "ukpn", "enwl", "spen"]
OUT = ROOT / "data" / "gb"
SHRINK_LIMIT = 0.8  # refuse a refresh that loses more than 20% of a DNO's sites at any level


def build_licence_areas():
    fc = ods_geojson("northernpowergrid.opendatasoft.com", "all_dno_boundaries")
    feats = []
    for f in fc["features"]:
        geom = simplify(f["geometry"], "gsp")
        if geom:
            feats.append({"type": "Feature", "properties": {"name": f["properties"].get("longname")}, "geometry": geom})
    (OUT / "licence_areas.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                         separators=(",", ":")))
    print(f"licence areas: {len(feats)}")


def _norm(name):
    """Loose name key: drop voltage/type words so 'Buxton 132Kv S Stn', 'BUXTON BSP' and 'Buxton' all match."""
    s = (name or "").lower()
    s = re.sub(r"\b(s\.?g\.?p\.?|gsp|bsp|grid|primary|bulk supply point|supply point|substation|s ?stn|sub|"
               r"\d+ ?kv|132|66|33|11|\d+/\d+(/\d+)?)\b", " ", s)
    return re.sub(r"[^a-z]+", "", s)


def link_hierarchy(dno, sites):
    """Give every site an id and, below GSP, a parent id (pid): the DNO's own link if supplied, else the parent
    whose supply area contains the site, else a name match. Primaries with no BSP link to their GSP."""
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    for i, s in enumerate(sites):
        p = s["properties"]
        p["id"] = f"{dno}:{p['level']}:{p['id'] or i}"
    ids = {s["properties"]["id"] for s in sites}
    for s in sites:
        p = s["properties"]
        if p["pid"]:  # a DNO-supplied parent id: a primary's parent may be a BSP or (no BSP tier) a GSP
            ups = ("bsp", "gsp") if p["level"] == "primary" else ("gsp",)
            p["pid"] = next((f"{dno}:{u}:{p['pid']}" for u in ups if f"{dno}:{u}:{p['pid']}" in ids), None)
    by_level = {lvl: [s for s in sites if s["properties"]["level"] == lvl and s["geometry"]] for lvl in LEVELS}

    def finder(parents):
        polys = [(shape(s["geometry"]), s["properties"]["id"]) for s in parents if s["geometry"]["type"] != "Point"]
        tree = STRtree([g for g, _ in polys]) if polys else None
        names = {_norm(s["properties"]["name"]): s["properties"]["id"] for s in parents}

        def find(child, upstream_name):
            if tree is not None:
                pt = shape(child["geometry"]).representative_point()
                for j in tree.query(pt):
                    if polys[j][0].contains(pt):
                        return polys[j][1]
            return names.get(_norm(upstream_name)) if upstream_name else None
        return find

    to_gsp, to_bsp = finder(by_level["gsp"]), finder(by_level["bsp"])
    linked = 0
    for s in by_level["bsp"] + by_level["primary"]:
        p = s["properties"]
        if p["pid"] in ids:
            linked += 1
            continue
        pid = to_bsp(s, p["bsp"]) if p["level"] == "primary" else None
        p["pid"] = pid or to_gsp(s, p["gsp"])
        linked += bool(p["pid"])
    return linked, len(by_level["bsp"]) + len(by_level["primary"])


def run(name, manifest):
    mod = importlib.import_module(f"dnos.{name}")
    fetch = getattr(mod, "fetch", None)
    if fetch is None:
        print(f"{mod.NAME}: adapter not written yet - skipping")
        return
    try:
        sites = fetch()
    except MissingKey as e:
        print(f"{mod.NAME}: needs API key {e} - keeping previous data")
        return
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        print(f"{mod.NAME}: FAILED ({e}) - keeping previous data")
        manifest.setdefault(mod.NAME, {})["last_error"] = str(e)[:300]
        return

    by_level = {lvl: [s for s in sites if s["properties"]["level"] == lvl and s["geometry"]] for lvl in LEVELS}
    previous = manifest.get(mod.NAME, {}).get("counts", {})
    for lvl, feats in by_level.items():
        if previous.get(lvl) and len(feats) < SHRINK_LIMIT * previous[lvl]:
            print(f"{mod.NAME}: {lvl} dropped from {previous[lvl]} to {len(feats)} sites - keeping previous data")
            manifest[mod.NAME]["last_error"] = f"{lvl} shrank to {len(feats)} from {previous[lvl]}"
            return

    linked, below = link_hierarchy(mod.NAME, sites)
    counts = {}
    for lvl, feats in by_level.items():
        path = OUT / f"{mod.NAME}_{lvl}.geojson"
        if not feats:
            path.unlink(missing_ok=True)
            continue
        for f in feats:
            f["geometry"] = simplify(f["geometry"], lvl)
            f["properties"]["dno"] = mod.NAME
        feats = [f for f in feats if f["geometry"]]
        path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
        counts[lvl] = len(feats)
    polygons = sum(1 for s in sites if s["geometry"] and s["geometry"]["type"] != "Point")
    manifest[mod.NAME] = {
        "title": mod.TITLE, "licence": mod.LICENCE, "source": mod.SOURCE, "counts": counts,
        "geometry": "polygons" if polygons > len(sites) / 2 else "points",
        "updated": datetime.date.today().isoformat(),
    }
    manifest[mod.NAME]["linked"] = f"{linked}/{below}"
    print(f"{mod.NAME}: " + ", ".join(f"{lvl} {n}" for lvl, n in counts.items()) + f" | linked to parent {linked}/{below}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    wanted = [a for a in ADAPTERS if not sys.argv[1:] or a in [x.lower() for x in sys.argv[1:]]]
    for name in wanted:
        run(name, manifest)
    if not (OUT / "licence_areas.geojson").exists() or not sys.argv[1:]:
        build_licence_areas()
    manifest_path.write_text(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
