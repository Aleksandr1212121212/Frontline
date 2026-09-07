#!/usr/bin/env python3
"""
Turn whatever the fetchers produced into out/data.json and out/index.html.

Runs offline. If a source is missing the page still builds and says so in the open, rather than
inventing geometry. Run with --self-test to build from tests/ fixtures instead of live/.
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from shapely.geometry import shape, Point, LineString, MultiLineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

import config as C
import geo as G
from gazetteer import Gazetteer, haversine_km

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
LIVE = P("live")
SNAPS = P("cache", "snapshots")

CLASSES = ("ru_control", "ua_control", "grey", "ru_claimed", "advance", "infiltration", "fortification", "strike", "unit")


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- classification

def _colour_family(hexcol):
    if not hexcol or len(hexcol) != 7:
        return None
    r, g, b = int(hexcol[1:3], 16), int(hexcol[3:5], 16), int(hexcol[5:7], 16)
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 40:
        return "grey"
    if r == mx and g < 150 and b < 150:
        return "pink" if r < 200 and min(g, b) > 90 else "red"
    if b == mx and r < 150:
        return "blue"
    if g == mx and b > 150:
        return "cyan"
    if r > 200 and g > 140 and b < 120:
        return "orange"
    if r > 200 and g > 200 and b < 140:
        return "yellow"
    return None


def classify_layer(name, features):
    """Class for a group of features. Layer name wins; fill colour decides when the name says nothing."""
    n = (name or "").lower()
    for w in C.IGNORE_LAYER_KEYWORDS:
        if w in n:
            return None, f"ignored: layer name contains {w!r}"
    named = None
    for cls, words in C.LAYER_KEYWORDS:
        if any(w in n for w in words):
            named = cls
            break
    fills = [f.get("fill") for f in features if f.get("fill")]
    fam = _colour_family(max(set(fills), key=fills.count)) if fills else None
    coloured = C.COLOUR_CLASS.get(fam)
    if named and coloured and named != coloured and fam in ("blue", "cyan"):
        # a blue group inside a layer named for Russian control is the other side's territory
        return coloured, f"fill colour {fam} overrides layer name {name!r}"
    if named:
        return named, f"layer name {name!r}"
    if coloured:
        return coloured, f"fill colour {fam}"
    kinds = {f["kind"] for f in features}
    if kinds == {"line"}:
        return "advance", "line-only layer"
    if kinds == {"point"}:
        return "unit", "point-only layer"
    return None, "unclassified"


def colour_groups(features):
    """Split a layer into groups of one fill colour each, so a layer holding both sides is not read as one."""
    groups = defaultdict(list)
    for f in features:
        groups[f.get("fill") or f.get("stroke") or "none"].append(f)
    return groups


def load_mymaps(want_russian=False):
    """My Maps sources in live/, classified. want_russian selects the Russian-side maps instead of the base ones."""
    out = {c: [] for c in CLASSES}
    by_source, ua_by_source = defaultdict(list), defaultdict(list)
    points, linefeats, report, sources = [], [], [], {}
    for path in sorted(glob.glob(os.path.join(LIVE, "*.json"))):
        base = os.path.basename(path)
        if not (base == "kalibrated.json" or base.startswith("mymaps_") or base.startswith("rumap_")):
            continue
        try:
            doc = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            log(f"  {base}: unreadable ({e})")
            continue
        src = base[:-5]
        if src.startswith("rumap_") != want_russian:
            continue
        sources[src] = doc.get("fetched", "unknown")
        for layer in doc.get("layers", []):
          for fill, feats in colour_groups(layer["features"]).items():
            cls, why = classify_layer(layer["name"], feats)
            polys_, lines_, pts_ = [], [], []
            for f in feats:
                if f["kind"] == "polygon":
                    polys_.append(f["coords"])
                elif f["kind"] == "line":
                    lines_.append({"c": f["coords"], "n": f.get("name", ""), "src": src, "cls": cls})
                else:
                    pts_.append({"lon": f["coords"][0], "lat": f["coords"][1], "n": f.get("name", ""),
                                 "d": f.get("desc", "")[:240], "src": src, "cls": cls})
            label = layer["name"] if len(colour_groups(layer["features"])) == 1 else f"{layer['name']} [{fill}]"
            report.append({"source": src, "layer": label, "class": cls, "why": why,
                           "polygons": len(polys_), "lines": len(lines_), "points": len(pts_)})
            if cls and polys_:
                g = G.from_rings(polys_)
                out[cls].append(g)
                if cls == "ru_control":
                    by_source[src].append(g)
                elif cls == "ua_control":
                    ua_by_source[src].append(g)
            if cls:                      # lines and pins from an ignored layer are map furniture, not data
                linefeats.extend(lines_)
                points.extend(pts_)
    geom = {}
    for c in CLASSES:
        geom[c] = G.polys(G.valid(unary_union(out[c])), C.MIN_POLY_KM2) if out[c] else MultiPolygon()
    per_src = {k: G.polys(G.valid(unary_union(v)), C.MIN_POLY_KM2) for k, v in by_source.items() if v}
    per_src_ua = {k: G.polys(G.valid(unary_union(v)), C.MIN_POLY_KM2) for k, v in ua_by_source.items() if v}
    return geom, points, linefeats, report, sources, per_src, per_src_ua


def load_isw():
    out, meta = {}, {}
    for key in ("claimed", "control", "advances", "fortifications", "partisan"):
        fp = os.path.join(LIVE, f"isw_{key}.geojson")
        if not os.path.exists(fp):
            continue
        try:
            fc = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            log(f"  isw_{key}: unreadable ({e})")
            continue
        geoms = [G.valid(shape(f["geometry"])) for f in fc.get("features", []) if f.get("geometry")]
        if not geoms:
            continue
        u = G.valid(unary_union(geoms))
        out[key] = u
        meta[key] = fc.get("fetched", "yes")
    return out, meta


# ---------------------------------------------------------------- events

EVENT_WEIGHT = {"ru_taken": 1.0, "ru_advance": 0.8, "contested": 0.9, "infiltration": 0.7, "ua_retaken": 0.8}


def load_events(gaz, front):
    fp = os.path.join(LIVE, "events.json")
    if not os.path.exists(fp):
        return [], []
    raw = json.load(open(fp, encoding="utf-8"))
    located, unlocated = [], []
    cache = {}
    for e in raw:
        key = e["name"]
        if key not in cache:
            cache[key] = gaz.geocode(key, near=front)
        hit, how = cache[key]
        if hit:
            located.append({**e, "lat": hit["lat"], "lon": hit["lon"], "place": hit["n"], "match": how})
        else:
            unlocated.append({**e, "match": how})
    return located, unlocated


def event_uncertainty(events, days=10):
    """Clusters of recent reports become uncertainty blobs: fighting reported here, control unclear."""
    cutoff = (NOW - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = [e for e in events if e["d"] >= cutoff and e["kind"] in ("contested", "infiltration", "ru_advance")]
    if not recent:
        return MultiPolygon(), []
    by_place = defaultdict(list)
    for e in recent:
        by_place[(round(e["lat"], 3), round(e["lon"], 3))].append(e)
    blobs, hotspots = [], []
    for (lat, lon), items in by_place.items():
        sides = {i["side"] for i in items}
        w = sum(EVENT_WEIGHT.get(i["kind"], 0.6) for i in items)
        radius = min(C.EVENT_CLUSTER_KM, 3 + 1.6 * w) * (1.25 if len(sides) > 1 else 1.0)
        blobs.append(G.buffer_km(Point(lon, lat), radius))
        hotspots.append({"lat": lat, "lon": lon, "place": items[0].get("place", items[0]["name"]),
                         "n": len(items), "sides": sorted(sides), "km": round(radius, 1),
                         "kinds": sorted({i["kind"] for i in items})})
    hotspots.sort(key=lambda h: -h["n"])
    return G.polys(G.valid(unary_union(blobs))), hotspots


# ---------------------------------------------------------------- snapshots and history

def save_snapshot(ru_control, grey, source_tag):
    if ru_control.is_empty:
        return
    os.makedirs(SNAPS, exist_ok=True)
    path = os.path.join(SNAPS, f"{TODAY}.json")
    json.dump({"d": TODAY, "src": source_tag, "km2": round(G.km2(ru_control), 1),
               "ru": G.poly_coords(G.simplify_m(ru_control, 150), 4),
               "grey_km2": round(G.km2(grey), 1) if not grey.is_empty else 0.0},
              open(path, "w"), separators=(",", ":"))


def load_history():
    out = []
    for fp in sorted(glob.glob(os.path.join(SNAPS, "*.json"))):
        try:
            s = json.load(open(fp, encoding="utf-8"))
            out.append(s)
        except Exception:
            continue
    cutoff = (NOW - timedelta(days=C.HISTORY_KEEP_DAYS)).strftime("%Y-%m-%d")
    return [s for s in out if s["d"] >= cutoff]


def build_history(today_geom):
    snaps = load_history()
    series = [{"d": s["d"], "a": s["km2"], "g": s.get("grey_km2", 0)} for s in snaps]
    anchors, changes = [], []
    if not snaps:
        return series, anchors, changes, None
    cur_ea = G.ea(today_geom)
    # anchors: every snapshot for the last 21 days, then weekly, then monthly
    picked, seen_week, seen_month = [], set(), set()
    latest = datetime.strptime(snaps[-1]["d"], "%Y-%m-%d")
    for s in snaps:
        d = datetime.strptime(s["d"], "%Y-%m-%d")
        age = (latest - d).days
        if age <= 21:
            picked.append(s)
        elif age <= 180:
            wk = d.isocalendar()[:2]
            if wk not in seen_week:
                seen_week.add(wk); picked.append(s)
        else:
            mo = (d.year, d.month)
            if mo not in seen_month:
                seen_month.add(mo); picked.append(s)
    for s in picked:
        g = G.from_rings(s["ru"])
        g_ea = G.ea(g)
        gained = G.polys(G.valid(cur_ea.difference(g_ea)))
        lost = G.polys(G.valid(g_ea.difference(cur_ea)))
        gained = MultiPolygon([p for p in gained.geoms if p.area >= C.MIN_POLY_KM2 * 1e6])
        lost = MultiPolygon([p for p in lost.geoms if p.area >= C.MIN_POLY_KM2 * 1e6])
        anchors.append({"d": s["d"], "gained_km2": round(gained.area / 1e6, 1), "lost_km2": round(lost.area / 1e6, 1),
                        "gained": G.poly_coords(G.ll(gained), 5), "lost": G.poly_coords(G.ll(lost), 5)})
    # day-to-day change list
    for a, b in zip(snaps, snaps[1:]):
        ga, gb = G.ea(G.from_rings(a["ru"])), G.ea(G.from_rings(b["ru"]))
        for kind, diff in (("gained", gb.difference(ga)), ("lost", ga.difference(gb))):
            for p in G.polys(G.valid(diff)).geoms:
                km2 = p.area / 1e6
                if km2 < C.MIN_POLY_KM2:
                    continue
                c = G.ll(p).representative_point()
                changes.append({"d": b["d"], "from": a["d"], "k": kind, "km2": round(km2, 1),
                                "lat": round(c.y, 4), "lon": round(c.x, 4)})
    # the most recent day the geometry actually moved; None if it has moved every day
    last_change = None
    for a, b in zip(snaps, snaps[1:]):
        if b["km2"] != a["km2"]:
            last_change = b["d"]
    if last_change == snaps[-1]["d"]:
        last_change = None
    changes.sort(key=lambda c: (c["d"], -c["km2"]), reverse=True)
    return series, anchors, changes, last_change


# ---------------------------------------------------------------- basemap

def _near_ukraine(coords, ukraine_box=(21.5, 44.0, 41.5, 53.0), max_span_deg=14):
    """Drop lines that are really country outlines: they sprawl far wider than any front axis."""
    if not coords:
        return False
    xs = [p[0] for p in coords]; ys = [p[1] for p in coords]
    if max(xs) - min(xs) > max_span_deg or max(ys) - min(ys) > max_span_deg:
        return False
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    return ukraine_box[0] <= cx <= ukraine_box[2] and ukraine_box[1] <= cy <= ukraine_box[3]


def load_basemap():
    c = json.load(open(P("basemap", "basemap_clipped.json"), encoding="utf-8"))
    ukraine = unary_union([Polygon(r[0], r[1:]) for r in c["_ukraine"]])
    crimea = unary_union([Polygon(r[0], r[1:]) for r in c["_crimea"]])
    return c["basemap"], ukraine, crimea


# ---------------------------------------------------------------- main

def main(self_test=False):
    global LIVE
    if self_test:
        LIVE = P("tests", "live")
    log("basemap…")
    basemap, ukraine, crimea = load_basemap()
    gaz = Gazetteer()
    log(f"gazetteer: {len(gaz.places)} places ({'full' if gaz.full else 'seed, pop>1000 only'})")

    def clip_to_ukraine(g, label):
        """Control geometry means occupied Ukraine. Russian federal subjects on a Russian map are not that."""
        if g.is_empty:
            return g, MultiPolygon()
        inside = G.polys(G.valid(G.ll(G.ea(g).intersection(G.ea(ukraine)))), C.MIN_POLY_KM2)
        outside = G.polys(G.valid(G.ll(G.ea(g).difference(G.ea(ukraine)))), 5.0)
        if not outside.is_empty:
            log(f"  {label}: {round(G.km2(outside)):,} km2 outside Ukraine dropped from control")
        return inside, outside

    log("my maps sources…")
    my, my_points, my_lines, class_report, my_sources, per_source, per_source_ua = load_mymaps()
    for r in class_report:
        log(f"  {r['source']}: {r['layer']!r} -> {r['class'] or 'IGNORED'} ({r['why']}; "
            f"{r['polygons']}p {r['lines']}l {r['points']}pt)")
    ru_my, ru_points, ru_lines, ru_class_report, ru_sources, ru_per_source, ru_per_source_ua = load_mymaps(want_russian=True)
    for r in ru_class_report:
        log(f"  [ru] {r['source']}: {r['layer']!r} -> {r['class'] or 'IGNORED'} ({r['why']}; "
            f"{r['polygons']}p {r['lines']}l {r['points']}pt)")
    isw, isw_meta = load_isw()
    if isw:
        log("ISW: " + ", ".join(f"{k} {round(G.km2(v)):,} km2" for k, v in isw.items() if v.geom_type != "LineString"))

    # ---- control geometry, in priority order: Kalibrated first, ISW as the fallback
    ru = my["ru_control"]
    base_source = "kalibrated"
    if ru.is_empty and "control" in isw:
        ru = G.polys(isw["control"], C.MIN_POLY_KM2)
        base_source = "isw"
    # "Ukrainian-held inside Russia" means inside Russia, not any sliver outside the Ukraine polygon
    russia = unary_union([Polygon(r[0], r[1:]) for c in basemap["land"] if c["a3"] == "RUS" for r in c["c"]])
    ua_held = G.polys(G.valid(G.ll(G.ea(unary_union([my["ua_control"], ru_my["ua_control"]])).buffer(0)
                                    .intersection(G.ea(russia).buffer(0)))), 20.0)
    grey = my["grey"]
    claimed = my["ru_claimed"]
    if claimed.is_empty and "claimed" in isw:
        claimed = G.polys(isw["claimed"], C.MIN_POLY_KM2)

    ru, ru_outside = clip_to_ukraine(ru, "base control")
    have_control = not ru.is_empty
    front = MultiLineString()
    if have_control:
        inner = G.ea(ukraine).buffer(-2500)
        front = G.lines(G.ll(G.ea(ru).boundary.intersection(inner)))

    # ---- every source's own reading of Russian-held ground, clipped to Ukraine and sanity-checked
    readings = []

    def add_reading(src, geom, kind="map"):
        if geom.is_empty:
            return
        # clip first: a Russian map draws Russia too, and that is not occupied Ukraine
        geom, _ = clip_to_ukraine(geom, src)
        if geom.is_empty:
            log(f"  {src}: nothing left inside Ukraine, skipped")
            return
        area = G.km2(geom)
        if area > C.MAX_SOURCE_KM2:
            log(f"  {src}: {area:,.0f} km2 is implausible for occupied Ukraine, skipped")
            return
        readings.append({"id": src, "kind": kind, "geom": geom})

    ua_area = G.ea(ukraine).area / 1e6

    def reading_for(src, own, ua_own):
        """Some maps draw the other side's territory instead of the occupied part. When a source's
        Ukrainian-control layer covers most of the country, its Russian-held reading is the complement."""
        if not ua_own.is_empty:
            covered = G.ea(ua_own).buffer(0).intersection(G.ea(ukraine).buffer(0)).area / 1e6
            if covered > 0.4 * ua_area:
                comp = G.polys(G.valid(G.ll(G.ea(ukraine).buffer(0).difference(G.ea(ua_own).buffer(0)))), C.MIN_POLY_KM2)
                log(f"  {src}: read as Ukraine minus its Ukrainian-control layer ({round(G.km2(comp)):,} km2)")
                return comp
        return own

    for src in set(per_source) | set(per_source_ua):
        add_reading(src, reading_for(src, per_source.get(src, MultiPolygon()), per_source_ua.get(src, MultiPolygon())))
    for src in set(ru_per_source) | set(ru_per_source_ua):
        add_reading(src, reading_for(src, ru_per_source.get(src, MultiPolygon()), ru_per_source_ua.get(src, MultiPolygon())))
    if "control" in isw:
        add_reading("isw_control", G.polys(isw["control"], C.MIN_POLY_KM2))
    if "claimed" in isw:
        add_reading("isw_claimed", G.polys(isw["claimed"], C.MIN_POLY_KM2), kind="claims")

    # the Russian-side reading of the same ground
    ru_read = MultiPolygon()
    for r in readings:
        if r["id"].startswith("rumap_"):
            ru_read = r["geom"]
            break
    if ru_read.is_empty and not my["ru_claimed"].is_empty:
        ru_read = my["ru_claimed"]
    ru_ahead = MultiPolygon()
    if have_control and not ru_read.is_empty:
        ru_ahead = G.polys(G.valid(G.ll(G.ea(ru_read).difference(G.ea(ru)))), 1.0)

    log("events…")
    events, unlocated = load_events(gaz, front if not front.is_empty else None)
    log(f"  {len(events)} located, {len(unlocated)} unmatched")
    ev_zone, hotspots = event_uncertainty(events)

    # ---- uncertainty: where the sources disagree, plus where reports cluster
    # where the source maps do not agree: the union minus the part they all share. A cut polygon, not a blur.
    disagreement = MultiPolygon()
    map_geoms = [r["geom"] for r in readings if r["kind"] == "map"]
    if len(map_geoms) > 1:
        u = G.ea(map_geoms[0]).buffer(0)
        i = G.ea(map_geoms[0]).buffer(0)
        for g in map_geoms[1:]:
            ge = G.ea(g).buffer(0)
            u = u.union(ge)
            i = i.intersection(ge)
        disagreement = G.polys(G.valid(G.ll(u.difference(i))), 1.0)
    claim_gap = MultiPolygon()
    if have_control and not claimed.is_empty:
        claim_gap = G.polys(G.valid(G.ll(G.ea(claimed).difference(G.ea(ru)))), 1.0)

    uncertain = G.polys(G.valid(unary_union([g for g in (grey, ru_my["grey"], disagreement) if not g.is_empty])), 0.5)
    feather_rings = []   # no soft edges: every boundary on this map is a cut line

    # symmetry: how much each side actually contributed, shown on the page
    def side_counts(side):
        loc = [e for e in events if e["side"] == side]
        unl = [e for e in unlocated if e["side"] == side]
        return {"located": len(loc), "unmatched": len(unl),
                "sources": sorted({e["src"] for e in loc} | {e["src"] for e in unl})}
    balance = {
        "ru": side_counts("ru"), "ua": side_counts("ua"), "mapper": side_counts("mapper"),
        "geometry": {"base": base_source if have_control else None,
                     "russian": sorted(ru_sources) or None,
                     "claims": "isw" if "claimed" in isw else ("source map" if not my["ru_claimed"].is_empty else None)},
    }

    # ---- our own map: four rules over the same inputs
    log("our line…")
    map_reads = [r for r in readings if r["kind"] == "map"]
    claim_reads = [r for r in readings if r["kind"] == "claims"]
    n_maps = len(map_reads)

    def coverage_at_least(k):
        """Ground that at least k of the map sources place under Russian control."""
        if n_maps == 0 or k <= 0:
            return MultiPolygon()
        if k == 1:
            return G.polys(G.valid(unary_union([r["geom"] for r in map_reads])), C.MIN_POLY_KM2)
        acc = MultiPolygon()
        seen = []
        for r in map_reads:
            g = G.ea(r["geom"])
            for prev in seen:
                acc = G.polys(G.valid(G.ll(unary_union([G.ea(acc), g.intersection(prev)]))), C.MIN_POLY_KM2) if k == 2 else acc
            seen.append(g)
        if k == 2:
            return acc
        # k >= 3: count overlaps pairwise-free by summing indicator via successive intersections
        from itertools import combinations
        parts = []
        for combo in combinations(seen, k):
            inter = combo[0]
            for g in combo[1:]:
                inter = inter.intersection(g)
                if inter.is_empty:
                    break
            if not inter.is_empty:
                parts.append(inter)
        return G.polys(G.valid(G.ll(unary_union(parts))), C.MIN_POLY_KM2) if parts else MultiPolygon()

    event_taken = MultiPolygon()
    taken_pts = [Point(e["lon"], e["lat"]) for e in events
                 if e["kind"] in ("ru_taken", "ru_advance") and e["side"] in ("ru", "mapper")]
    if taken_pts:
        event_taken = G.polys(G.valid(G.buffer_km(unary_union(taken_pts), 3.0)), 0.2)

    majority = max(2, (n_maps + 1) // 2) if n_maps > 1 else 1
    leans = {}
    leans["confirmed"] = coverage_at_least(min(2, n_maps))
    leans["balanced"] = coverage_at_least(majority)
    leans["forward"] = coverage_at_least(1)
    maximal_parts = [g for g in [leans["forward"]] + [r["geom"] for r in claim_reads] + [event_taken] if not g.is_empty]
    leans["maximal"] = G.polys(G.valid(unary_union(maximal_parts)), C.MIN_POLY_KM2) if maximal_parts else MultiPolygon()

    lean_out = {}
    for k, g in leans.items():
        front_k = MultiLineString()
        if not g.is_empty:
            front_k = G.lines(G.ll(G.ea(g).boundary.intersection(G.ea(ukraine).buffer(-2500))))
        lean_out[k] = {"c": G.poly_coords(G.simplify_m(g, 200), 5),
                       "front": G.line_coords(G.simplify_m(front_k, 200), 5),
                       "km2": round(G.km2(g)) if not g.is_empty else 0,
                       "front_km": round(G.km(front_k)) if not front_k.is_empty else 0}
    lean_out["_meta"] = {"n_maps": n_maps, "majority": majority,
                         "sources": [r["id"] for r in map_reads], "claims": [r["id"] for r in claim_reads],
                         "events_used": len(taken_pts)}

    # ---- per-source outlines for the toggles
    source_layers = [{"id": r["id"], "kind": r["kind"],
                      "km2": round(G.km2(r["geom"])),
                      "c": G.poly_coords(G.simplify_m(r["geom"], 400), 4)} for r in readings]

    # ---- per-place perspective: what each source says about one settlement
    log("perspectives…")
    perspectives = []
    if readings:
        span = G.polys(G.valid(unary_union([r["geom"] for r in readings])))
        band = G.ea(span.boundary).buffer(35000)
        prepared = [(r["id"], G.ea(r["geom"])) for r in readings]
        ev_by_place = defaultdict(list)
        for e in events:
            ev_by_place[(round(e["lat"], 3), round(e["lon"], 3))].append(e)
        cands = [p for p in gaz.places if p["p"] >= 500]
        for p in cands:
            pt = G.ea(Point(p["lon"], p["lat"]))
            if not band.contains(pt):
                continue
            says = {sid: ("in" if g.contains(pt) else "out") for sid, g in prepared}
            local = []
            for (lat, lon), items in ev_by_place.items():
                if abs(lat - p["lat"]) < .07 and abs(lon - p["lon"]) < .10:
                    local.extend(items)
            if len(set(says.values())) < 2 and not local:
                continue          # everyone agrees and nothing was reported: not interesting
            perspectives.append({
                "n": p["n"], "uk": p.get("uk", ""), "lat": p["lat"], "lon": p["lon"], "p": p["p"],
                "says": says,
                "reports": sorted(({"d": e["d"], "k": e["kind"], "s": e["side"], "src": e["src"], "u": e["url"]}
                                   for e in local), key=lambda x: x["d"], reverse=True)[:6],
            })
            if len(perspectives) >= 400:
                break
        perspectives.sort(key=lambda x: (-len(x["reports"]), -x["p"]))
    log(f"  {len(perspectives)} places where sources disagree or something was reported")

    # labels: everything within reach of the fighting, then only real towns further out
    if have_control:
        band = G.ea(front).buffer(70000)
        page_places = []
        for p in gaz.places:
            pt = G.ea(Point(p["lon"], p["lat"]))
            near = band.contains(pt)
            if near or p["p"] >= 2000:
                page_places.append({"n": p["n"], "uk": p["uk"], "p": p["p"],
                                    "lat": p["lat"], "lon": p["lon"], "f": 1 if near else 0})
    else:
        page_places = [{**p, "f": 0} for p in gaz.page_places()]
    log(f"  {len(page_places)} places carried for labels and search")

    log("history…")
    save_snapshot(ru, uncertain, base_source)
    series, anchors, changes, last_change = build_history(ru) if have_control else ([], [], [], None)

    # ---- stats
    def delta(days):
        if not series:
            return None
        target = (datetime.strptime(series[-1]["d"], "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        prev = [s for s in series if s["d"] <= target]
        return round(series[-1]["a"] - prev[-1]["a"], 1) if prev else None

    stats = {
        "date": TODAY,
        "have_control": have_control,
        "base_source": base_source,
        "ru_km2": round(G.km2(ru)) if have_control else None,
        "ua_held_ru_km2": round(G.km2(ua_held)) if not ua_held.is_empty else 0,
        "uncertain_km2": round(G.km2(uncertain)) if not uncertain.is_empty else 0,
        "claim_gap_km2": round(G.km2(claim_gap)) if not claim_gap.is_empty else 0,
        "ru_read_km2": round(G.km2(ru_read)) if not ru_read.is_empty else 0,
        "ru_ahead_km2": round(G.km2(ru_ahead)) if not ru_ahead.is_empty else 0,
        "front_km": round(G.km(front)) if have_control else None,
        "d1": delta(1), "d7": delta(7), "d30": delta(30), "d90": delta(90), "d365": delta(365),
        "since": series[0]["d"] if series else None,
        "n_snapshots": len(series),
        "lean_km2": {k: v["km2"] for k, v in lean_out.items() if not k.startswith("_")},
        "last_change": last_change,
        "crimea_km2": round(G.km2(ru.intersection(crimea))) if have_control else None,
    }

    data = {
        "built": NOW.strftime("%Y-%m-%d %H:%M UTC"),
        "stats": stats,
        "control": {
            "ru": G.poly_coords(ru, 5),
            "ua_held": G.poly_coords(ua_held, 5),
            "claimed": G.poly_coords(claimed, 5),
            "front": G.line_coords(front, 5),
        },
        "uncertainty": {
            "zone": G.poly_coords(G.simplify_m(uncertain, 300), 4) if not uncertain.is_empty else [],
            "feather": feather_rings,
            "disagreement": G.poly_coords(G.simplify_m(disagreement, 400), 4) if not disagreement.is_empty else [],
            "claim_gap": G.poly_coords(G.simplify_m(claim_gap, 400), 4) if not claim_gap.is_empty else [],
            "hotspots": hotspots[:60],
        },
        "ours": lean_out,
        "source_layers": source_layers,
        "perspectives": perspectives,
        "russian_read": {
            "control": G.poly_coords(ru_read, 5),
            "ahead": G.poly_coords(G.simplify_m(ru_ahead, 300), 4) if not ru_ahead.is_empty else [],
            "advances": [a for a in ({"n": l["n"], "c": l["c"], "src": l["src"]} for l in ru_lines)
                         if _near_ukraine(a["c"])][:400],
            "markers": ru_points[:400],
        },
        "isw": {k: (G.poly_coords(G.polys(v), 5) if v.geom_type in ("Polygon", "MultiPolygon") else G.line_coords(v, 5))
                for k, v in isw.items()},
        "advances": [a for a in (
            {"n": l["n"], "c": l["c"], "src": l["src"]} for l in my_lines if l["cls"] in ("advance", None))
            if _near_ukraine(a["c"])][:400],
        "markers": my_points[:600],
        "events": [{"d": e["d"], "k": e["kind"], "s": e["side"], "src": e["src"], "u": e["url"],
                    "n": e.get("place", e["name"]), "raw": e["name"], "lat": e["lat"], "lon": e["lon"],
                    "t": e["text"][:220], "m": e["match"]} for e in events[:800]],
        "unlocated": [{"d": e["d"], "k": e["kind"], "s": e["side"], "src": e["src"], "u": e["url"], "n": e["name"], "m": e["match"]}
                      for e in unlocated[:200]],
        "series": series,
        "anchors": anchors,
        "changes": changes[:400],
        "places": page_places,
        "basemap": basemap,
        "sources": {
            **{f"mymaps:{k}": v for k, v in my_sources.items()},
            **{f"russian map:{k}": v for k, v in ru_sources.items()},
            **{f"isw:{k}": v for k, v in isw_meta.items()},
            "events_file": "yes" if os.path.exists(os.path.join(LIVE, "events.json")) else "missing",
            "gazetteer": "full GeoNames dump" if gaz.full else "seed list, population over 1,000",
        },
        "classification": class_report + ru_class_report,
        "balance": balance,
    }

    os.makedirs(P("out"), exist_ok=True)
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    open(P("out", "data.json"), "w", encoding="utf-8").write(js)
    log(f"data.json {len(js)/1e6:.2f} MB")

    tpl = open(P("template.html"), encoding="utf-8").read()
    html = (tpl.replace("/*__LEAFLET_CSS__*/", open(P("vendor", "leaflet.css"), encoding="utf-8").read())
               .replace("/*__LEAFLET_JS__*/", open(P("vendor", "leaflet.js"), encoding="utf-8").read())
               .replace("/*__DATA__*/", js.replace("</", "<\\/")))
    open(P("out", "index.html"), "w", encoding="utf-8").write(html)
    log(f"index.html {len(html)/1e6:.2f} MB")
    log(json.dumps({k: stats[k] for k in ("have_control", "base_source", "ru_km2", "uncertain_km2", "n_snapshots")}))
    return data


if __name__ == "__main__":
    main(self_test="--self-test" in sys.argv)
