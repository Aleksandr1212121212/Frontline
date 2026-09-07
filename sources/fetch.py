#!/usr/bin/env python3
"""
Fetch every live source. Each one is independent: a failure logs and leaves the previous good file in place.

  python -m sources.fetch            all sources
  python -m sources.fetch kalibrated isw telegram gazetteer

Writes into live/ and cache/:
  live/kalibrated.kml            raw KML as served (kept so a parse bug can be re-run without refetching)
  live/kalibrated.json           parsed layers
  live/mymaps_<name>.json        any EXTRA_MYMAPS
  live/isw_<key>.geojson         claimed / control / advances / fortifications / partisan
  live/events.json               settlement-level reports from Telegram, both sides
  gazetteer/ua_full.json         GeoNames places incl. Cyrillic names and villages
  cache/snapshots/<date>.json    daily control snapshot, this is what the time slider is built from

None of these hosts is reachable from the machine this was written on; the first CI run is the real test.
Every fetcher prints "ok" with a count, or "failed" with the reason.
"""
import io
import html
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C
from sources import kml as kmlmod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
for d in ("live", "cache", "cache/snapshots", "gazetteer"):
    os.makedirs(P(d), exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      "Accept-Language": "en,uk;q=0.8,ru;q=0.7"}
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")


def log(*a):
    print(NOW.strftime("%H:%M:%S"), *a, flush=True)


def get(url, **kw):
    r = requests.get(url, headers={**UA, **kw.pop("headers", {})}, timeout=kw.pop("timeout", 90), **kw)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- Google My Maps

