"""Geometry helpers. Everything metric goes through an equal-area projection so km2 and buffers are real."""
from shapely.geometry import shape, Polygon, MultiPolygon, LineString, MultiLineString, Point, box
from shapely.ops import unary_union, transform as sh_transform
from shapely.validation import make_valid
from pyproj import Transformer

_to_ea = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True).transform
_to_ll = Transformer.from_crs("EPSG:6933", "EPSG:4326", always_xy=True).transform


def ea(g):
    return sh_transform(_to_ea, g)


def ll(g):
    return sh_transform(_to_ll, g)


def km2(g):
    return ea(g).area / 1e6


def km(g):
    return ea(g).length / 1000


def valid(g):
    if g is None or g.is_empty:
        return Polygon()
    if not g.is_valid:
        g = make_valid(g)
    return g


def polys(g, min_km2=0.0):
    """Keep polygonal parts only, drop slivers."""
    if g is None or g.is_empty:
        return MultiPolygon()
    parts = []
    stack = [g]
    while stack:
        x = stack.pop()
        if x.is_empty:
            continue
        if x.geom_type == "Polygon":
            parts.append(x)
        elif x.geom_type in ("MultiPolygon", "GeometryCollection"):
            stack.extend(x.geoms)
    if min_km2 > 0:
        parts = [p for p in parts if km2(p) >= min_km2]
    return MultiPolygon(parts) if parts else MultiPolygon()


def lines(g):
    if g is None or g.is_empty:
        return MultiLineString()
    out = []
    stack = [g]
    while stack:
        x = stack.pop()
        if x.is_empty:
            continue
        if x.geom_type == "LineString":
            out.append(x)
        elif x.geom_type in ("MultiLineString", "GeometryCollection"):
            stack.extend(x.geoms)
    return MultiLineString(out) if out else MultiLineString()


def buffer_km(g, d):
    return ll(ea(g).buffer(d * 1000))


def simplify_m(g, m):
    return ll(ea(g).simplify(m))


def from_rings(rings_list):
    """[[outer, hole...], ...] -> MultiPolygon"""
    out = []
    for rings in rings_list:
        if not rings or len(rings[0]) < 4:
            continue
        try:
            p = Polygon(rings[0], [r for r in rings[1:] if len(r) >= 4])
            if not p.is_valid:
                p = make_valid(p)
            out.extend(polys(p).geoms)
        except Exception:
            continue
    return MultiPolygon(out) if out else MultiPolygon()


def rnd(coords, nd):
    return [[round(float(x), nd), round(float(y), nd)] for x, y in coords]


def poly_coords(g, nd=5):
    out = []
    for p in polys(g).geoms:
        out.append([rnd(p.exterior.coords, nd)] + [rnd(i.coords, nd) for i in p.interiors])
    return out


def line_coords(g, nd=5):
    return [rnd(l.coords, nd) for l in lines(g).geoms]


def bands(inner, outer, steps=3):
    """Feathered edge: a sequence of nested rings between inner and outer, drawn with falling opacity.
    Returns a list of MultiPolygons from the tightest to the widest."""
    out = []
    prev = inner
    for i in range(1, steps + 1):
        f = i / steps
        cur = ll(ea(inner).buffer(0).union(ea(outer).buffer(0).intersection(
            ea(inner).buffer(f * 1e-9).union(ea(outer)))))
        out.append(cur)
        prev = cur
    return out


def feather(core, half_width_km, steps=3):
    """Concentric rings outward from `core`, each `half_width_km/steps` wider than the last.
    Returns [(ring_geometry, weight 0..1)] from strongest to weakest."""
    rings = []
    core_ea = ea(core)
    prev = core_ea
    for i in range(1, steps + 1):
        d = half_width_km * 1000 * i / steps
        cur = core_ea.buffer(d)
        ring = cur.difference(prev)
        if not ring.is_empty:
            rings.append((polys(ll(valid(ring))), 1 - i / (steps + 1)))
        prev = cur
    return rings
