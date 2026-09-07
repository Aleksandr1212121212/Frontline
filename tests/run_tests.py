#!/usr/bin/env python3
"""Offline checks. Run: python tests/run_tests.py"""
import io, json, os, subprocess, sys, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("WARMAP_GAZETTEER", os.path.join(ROOT, "tests", "gaz_fixture.json"))
fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))
    if not cond: fails.append(name)

# the suite writes its own fixtures so it runs on a fresh clone
LIVE_T = os.path.join(ROOT, "tests", "live")
os.makedirs(LIVE_T, exist_ok=True)

from sources import kml
raw = open(os.path.join(ROOT, "tests", "fixture_mymaps.kml"), "rb").read()
json.dump({"fetched": "fixture", "url": "fixture", **kml.parse(raw)},
          open(os.path.join(LIVE_T, "kalibrated.json"), "w"), ensure_ascii=False)
ru_raw = open(os.path.join(ROOT, "tests", "fixture_rumap.kml"), encoding="utf-8").read().encode()
json.dump({"fetched": "fixture", "url": "fixture", **kml.parse(ru_raw)},
          open(os.path.join(LIVE_T, "rumap_creamy_caprice.json"), "w"), ensure_ascii=False)
json.dump({"type": "FeatureCollection", "title": "Assessed Russian Controlled", "fetched": "fixture", "features": [
    {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates":
     [[[37.15, 48.05], [38.4, 48.05], [38.4, 48.95], [37.15, 48.95], [37.15, 48.05]]]}}]},
    open(os.path.join(LIVE_T, "isw_control.geojson"), "w"))
json.dump({"type": "FeatureCollection", "title": "Claimed Russian Territory", "fetched": "fixture", "features": [
    {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates":
     [[[36.85, 47.95], [38.6, 47.95], [38.6, 49.1], [36.85, 49.1], [36.85, 47.95]]]}}]},
    open(os.path.join(LIVE_T, "isw_claimed.geojson"), "w"))
json.dump([
    {"src": "rybar", "side": "ru", "post": "rybar/1", "url": "https://t.me/rybar/1", "d": "2026-09-06", "kind": "ru_taken", "name": "Кутьковка", "text": "Освобождена Кутьковка"},
    {"src": "rybar", "side": "ru", "post": "rybar/2", "url": "https://t.me/rybar/2", "d": "2026-09-06", "kind": "contested", "name": "Константиновку", "text": "Бои за Константиновку"},
    {"src": "dva_majors", "side": "ru", "post": "dva_majors/3", "url": "https://t.me/dva_majors/3", "d": "2026-09-05", "kind": "ru_advance", "name": "Мирного", "text": "Продвижение в районе Мирного"},
    {"src": "GeneralStaffZSU", "side": "ua", "post": "GeneralStaffZSU/4", "url": "https://t.me/GeneralStaffZSU/4", "d": "2026-09-05", "kind": "contested", "name": "Костянтинівці", "text": "Бої у Костянтинівці"},
    {"src": "GeneralStaffZSU", "side": "ua", "post": "GeneralStaffZSU/5", "url": "https://t.me/GeneralStaffZSU/5", "d": "2026-09-04", "kind": "infiltration", "name": "Миколаївці", "text": "ДРГ у Миколаївці"},
    {"src": "kalibrated", "side": "mapper", "post": "kalibrated/6", "url": "https://t.me/kalibrated/6", "d": "2026-09-04", "kind": "ru_taken", "name": "Заповідного", "text": "Zapovidne under Russian control"},
    {"src": "rybar", "side": "ru", "post": "rybar/7", "url": "https://t.me/rybar/7", "d": "2026-09-03", "kind": "ru_taken", "name": "Небывалово", "text": "unmatched on purpose"},
], open(os.path.join(LIVE_T, "events.json"), "w"), ensure_ascii=False)
doc = kml.parse(raw)
names = [l["name"] for l in doc["layers"]]
check("kml: folders", names == ["Russian Control", "Ukrainian Control (Russian territory)", "Contested / Grey Zone", "Russian Advances"], str(names))
ru = doc["layers"][0]["features"]
check("kml: multigeometry split", len(ru) == 3, str(len(ru)))
check("kml: polygon hole kept", len(ru[0]["coords"]) == 2)
check("kml: stylemap resolved to colour", ru[0]["fill"] == "#c2185b", str(ru[0]["fill"]))
check("kml: extended data", ru[0]["data"].get("updated") == "2026-09-05")
adv = doc["layers"][3]["features"]
check("kml: line and point kinds", sorted(f["kind"] for f in adv) == ["line", "point"])
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z: z.writestr("doc.kml", raw)
check("kml: kmz", len(kml.parse(buf.getvalue())["layers"]) == 4)

from sources import lostarmour
la_ev = lostarmour.parse(open(os.path.join(ROOT, "tests", "fixture_lostarmour.txt"), encoding="utf-8").read(), "2026-09-06", "u")
la_names = {e["name"] for e in la_ev}
check("lostarmour: control bullets read", {"Казачьей Лопани", "Диброва", "Часовом Яре", "Ижевки"} <= la_names, str(sorted(la_names)))
check("lostarmour: multiple names in one bullet", {"Доброполье", "Матяшево"} <= la_names)
check("lostarmour: place before the verb kept", "Орехове" in la_names)
check("lostarmour: no adjective or verb junk", not ({"восточной", "Увеличена", "жилой", "районе", "Сумской"} & la_names), str(sorted(la_names)))
check("lostarmour: axis captured", any(e["grouping"] == "Запад" for e in la_ev))
check("lostarmour: dated entries keep their own date", any(e["d"] == "2026-09-05" for e in la_ev))

