"""
Parse LostArmour's daily situation report.

Structure of the page, which is what the parser keys off:
  · bold axis headers, e.g. "Лиманское/Купянское направление, группировка «Запад»"
  · dated entries under them: "05.09.26 Красный Лиман - Кировск(Заречное). Позиционные боевые действия …"
  · a "Статистические данные" block of control-change bullets:
      "ВС РФ увеличили зону контроля на западных подступах к Рясному"
      "подняли флаги в Казачьей Лопани"
      "восстановили контроль в восточной части Диброва"

Settlements are written with their Russian names (Красноармейск, Красный Лиман, Часовой Яр); the gazetteer
bridges those to the Ukrainian ones.

parse(html, date_iso) -> list of events
  {"axis", "grouping", "name", "kind", "text", "d", "src": "lostarmour", "side": "ru", "url"}
"""
import html as htmllib
import re

AXIS_RE = re.compile(r"(?:^|\n)\s*\*{0,2}\s*([А-ЯЁ][^\n*]{0,80}?направлени[ея][^\n*]{0,80}?)\*{0,2}\s*(?:\n|$)")
GROUPING_RE = re.compile(r"группировк\w*\s+(?:войск\s+)?[«\"]([^»\"]+)[»\"]")
DATED_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\s+([^.\n]{3,80}?)\s*[-–—]\s*([^.\n]{2,60}?)\s*\.\s*([^\n]{0,400})")

# control-change phrasings, strongest first
CONTROL_PATTERNS = [
    (r"(?:установили|установлен)\s+контрол[ья]", "ru_taken"),
    (r"(?:восстановили|восстановлен)\s+контрол[ья]", "ru_taken"),
    (r"подняли\s+флаги", "ru_taken"),
    (r"(?:освобожд\w+|заняли)\s", "ru_taken"),
    (r"(?:увеличили|расширяют|расширили)\s+зону\s+контроля", "ru_advance"),
    (r"(?:увеличена|расширена)\s+зона\s+контроля", "ru_advance"),
    (r"продвинулись\s+(?:в|на|западнее|восточнее|севернее|южнее)", "ru_advance"),
    (r"продвигаются", "ru_advance"),
    (r"демонстрируют\s+флаги", "ru_advance"),
    (r"(?:закрепи\w+|вклинение)", "ru_advance"),
    (r"(?:завязали\s+)?боевые\s+действия\s+(?:в|на|у|за)", "contested"),
    (r"позиционные\s+боевые\s+действия\s+в\s+районе", "contested"),
    (r"(?:атаковали|контратак\w+)\s+позиции", "contested"),
    (r"инфильтрацион\w+\s+групп\w*\s+ВСУ[^.]{0,60}?\s+в\s+зону\s+контроля", "infiltration"),
    (r"ВСУ\s+(?:вернули|отбили|восстановили\s+контроль)", "ua_retaken"),
]

# Words that turn up in the same grammatical slot as a settlement name but are not one.
STOP = {
    "вс", "рф", "всу", "днр", "лнр", "ук", "украины", "россии", "сво", "бпла", "фпв", "мо", "гру",
    "заявлено", "событие", "позиционные", "обстрел", "продвижение", "карта", "сводка", "сутки",
    # geography-shaped nouns
    "районе", "район", "районы", "части", "часть", "зоне", "зона", "зоны", "рубеже", "рубеж",
    "окраинах", "окраине", "окраины", "подступах", "застройке", "застройка", "секторе", "сектор",
    "микрорайоне", "микрорайон", "направлении", "направление", "участке", "участок", "глубину",
    "позиции", "позициях", "позиция", "опорных", "пунктов", "пункте", "пункт", "пунктах",
    "населённого", "населенного", "города", "город", "селе", "сел", "села", "деревне", "лесу",
    "стороны", "территории", "границы", "берегу", "трассе", "дороге", "высоте", "балке",
    # adjectives that appear standalone
    "восточной", "восточных", "восточные", "западной", "западных", "западные", "северной", "северных",
    "южной", "южных", "новые", "новых", "новая", "жилой", "жилых", "частном", "частного", "центре",
    "центральной", "передовая", "передовые", "укрепленными", "укреплёнными", "сельскохозяйственных",
    "боевые", "боевых", "штурмовых", "оборонительных", "нашей", "наших", "противника", "своих",
    # sentence-initial verbs and connectives that are capitalised like a name
    "увеличена", "расширена", "восстановлен", "заявлено", "около", "однако", "продвижение",
    "операторы", "подразделения", "формируемая", "произведены", "комбриг", "батальон", "хронология",
    "материалы", "автор", "статистические", "силы", "средства", "итоги", "сводка",
}
VERBISH = re.compile(r"(?:ена|ены|ено|ила|или|ился|лись|вают|яют)$", re.I)
# a lone oblast/axis adjective ("Сумской", "Краснолиманском") is a region, not the settlement
REGIONISH = re.compile(r"(?:ской|ском|ская|ского|скому)$", re.I)
PREP = {"около", "возле", "близ", "под", "над", "при", "из", "от", "до", "за", "на", "в", "во", "у", "к", "по"}
# a two-word candidate is rejected when its head noun is one of these
HEAD_STOP = {"позиции", "части", "застройке", "секторе", "районе", "зоне", "окраинах", "пунктах", "рубеже"}


