"""
Turn a settlement name as written in a Telegram post into coordinates.

Two hard parts, both handled here:
  1. Slavic case endings. Posts say "в районе Мирного", "бої у Костянтинівці", so the name arrives in the
     genitive or locative and never matches the nominative in the gazetteer.
  2. Russian and Ukrainian spell the same village differently (Николаевка / Миколаївка, Красное / Червоне).

Strategy: generate candidate nominatives from the written form, look each up in an index keyed by every
name the gazetteer knows (Latin, Ukrainian, Russian, alternates), then disambiguate by distance to the front.
"""
import json
import math
import os
import re
import unicodedata

import config as C

ROOT = os.path.dirname(os.path.abspath(__file__))
FULL = os.environ.get("WARMAP_GAZETTEER") or os.path.join(ROOT, "gazetteer", "ua_full.json")
SEED = os.path.join(ROOT, "gazetteer", "ua_cities1000.json")

# ---------------------------------------------------------------- transliteration of the Latin forms

NAME_FIX = {"Syevyerodonetsk": "Sievierodonetsk", "Odessa": "Odesa", "Kiev": "Kyiv", "Kharkov": "Kharkiv",
            "Nikolayev": "Mykolaiv", "Rovno": "Rivne", "Lvov": "Lviv", "Krasnyy Lyman": "Lyman",
            "Kupyansk": "Kupiansk", "Kupjansk": "Kupiansk", "Artemovsk": "Bakhmut", "Ugledar": "Vuhledar"}
_CONS = "bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ"


def modern_name(n):
    """GeoNames ascii names use the old BGN scheme; move them to the 2010 Ukrainian national scheme."""
    n = re.sub(r"[\u2019'`]", "", n or "")
    n = re.sub(r"yy\b", "yi", n)
    n = re.sub(r"iyi", "ii", n)
    n = re.sub(r"iya\b", "iia", n)
    n = re.sub(r"iye", "iie", n)
    n = re.sub(r"([aeiou])yi", r"\1i", n)
    n = re.sub(rf"(?<=[{_CONS}])y([au])", r"i\1", n)
    n = re.sub(rf"ay(?=[{_CONS}]|\b)", "ai", n)
    return NAME_FIX.get(n, n)


# ---------------------------------------------------------------- Cyrillic normalisation and de-declension

def norm(s):
    s = unicodedata.normalize("NFC", (s or "").strip().lower())
    s = re.sub(r"[\u2019'`ʼʹ’]", "", s)
    # fold the letters that differ only between the two orthographies
    s = (s.replace("ё", "е").replace("ъ", "").replace("ы", "и").replace("э", "е")
           .replace("і", "и").replace("ї", "и").replace("є", "е").replace("ґ", "г"))
    return re.sub(r"\s+", " ", s)


# suffix -> possible nominative endings. Ordered longest first; applied to the normalised form.
_DECL = [
    ("ому", ["е", "ий", "ой"]), ("ого", ["е", "ий", "ой"]), ("ому", ["ий"]),
    ("ською", ["ське"]), ("ской", ["ское"]), ("ською", ["ська"]),
    ("івці", ["івка"]), ("овці", ["овка"]), ("евці", ["евка"]), ("инці", ["инка"]),
    ("івку", ["івка"]), ("овку", ["овка"]), ("евку", ["евка"]),
    ("івки", ["івка"]), ("овки", ["овка"]), ("евки", ["евка"]),
    ("івськ", ["івськ"]), ("івці", ["івці"]),
    ("ому", ["о"]), ("ями", ["я"]), ("ами", ["а"]),
    ("ці", ["ка", "ця"]), ("ці", ["ця"]), ("ке", ["ке"]),
    ("ку", ["ка"]), ("ки", ["ка"]), ("ке", ["ка"]),
    ("не", ["не"]), ("ном", ["не", "но"]), ("ном", ["н"]),
    ("ої", ["а"]), ("ій", ["а", "я"]), ("ою", ["а"]),
    ("ім", ["е"]), ("ем", ["е"]), ("ом", ["о", ""]),
    ("ах", ["и"]), ("ях", ["і"]),
    ("у", ["а", "о", ""]), ("ю", ["я", "е", ""]),
    ("и", ["а", "е", "о", ""]), ("і", ["а", "я", "е", ""]),
    ("е", ["е", "а", "о"]), ("а", ["а", "о", ""]),
    ("я", ["я", "е"]), ("ой", ["ая", "ое"]), ("ей", ["ея"]),
]


_DECL_N = None


def _decl_table():
    """The table above is written in ordinary spelling; normalisation folds і/ї/є/ы, so fold the table too."""
    global _DECL_N
    if _DECL_N is None:
        seen, out = set(), []
        for suf, repls in _DECL:
            k = (norm(suf), tuple(norm(r) for r in repls))
            if k not in seen:
                seen.add(k)
                out.append((k[0], list(k[1])))
        out.sort(key=lambda x: -len(x[0]))
        _DECL_N = out
    return _DECL_N


