"""
Parse a Google My Maps KML/KMZ export into layers.

Google My Maps writes one <Folder> per map layer, styles as <Style>/<StyleMap> referenced by <styleUrl>,
and per-feature fields in <ExtendedData><Data name="...">. Geometry can be Polygon, LineString, Point or
MultiGeometry, and polygons carry inner rings. Coordinates are "lon,lat[,alt]" separated by whitespace.

parse(bytes_or_str) -> {"layers": [{"name", "features": [...]}], "name": <doc name>}
each feature: {"name", "desc", "kind": polygon|line|point, "coords": ..., "fill": "#rrggbb"|None,
               "stroke": "#rrggbb"|None, "data": {k: v}}
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

NS = {"k": "http://www.opengis.net/kml/2.2"}


def _t(el, path):
    x = el.find(path, NS)
    return (x.text or "").strip() if x is not None and x.text else ""


def _abgr_to_hex(v):
    """KML colours are aabbggrr. Returns (#rrggbb, alpha 0-1)."""
    v = (v or "").strip().lower()
    if len(v) != 8:
        return None, None
    a, b, g, r = v[0:2], v[2:4], v[4:6], v[6:8]
    return f"#{r}{g}{b}", int(a, 16) / 255


def _coords(text):
    out = []
    for tok in (text or "").split():
        parts = tok.split(",")
        if len(parts) >= 2:
            try:
                out.append([round(float(parts[0]), 6), round(float(parts[1]), 6)])
            except ValueError:
                pass
    return out


def _styles(root):
    """Flatten <Style> and <StyleMap> (normal pair) into id -> {fill, stroke, fill_alpha}."""
    styles = {}
    for st in root.iter():
        tag = st.tag.split("}")[-1]
        if tag != "Style":
            continue
        sid = st.get("id")
        if not sid:
            continue
        fill = stroke = None
        alpha = None
        poly = st.find("k:PolyStyle", NS)
        line = st.find("k:LineStyle", NS)
        icon = st.find("k:IconStyle", NS)
        if poly is not None:
            fill, alpha = _abgr_to_hex(_t(poly, "k:color"))
        if line is not None:
            stroke, _ = _abgr_to_hex(_t(line, "k:color"))
        if icon is not None and fill is None:
            fill, _ = _abgr_to_hex(_t(icon, "k:color"))
        styles["#" + sid] = {"fill": fill, "stroke": stroke, "fill_alpha": alpha}
    # StyleMap: point the map id at its "normal" style
    for sm in root.iter():
        if sm.tag.split("}")[-1] != "StyleMap":
            continue
        sid = sm.get("id")
        if not sid:
            continue
        target = None
        for pair in sm.findall("k:Pair", NS):
            if _t(pair, "k:key") == "normal":
                target = _t(pair, "k:styleUrl")
        if target and target in styles:
            styles["#" + sid] = styles[target]
    return styles


def _geometry(pm):
    """Returns (kind, coords). polygon coords = [ring, hole...]; multi geometries are split by the caller."""
    out = []
    for geom in pm.iter():
        tag = geom.tag.split("}")[-1]
        if tag == "Polygon":
            outer = geom.find("k:outerBoundaryIs/k:LinearRing/k:coordinates", NS)
            rings = [_coords(outer.text)] if outer is not None else []
            for inner in geom.findall("k:innerBoundaryIs/k:LinearRing/k:coordinates", NS):
                rings.append(_coords(inner.text))
            rings = [r for r in rings if len(r) >= 4]
            if rings:
                out.append(("polygon", rings))
        elif tag == "LineString":
            c = _coords(_t(geom, "k:coordinates"))
            if len(c) >= 2:
                out.append(("line", c))
        elif tag == "Point":
            c = _coords(_t(geom, "k:coordinates"))
            if c:
                out.append(("point", c[0]))
    return out


def _extended(pm):
    data = {}
    for d in pm.findall("k:ExtendedData/k:Data", NS):
        name = d.get("name")
        val = _t(d, "k:value")
        if name:
            data[name] = val
    for d in pm.findall("k:ExtendedData/k:SchemaData/k:SimpleData", NS):
        name = d.get("name")
        if name:
            data[name] = (d.text or "").strip()
    return data


def _strip_html(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;?", " ", s)
    s = re.sub(r"&amp;", "&", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def parse(raw):
    """raw: bytes (kml or kmz) or str."""
    if isinstance(raw, bytes):
        if raw[:2] == b"PK":  # kmz
            z = zipfile.ZipFile(io.BytesIO(raw))
            name = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            if not name:
                raise ValueError("kmz without a kml inside")
            raw = z.read(name)
        raw = raw.decode("utf-8", "replace")
    raw = raw.lstrip("\ufeff \n\r\t")
    root = ET.fromstring(raw)
    styles = _styles(root)
    doc = root.find("k:Document", NS)
    if doc is None:
        doc = root
    doc_name = _t(doc, "k:name")

    def read_folder(folder, fname):
        feats = []
        for pm in folder.findall("k:Placemark", NS):
            st = styles.get(_t(pm, "k:styleUrl"), {})
            base = {"name": _t(pm, "k:name"), "desc": _strip_html(_t(pm, "k:description")),
                    "fill": st.get("fill"), "stroke": st.get("stroke"), "fill_alpha": st.get("fill_alpha"),
                    "data": _extended(pm)}
            for kind, coords in _geometry(pm):
                feats.append({**base, "kind": kind, "coords": coords})
        return {"name": fname, "features": feats}

    layers = []
    folders = doc.findall("k:Folder", NS)
    if folders:
        for f in folders:
            layers.append(read_folder(f, _t(f, "k:name") or "unnamed"))
            for sub in f.findall("k:Folder", NS):  # one level of nesting is enough for My Maps
                layers.append(read_folder(sub, f"{_t(f, 'k:name')} / {_t(sub, 'k:name')}"))
    loose = read_folder(doc, doc_name or "root")
    if loose["features"]:
        layers.append(loose)
    return {"name": doc_name, "layers": [l for l in layers if l["features"]]}