def fetch_mymap(mid, slug):
    last = None
    for tmpl in C.KALIBRATED_URLS:
        url = tmpl.format(mid=mid)
        try:
            r = get(url)
            body = r.content
            if body[:2] != b"PK" and b"<kml" not in body[:4000]:
                raise ValueError(f"not KML (content-type {r.headers.get('content-type')}, {len(body)} bytes)")
            parsed = kmlmod.parse(body)
            if not parsed["layers"]:
                raise ValueError("KML parsed but has no layers")
            open(P("live", f"{slug}.kml"), "wb").write(body)
            json.dump({"fetched": NOW.strftime("%Y-%m-%d %H:%M UTC"), "url": url, **parsed},
                      open(P("live", f"{slug}.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
            n = sum(len(l["features"]) for l in parsed["layers"])
            log(f"{slug} ok: {len(parsed['layers'])} layers, {n} features, from {url.split('?')[0]}")
            log("   layers: " + "; ".join(f"{l['name']} ({len(l['features'])})" for l in parsed["layers"]))
            return True
        except Exception as e:
            last = e
            log(f"{slug}: {url.split('?')[0]} failed: {e}")
    log(f"{slug} failed, keeping previous file. Last error: {last}")
    return False


def fetch_kalibrated():
    ok = fetch_mymap(C.KALIBRATED_MID, "kalibrated")
    for name, mid in C.EXTRA_MYMAPS.items():
        fetch_mymap(mid, f"mymaps_{name}")
    return ok


def fetch_russian_map():
    """The Russian-side control map, same My Maps mechanism, kept in its own namespace."""
    ok = False
    for name, mid in C.RU_MYMAPS.items():
        ok = fetch_mymap(mid, f"rumap_{name}") or ok
    return ok


def fetch_lostarmour():
    """Daily situation report: axis sections and control-change bullets, in Russian."""
    from sources import lostarmour as la
    path = P("live", "events.json")
    events = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    seen = {(e["src"], e.get("post") or e.get("url"), e["name"], e["d"]) for e in events}
    added = 0
    for back in range(C.LOSTARMOUR_DAYS):
        day = NOW - timedelta(days=back)
        url = C.LOSTARMOUR_SUMMARY.format(d=day.strftime("%d-%m-%Y"))
        try:
            page = get(url).text
        except Exception as e:
            log(f"lostarmour {day:%Y-%m-%d}: {e}")
            continue
        try:
            found = la.parse(page, day.strftime("%Y-%m-%d"), url)
        except Exception as e:
            log(f"lostarmour {day:%Y-%m-%d} parse failed: {e}")
            continue
        for ev in found:
            key = (ev["src"], url, ev["name"], ev["d"])
            if key in seen:
                continue
            seen.add(key)
            events.append({**ev, "post": url})
            added += 1
        log(f"lostarmour {day:%Y-%m-%d}: {len(found)} mentions")
    cutoff = (NOW - timedelta(days=C.EVENT_KEEP_DAYS)).strftime("%Y-%m-%d")
    events = [e for e in events if e["d"] >= cutoff]
    events.sort(key=lambda e: e["d"], reverse=True)
    json.dump(events, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"lostarmour: {added} new mentions")
    return added > 0


# ---------------------------------------------------------------- ISW

def fetch_isw():
    found, all_titles = {}, set()
    for item in C.ISW_ITEMS:
        try:
            data = get(f"https://www.arcgis.com/sharing/rest/content/items/{item}/data?f=json").json()
        except Exception as e:
            log(f"ISW web map {item} failed: {e}")
            continue
        for ly in data.get("operationalLayers", []) + data.get("baseMap", {}).get("baseMapLayers", []):
            title = (ly.get("title") or ly.get("id") or "").lower().replace("_", " ")
            all_titles.add(ly.get("title") or ly.get("id") or "?")
            url = ly.get("url")
            if not url:
                continue
            for kw, key in C.ISW_WANT.items():
                if kw in title and key not in found:
                    found[key] = (url, ly.get("title"))
    if not found:
        log("ISW: none of the wanted layers matched. Titles seen: " + ", ".join(sorted(all_titles)))
        return False
    missing = sorted(set(C.ISW_WANT.values()) - set(found))
    if missing:
        log(f"ISW: no layer matched for {missing}. Titles seen: " + ", ".join(sorted(all_titles)))
    ok = False
    for key, (url, title) in found.items():
        try:
            feats, offset = [], 0
            while True:
                q = get(f"{url}/query", params={"where": "1=1", "outFields": "*", "outSR": 4326,
                                                "f": "geojson", "resultOffset": offset, "resultRecordCount": 1000}).json()
                if "error" in q:
                    raise RuntimeError(q["error"])
                batch = q.get("features", [])
                feats.extend(batch)
                if len(batch) < 1000:
                    break
                offset += 1000
                if offset > 60000:
                    break
            if not feats:
                raise ValueError("no features")
            json.dump({"type": "FeatureCollection", "title": title, "fetched": NOW.strftime("%Y-%m-%d %H:%M UTC"),
                       "features": feats}, open(P("live", f"isw_{key}.geojson"), "w"), separators=(",", ":"))
            log(f"ISW ok: {title} -> isw_{key} ({len(feats)} features)")
            ok = True
        except Exception as e:
            log(f"ISW {key} failed: {e}")
    return ok


# ---------------------------------------------------------------- Telegram, both sides

RU_PATTERNS = [
    (r"освобожд[её]н(?:ы|а|о)?\s+(?:населённ\w+|населенн\w+)?\s*(?:пункт[ыа]?\s+)?([А-ЯЁ][\w\-]+(?:\s*,\s*[А-ЯЁ][\w\-]+)*)", "ru_taken"),
    (r"освободили\s+(?:населённ\w+\s+пункт\s+)?([А-ЯЁ][\w\-]+(?:\s*,\s*[А-ЯЁ][\w\-]+)*)", "ru_taken"),
    (r"вз[ляa]т(?:ы|а|о)?\s+под\s+контроль\s+([А-ЯЁ][\w\-]+)", "ru_taken"),
    (r"устано[вл]лен\s+контроль\s+над\s+([А-ЯЁ][\w\-]+)", "ru_taken"),
    (r"продвин\w+\s+(?:в\s+районе|у|около|севернее|южнее|восточнее|западнее)\s+([А-ЯЁ][\w\-]+)", "ru_advance"),
    (r"бои\s+(?:идут\s+)?(?:в|за|на\s+окраинах)\s+([А-ЯЁ][\w\-]+)", "contested"),
    (r"зачист\w+\s+([А-ЯЁ][\w\-]+)", "ru_advance"),
    (r"(?:ДРГ|диверсионн\w+\s+групп\w*)(?:\s+\w+){0,2}?\s+(?:в|у|под|около|близ)\s+([А-ЯЁ][\w\-]+)", "infiltration"),
    (r"(?:вошли|ворвались|закрепились)\s+(?:в|на)\s+([А-ЯЁ][\w\-]+)", "ru_advance"),
]
UA_PATTERNS = [
    (r"звільнили\s+([А-ЯІЇЄҐ][\w\-’']+(?:\s*,\s*[А-ЯІЇЄҐ][\w\-’']+)*)", "ua_retaken"),
    (r"відновлено\s+контроль\s+над\s+([А-ЯІЇЄҐ][\w\-’']+)", "ua_retaken"),
    (r"ворог\s+(?:окупував|захопив)\s+([А-ЯІЇЄҐ][\w\-’']+(?:\s*,\s*[А-ЯІЇЄҐ][\w\-’']+)*)", "ru_taken"),
    (r"просунув\w*\s+(?:поблизу|біля|в\s+районі|у\s+районі|на\s+околиц\w+)\s+([А-ЯІЇЄҐ][\w\-’']+)", "ru_advance"),
    (r"(?:ДРГ|диверсійн\w+\s+групи?)(?:\s+\w+){0,2}?\s+(?:у|в|поблизу|біля)\s+([А-ЯІЇЄҐ][\w\-’']+)", "infiltration"),
    (r"(?:бої|боїв|бойові\s+дії)\s+(?:у|в|за|поблизу)\s+([А-ЯІЇЄҐ][\w\-’']+)", "contested"),
    (r"(?:ДРГ|диверсійн\w+\s+групи?)\s+(?:у|в|поблизу|біля)\s+([А-ЯІЇЄҐ][\w\-’']+)", "infiltration"),
    (r"(?:атак|штурмов\w+\s+ді\w+|наступальн\w+\s+ді\w+)\D{0,40}?(?:поблизу|в\s+районі|у\s+районі)\s+([А-ЯІЇЄҐ][\w\-’']+)", "contested"),
]
EN_PATTERNS = [
    (r"(?:captur(?:ed|ing)|seized|taken|entered|occupied)\s+(?:the\s+(?:village|town|settlement)s?\s+of\s+)?([A-Z][\w'\-]+(?:\s*(?:,|and)\s*[A-Z][\w'\-]+)*)", "ru_taken"),
    (r"(?:advanced|pushed)\s+(?:in|into|near|toward[s]?|west|east|north|south)\s+(?:of\s+)?([A-Z][\w'\-]+)", "ru_advance"),
    (r"(?:fighting|clashes)\s+(?:in|near|around)\s+([A-Z][\w'\-]+)", "contested"),
    (r"(?:infiltrat\w+|DRG[s]?)\s+(?:in|into|near)\s+([A-Z][\w'\-]+)", "infiltration"),
]
STOP = {"россии", "украины", "рф", "всу", "вс", "минобороны", "сво", "днр", "лнр", "юг", "север",
        "russian", "ukrainian", "russia", "ukraine", "the", "russians", "ukrainians"}


def _posts(channel, days):
    """Yield (post_id, iso_date, text) from the public web preview, paging backwards.

    Telegram's markup shifts around, so split on the message wrapper and pull the three fields out of each
    block independently rather than matching them in one sweep."""
    since = NOW - timedelta(days=days)
    url = f"https://t.me/s/{channel}"
    pages = 0
    while url and pages < 8:
        h = get(url).text
        pages += 1
        blocks = re.split(r'<div class="tgme_widget_message[ _]', h)
        if len(blocks) < 2:
            log(f"  {channel}: page {pages} had no message blocks ({len(h)} bytes) — markup may have changed")
        chunks = []
        for b in blocks[1:]:
            pm = re.search(r'data-post="([^"]+)"', b)
            tm = re.search(r'datetime="([^"]+)"', b)
            if not (pm and tm):
                continue
            texts = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*(?:<div class="tgme_widget_message_(?:footer|reply_markup)|</div>)', b, re.S)
            if not texts:
                texts = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*)', b, re.S)
            chunks.append((pm.group(1), "", (texts[0] if texts else ""), tm.group(1)))
        if not chunks:
            log(f"  {channel}: page {pages} parsed 0 posts")
        oldest = None
        for post, _mid, body, ts in chunks:
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            oldest = t if oldest is None or t < oldest else oldest
            if t < since:
                continue
            text = html.unescape(re.sub(r"<br\s*/?>", "\n", re.sub(r"<[^>]+>", "", body or "")))
            yield post, t, text
        m = re.search(r'data-before="(\d+)"', h)
        url = f"https://t.me/s/{channel}?before={m.group(1)}" if (m and oldest and oldest > since) else None


def _extract(text, patterns):
    out = []
    for pat, kind in patterns:
        for m in re.finditer(pat, text, re.I | re.U):
            for name in re.split(r"\s*(?:,|\band\b|\bта\b|\bи\b)\s*", m.group(1)):
                name = name.strip(" .,:;«»\"'")
                if len(name) < 3 or name.lower() in STOP:
                    continue
                out.append((name, kind))
    return out


def fetch_telegram():
    path = P("live", "events.json")
    events = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    seen = {(e["src"], e["post"]) for e in events}
    groups = [(C.RU_CHANNELS, "ru", RU_PATTERNS + EN_PATTERNS),
              (C.UA_CHANNELS, "ua", UA_PATTERNS + EN_PATTERNS),
              (C.MAPPER_CHANNELS, "mapper", EN_PATTERNS + UA_PATTERNS + RU_PATTERNS)]
    added = 0
    for channels, side, pats in groups:
        for ch in channels:
            try:
                n = 0
                seen_posts = [0]
                for post, t, text in _posts(ch, C.TELEGRAM_DAYS):
                    seen_posts[0] += 1
                    if (ch, post) in seen or not text.strip():
                        continue
                    hits = _extract(text, pats)
                    if not hits:
                        continue
                    seen.add((ch, post))
                    for name, kind in dict.fromkeys(hits):
                        events.append({"src": ch, "side": side, "post": post, "url": f"https://t.me/{post}",
                                       "d": t.strftime("%Y-%m-%d"), "kind": kind, "name": name, "text": text[:400]})
                        n += 1
                added += n
                log(f"telegram ok: {ch} ({side}) {seen_posts[0]} posts read, {n} mentions")
            except Exception as e:
                log(f"telegram {ch} failed: {e}")
    cutoff = (NOW - timedelta(days=C.EVENT_KEEP_DAYS)).strftime("%Y-%m-%d")
    events = [e for e in events if e["d"] >= cutoff]
    events.sort(key=lambda e: e["d"], reverse=True)
    json.dump(events, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"telegram: {added} new mentions, {len(events)} kept")
    return added > 0


# ---------------------------------------------------------------- gazetteer

def fetch_gazetteer(force=False):
    out = P("gazetteer", "ua_full.json")
    if os.path.exists(out) and not force:
        age = NOW - datetime.fromtimestamp(os.path.getmtime(out), timezone.utc)
        if age.days < 30:
            log("gazetteer: current, skipped")
            return True
    places = []
    for cc in ("UA", "RU"):   # RU for the border areas Ukraine has held
        try:
            z = zipfile.ZipFile(io.BytesIO(get(f"https://download.geonames.org/export/dump/{cc}.zip", timeout=300).content))
            txt = z.read(f"{cc}.txt").decode("utf-8")
        except Exception as e:
            log(f"gazetteer {cc} failed: {e}")
            continue
        for line in txt.splitlines():
            c = line.split("\t")
            if len(c) < 15 or c[6] != "P":
                continue
            lat, lon = float(c[4]), float(c[5])
            if not (C.BBOX[0] <= lon <= C.BBOX[2] and C.BBOX[1] <= lat <= C.BBOX[3]):
                continue
            alts = [a for a in c[3].split(",") if a]
            cyr = [a for a in alts if re.search(r"[\u0400-\u04FF]", a)]
            places.append({"n": c[2] or c[1], "uk": next((a for a in [c[1]] + cyr if re.search(r"[\u0400-\u04FF]", a)), ""),
                           "alt": cyr[:10], "p": int(c[14] or 0), "lat": round(lat, 4), "lon": round(lon, 4), "cc": cc})
        log(f"gazetteer {cc}: {len(places)} cumulative")
    if not places:
        return False
    json.dump(places, open(out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"gazetteer ok: {len(places)} populated places")
    return True


# ---------------------------------------------------------------- entry

FETCHERS = {"kalibrated": fetch_kalibrated, "russian_map": fetch_russian_map, "isw": fetch_isw,
            "telegram": fetch_telegram, "lostarmour": fetch_lostarmour, "gazetteer": fetch_gazetteer}

if __name__ == "__main__":
    which = sys.argv[1:] or list(FETCHERS)
    results = {}
    for name in which:
        if name not in FETCHERS:
            log(f"unknown source {name}")
            continue
        try:
            results[name] = FETCHERS[name]()
        except Exception as e:
            log(f"{name} crashed: {e}")
            results[name] = False
    log("summary: " + ", ".join(f"{k}={'ok' if v else 'failed'}" for k, v in results.items()))
    # only a total wipe-out is an error; a single failed source still lets the page rebuild
    sys.exit(0 if any(results.values()) else 1)