def _clean(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    return re.sub(r"[ \t\u00a0]+", " ", s)


def _body(page):
    """Trim navigation and footer so patterns do not fire on menu text."""
    txt = _clean(page)
    start = txt.find("Итоги за")
    if start < 0:
        start = txt.find("Сводка за")
    end = txt.find("Правила площадки")
    return txt[start if start > 0 else 0: end if end > 0 else len(txt)]


TOPONYM = re.compile(r"[А-ЯЁ][а-яё\-]{2,}(?:\s+[А-ЯЁ][а-яё\-]{2,})?")


def _toponyms(clause):
    """Every capitalised candidate in a clause, minus the vocabulary that merely looks like one."""
    out = []
    for m in TOPONYM.finditer(clause):
        cand = m.group(0).strip()
        parts = cand.split()
        if parts and parts[0].lower() in PREP:
            parts = parts[1:]
            cand = " ".join(parts)
        if not parts:
            continue
        if all(p.lower() in STOP for p in parts):
            continue
        if len(parts) > 1 and parts[-1].lower() in (HEAD_STOP | STOP):
            cand = parts[0]
        if cand.lower() in STOP or len(cand) < 4:
            continue
        if len(parts) == 1 and (VERBISH.search(cand) or REGIONISH.search(cand)):
            continue
        out.append(cand)
    return out


def _names(text):
    """Match a control phrasing, then take the toponyms out of the clause it matched."""
    out, seen = [], set()
    for pat, kind in CONTROL_PATTERNS:
        for m in re.finditer(pat, text, re.I | re.U):
            # the whole sentence the phrasing sits in, so a place named before the verb is not lost
            left = max((text.rfind(ch, 0, m.start()) for ch in ".;\n"), default=-1)
            right = min((p for p in (text.find(ch, m.end()) for ch in ".;\n") if p != -1), default=len(text))
            clause = text[left + 1:right]
            for name in _toponyms(clause):
                if (name, kind) in seen:
                    continue
                seen.add((name, kind))
                out.append((name, kind))
    return out


def parse(page, date_iso, url=""):
    txt = _body(page)
    events, axis, grouping = [], "", ""
    seen = set()

    lines = [l.strip() for l in txt.split("\n")]
    BULLET_AREA = re.compile(r"^[-·•]?\s*(?:В|Во|На|Около|Под|У)\s+([А-ЯЁ][\wё\-]+(?:\s+[а-яё]+)?)\s")
    for line in lines:
        if not line:
            continue
        if re.match(r"^\*{0,2}\s*(?:Статистические\s+данные|Силы\s+и\s+средства|Тылы|Удары\s+по|Непривязанное)", line):
            axis, grouping = "", ""
            continue
        am = AXIS_RE.search("\n" + line + "\n")
        if am and len(line) < 160:
            axis = re.sub(r"\s*,?\s*группировк.*$", "", am.group(1)).strip(" *:")
            gm = GROUPING_RE.search(line)
            grouping = gm.group(1) if gm else ""
            continue
        # dated entry: "05.09.26 Красный Лиман - Кировск(Заречное). ..."
        for dm in DATED_RE.finditer(line):
            dd, mm, yy, sector, place, rest = dm.groups()
            d = f"20{yy}-{mm}-{dd}"
            place = re.sub(r"\(.*?\)", "", place).strip()
            body = rest
            hits = _names(body) or [(place, "contested")]
            for name, kind in hits:
                key = (d, name, kind)
                if key in seen:
                    continue
                seen.add(key)
                events.append({"d": d, "name": name, "kind": kind, "axis": axis or sector.strip(),
                               "grouping": grouping, "text": (sector + " — " + body)[:400],
                               "src": "lostarmour", "side": "ru", "url": url})
        # loose control bullets anywhere in the body
        if len(line) > 25 and not DATED_RE.search(line):
            bm = BULLET_AREA.match(line)
            local_axis = bm.group(1) if bm else axis
            for name, kind in _names(line):
                key = (date_iso, name, kind)
                if key in seen:
                    continue
                seen.add(key)
                events.append({"d": date_iso, "name": name, "kind": kind, "axis": local_axis, "grouping": grouping,
                               "text": line[:400], "src": "lostarmour", "side": "ru", "url": url})
    return events