# Names whose declension is irregular enough that the rules above cannot recover the nominative.
IRREGULAR = {
    "часовом яре": "часив яр", "часовому яру": "часив яр", "часов яр": "часив яр",
    "кривом розе": "кривий риг", "кривому розi": "кривий риг",
    "билои церкви": "била церква", "билой церкви": "била церква",
    "камянце подильском": "камянець подильський",
    "красном лимане": "лиман", "краснии лиман": "лиман",
    "артемовске": "бахмут", "артемовск": "бахмут",
    "угледаре": "вугледар", "угледар": "вугледар",
    "красноармеиске": "покровськ", "красноармеиск": "покровськ",
    "димитрове": "мирноград", "димитров": "мирноград",
    "селидово": "селидове", "курахово": "курахове",
}


def variants(name, depth=0):
    """Candidate nominative forms of a written name, normalised."""
    base = norm(name)
    out = {base}
    if base in IRREGULAR:
        out.add(IRREGULAR[base])
    for suf, repls in _decl_table():
        if base.endswith(suf) and len(base) > len(suf) + 2:
            stem = base[: -len(suf)]
            for r in repls:
                out.add(stem + r)
    # multiword: decline the tail, and the head too ("часовом яре" -> "часив яр", "великій новосілці")
    if " " in base and depth < 1:
        head, _, tail = base.rpartition(" ")
        heads = variants(head, depth + 1) | {head}
        tails = variants(tail, depth + 1) | {tail}
        for h in list(heads)[:12]:
            for t in list(tails)[:12]:
                out.add(f"{h} {t}")
    # Russian <-> Ukrainian stems that survive normalisation only partly
    swaps = [("николаев", "миколаив"), ("красн", "червон"), ("новоселк", "новосилк"), ("петровск", "петривск"),
             ("александр", "олександр"), ("владимир", "володимир"), ("днепр", "днипр"), ("серебр", "срибн"),
             ("белогор", "билогир"), ("бел", "бил"), ("черн", "чорн"), ("зелен", "зелен"), ("степн", "степов")]
    for a, b in swaps:
        for v in list(out):
            if a in v:
                out.add(v.replace(a, b))
            if b in v:
                out.add(v.replace(b, a))
    return {v for v in out if len(v) >= 3}


class Gazetteer:
    def __init__(self):
        self.full = os.path.exists(FULL)
        raw = json.load(open(FULL if self.full else SEED, encoding="utf-8"))
        self.places, self.index = [], {}
        for p in raw:
            lon, lat = p["lon"], p["lat"]
            if not (C.BBOX[0] <= lon <= C.BBOX[2] and C.BBOX[1] <= lat <= C.BBOX[3]):
                continue
            rec = {"n": modern_name(p["n"]), "uk": p.get("uk") or "", "p": int(p.get("p") or 0),
                   "lat": lat, "lon": lon, "cc": p.get("cc", "UA")}
            self.places.append(rec)
            for key in {rec["n"], p["n"], rec["uk"], *(p.get("alt") or [])}:
                k = norm(key)
                if k:
                    self.index.setdefault(k, []).append(rec)
        self.places.sort(key=lambda r: -r["p"])

    def page_places(self, min_pop=1000):
        """What the page carries for labels and search."""
        out = [p for p in self.places if p["p"] >= min_pop] if self.full else list(self.places)
        return [{"n": p["n"], "uk": p["uk"], "p": p["p"], "lat": p["lat"], "lon": p["lon"]} for p in out]

    def lookup(self, name):
        hits, seen = [], set()
        for v in variants(name):
            for r in self.index.get(v, []):
                key = (r["lat"], r["lon"])
                if key not in seen:
                    seen.add(key)
                    hits.append(r)
        return hits

    def geocode(self, name, near=None, max_km=60):
        """Best match. `near` is a shapely geometry (usually the front line) used to disambiguate
        between the dozens of villages that share a name."""
        hits = self.lookup(name)
        if not hits:
            return None, "unmatched"
        if near is None or near.is_empty:
            return max(hits, key=lambda r: r["p"]), ("unique" if len(hits) == 1 else "ambiguous")
        from shapely.geometry import Point
        scored = []
        for r in hits:
            d = near.distance(Point(r["lon"], r["lat"])) * 78.0   # deg -> km at ~48N, good enough to rank
            scored.append((d, r))
        scored.sort(key=lambda x: x[0])
        best_d, best = scored[0]
        if best_d > max_km:
            return None, "far"
        if len(scored) > 1 and scored[1][0] - best_d < 8:
            return best, "ambiguous"
        return best, "matched"


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