from sources import fetch
ru_txt = "Освобождены населённые пункты Кутьковка, Заповедное. Продвинулись в районе Мирного. Бои за Константиновку. ДРГ противника у Николаевки."
ua_txt = "Ворог окупував Кутьківку та просунувся поблизу Заповідного. Сили оборони звільнили Дворічну. Бої у Костянтинівці."
en_txt = "Russian forces have entered Dobropillya and Hannivka. Fighting near Druzhkivka."
got_ru = dict(fetch._extract(ru_txt, fetch.RU_PATTERNS))
check("text: ru taken", got_ru.get("Кутьковка") == "ru_taken" and got_ru.get("Заповедное") == "ru_taken", str(got_ru))
check("text: ru advance/contested/drg", {"Мирного", "Константиновку", "Николаевки"} <= set(got_ru), str(got_ru))
got_ua = dict(fetch._extract(ua_txt, fetch.UA_PATTERNS))
check("text: ua retaken + occupied", got_ua.get("Дворічну") == "ua_retaken" and got_ua.get("Кутьківку") == "ru_taken", str(got_ua))
got_en = dict(fetch._extract(en_txt, fetch.EN_PATTERNS))
check("text: en list split", {"Dobropillya", "Hannivka"} <= set(got_en), str(got_en))

from gazetteer import Gazetteer
from shapely.geometry import LineString
gaz = Gazetteer()
front = LineString([(37.4, 49.9), (37.7, 48.8), (37.2, 48.3), (36.5, 47.8)])
cases = {"Кутьковка": "Kutkivka", "Заповедное": "Zapovidne", "Мирного": "Myrne", "Константиновку": "Kostiantynivka",
         "Николаевки": "Mykolaivka", "Костянтинівці": "Kostiantynivka", "Часовом Яре": "Chasiv Yar",
         "Великій Новосілці": "Velyka Novosilka", "Красноармейск": "Pokrovsk"}
bad = {k: (gaz.geocode(k, near=front)[0] or {}).get("n") for k, v in cases.items()
       if (gaz.geocode(k, near=front)[0] or {}).get("n") != v}
check("geocode: declined and cross-orthography names", not bad, str(bad))
check("geocode: nonsense rejected", gaz.geocode("Небывалово", near=front)[0] is None)
check("geocode: right Kostiantynivka picked", abs(gaz.geocode("Костянтинівці", near=front)[0]["lat"] - 48.527) < 0.01)

import shutil
shutil.rmtree(os.path.join(ROOT, "cache", "snapshots"), ignore_errors=True)
import build
data = build.main(self_test=True)
check("build: control geometry present", data["stats"]["have_control"])
check("build: ua-held inside russia", data["stats"]["ua_held_ru_km2"] > 0)
check("build: uncertainty derived", data["stats"]["uncertain_km2"] > 0)
check("build: layers classified", {c["class"] for c in data["classification"]} >= {"ru_control", "ua_control", "grey", "advance"})
check("build: events located", len(data["events"]) >= 4 and any(e["m"] == "matched" for e in data["events"]))
check("build: unmatched surfaced not dropped", len(data["unlocated"]) >= 1)
check("build: no deepstate anywhere", "deepstate" not in json.dumps(data).lower())
check("build: russian-side geometry loaded", len(data["russian_read"]["control"]) > 0)
check("build: russian reading measured against base", data["stats"]["ru_ahead_km2"] > 0)
check("build: both sides counted", data["balance"]["ru"]["located"] > 0 and data["balance"]["ua"]["located"] > 0)
check("build: russian geometry named in balance", bool(data["balance"]["geometry"]["russian"]))

html = open(os.path.join(ROOT, "out", "index.html"), encoding="utf-8").read()
check("page: data embedded", '"have_control"' in html)
check("page: leaflet inlined", "L.Map" in html or "leaflet" in html.lower())
node = subprocess.run(["node", "--version"], capture_output=True)
if node.returncode == 0:
    js = html.split('<script id="data"')[0].split("<script>")[-1].split("</script>")[0]
    open("/tmp/_lf.js", "w").write(js)
    r = subprocess.run(["node", "--check", "/tmp/_lf.js"], capture_output=True, text=True)
    check("page: leaflet block parses", r.returncode == 0, r.stderr[:200])
    app = html.rsplit("<script>", 1)[-1].rsplit("</script>", 1)[0]
    open("/tmp/_app.js", "w").write(app)
    r = subprocess.run(["node", "--check", "/tmp/_app.js"], capture_output=True, text=True)
    check("page: app script parses", r.returncode == 0, r.stderr[:200])

# leave the tree as the user would ship it: page rebuilt from the real live/ directory
shutil.rmtree(os.path.join(ROOT, "cache", "snapshots"), ignore_errors=True)
os.makedirs(os.path.join(ROOT, "cache", "snapshots"), exist_ok=True)
build.LIVE = os.path.join(ROOT, "live")
build.main(self_test=False)
shutil.copyfile(os.path.join(ROOT, "out", "index.html"), os.path.join(ROOT, "docs", "index.html"))
print(("\nall passed" if not fails else f"\n{len(fails)} failed: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
