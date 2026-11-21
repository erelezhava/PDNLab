"""IPC-2581 nets, zone fills, tracks, pads, vias and drills. Not yet a board importer.

Builds on ipc2581_metadata (header, units, stackup, provenance) and adds:
* Net records: every distinct Set ``net`` attribute in the Step. KiCad 10 writes no
  LogicalNet or PhyNet, and omits ``net`` for copper with no net; such copper gets
  ``net_id=None``. Net names are the exported names, which KiCad may have altered.
* CopperFeature records of kind "fill": each Contour written directly in a copper
  layer's Set/Features/UserSpecial, one feature per Contour, holes preserved.
* CopperFeature records of kind "track": each Line (straight segment) and Arc in
  the same UserSpecial, one feature per element, outlined by ``track_outline`` with
  round ends at a 1 µm chord error. KiCad writes copper graphic segments and arcs the
  same way, so they are read as tracks too.
* CopperFeature records of kind "pad" (component pads, ID
  ``pad:<layer>:<component>.<pin>``, with ``#2``... for a repeated pin) and "via"
  (``via:<layer>:<k>``): each Pad on a copper layer, its DictionaryStandard shape
  (Circle, RectCenter, Oval, RectRound, RectCham or a Contour for custom and
  trapezoid pads) turned counter-clockwise by its Xform rotation about its Location.
  Outlines are solid: holes are not cut from them.
* DrillFeature records: each Hole in a layerFunction="DRILL" layer, its span from the
  layer's Span, kind via (VIA), pin (PLATED) or mechanical (NONPLATED), ID
  ``drill:<hole name>``. The pad and via copper of the same padstack and net at
  exactly the hole's position, on each layer of the span, gets ``drill_id``. A span
  does not by itself mean contact; holes are cut and barrels modelled later, with the
  setup's finished-hole size and plating (Phase 2 contract). Slots (ROUT layers),
  backdrills and hole tolerances raise.

``unread`` counts copper a later increment will read; KiCad boards leave it empty
now. Copper that no planned increment reads raises instead, so copper on
a net is never left out of a result: teardrops, copper text, footprint graphics and
board graphic shapes on copper. A footprint-owned zone raises, because the export drops
its net. LayerFeatures of other layers (drills, slots, mask, paste, legend) are listed
in ``uninspected_layers``. Anything else raises ValueError with layer, net and
element-path context; nothing is skipped silently.

Tracks: width from one inline LineDesc with lineEnd ROUND; an Arc's clockwise flag
is in IPC (Y-up) coordinates and its centre may differ from equidistance by up to
1 µm, the rounding KiCad applies. Outlines are built in source units and every
vertex is transformed once.

Geometry rules for fills: straight steps only (PolyBegin and PolyStepSegment), closed
rings, no Xform and no nonzero Location on any containing element. The recorded
xy_transform is applied exactly once. Coordinates are exact decimals until one
rounding to metres. Nothing is repaired, unioned or simplified. The single exception
is KiCad's fractured form: KiCad fills are one outline that reaches each hole along a
zero-width slit, the slit being an edge followed later by the same edge reversed.
Each such pair is removed, splitting the ring into an outline and holes exactly as
KiCad's own unfracture does. No vertex is moved and the enclosed area is unchanged.
An outline that is still invalid afterwards raises.
"""

from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from shapely.geometry import Polygon
from shapely.validation import explain_validity

from pdnlab_dc.board import (BoardModel, CopperFeature, CopperLayer, DrillFeature, Net,
                             SourceInfo)
from pdnlab_dc.ipc2581_metadata import (_DECIMAL, CONVENTIONS, NAMESPACE, _q,
                                        parse_ipc2581_tree)
from pdnlab_dc.pad_outline import (chamfer_outline, circle_outline, oval_outline, place,
                                  rect_outline, roundrect_outline)
from pdnlab_dc.track_outline import arc_outline, segment_outline

SCOPE = ("Nets, filled-zone contours, tracks, pads, vias and drill holes. Pad and via "
         "outlines are solid: holes are recorded as DrillFeature and linked by drill_id, not "
         "cut, and no barrel or plating is modelled. This is not a complete board.")
ASSUMPTIONS = (
    SCOPE,
    "xy_transform (source mm to stored m, IPC Y reversed once) was applied once, to fill, "
    "track and pad coordinates.",
    "Nets are the distinct Set net attributes in the Step, with their exported names; copper "
    "in a Set without one is unassigned (net_id None).",
    "A fill outline that returns along its own edge (KiCad's fractured form) was split at each "
    "such edge pair into an outline and holes; no other geometry change was made.",
    "Tracks and copper graphic segments/arcs are outlines with round ends; every vertex lies "
    "on its true circle or edge, within 1 µm chord error (inner arc edges may add up to 1 µm "
    "of copper; the total area is slightly less than exact).",
    "Pads are their dictionary shape turned counter-clockwise (IPC, Y up) by the Pad's Xform "
    "rotation about its Location; round parts use the same 1 µm rule. Per-layer padstacks, "
    "pad offsets and rounded chamfered pads are not exported faithfully by KiCad 10 and are "
    "not detectable here.",
    "Drill diameters are the exported drill values; finished-hole size and plating "
    "thickness are setup assumptions, applied when holes are cut and barrels modelled.",
) + CONVENTIONS

# Set geometryUsage values: copper no planned increment reads, so they raise.
_USAGE_KINDS = {"TEXT": "copper text", "GRAPHIC": "footprint graphic on copper",
                "TEARDROP": "teardrop"}
_SET_ATTRIBUTES = {"net", "padUsage", "geometryUsage", "componentRef"}
# Tracks: chord error and centre-equidistance tolerance, in metres.
TRACK_TOL_M = 1e-6
_LINE_ATTRIBUTES = {"startX", "startY", "endX", "endY"}
_ARC_ATTRIBUTES = _LINE_ATTRIBUTES | {"centerX", "centerY", "clockwise"}


@dataclass(frozen=True)
class UnreadCount:
    """Copper content present in the export but not read: a count, not geometry."""
    kind: str
    layer_id: str
    net_id: str | None
    count: int


@dataclass(frozen=True)
class PadPin:
    """Which component pin a pad copper feature belongs to, and where its pad sits."""
    feature_id: str
    component_ref: str
    pin_number: str
    position_m: tuple[float, float]


@dataclass(frozen=True)
class Ipc2581Copper:
    """Nets and zone fills only. Not a BoardModel: see scope, unread, uninspected_layers."""
    source: SourceInfo
    copper_layers: tuple[CopperLayer, ...]
    nets: tuple[Net, ...]
    fills: tuple[CopperFeature, ...]
    tracks: tuple[CopperFeature, ...]
    pads: tuple[CopperFeature, ...]
    pad_pins: tuple[PadPin, ...]
    drills: tuple[DrillFeature, ...]
    unread: tuple[UnreadCount, ...]
    uninspected_layers: tuple[str, ...]
    scope: str = SCOPE


def _tag(element):
    prefix = f"{{{NAMESPACE}}}"
    return element.tag[len(prefix):] if element.tag.startswith(prefix) else element.tag


def _children(element, ref):
    """(child, tag, element path) with 1-based positions among same-tag siblings."""
    seen = Counter()
    for child in element:
        tag = _tag(child)
        seen[tag] += 1
        yield child, tag, f"{ref}/{tag}[{seen[tag]}]"


def _context(layer, net, ref):
    return f"layer {layer!r}, net {net!r} ({ref})" if net is not None else \
        f"layer {layer!r}, no net ({ref})"


def _number(text, context):
    if text is None or not _DECIMAL.fullmatch(text.strip()):
        raise ValueError(f"{context}: coordinate {text!r} is not a decimal number")
    return Decimal(text.strip())


def _ring(element, context):
    """Closed list of exact (x, y) source points from PolyBegin/PolyStepSegment steps."""
    if element.attrib:
        raise ValueError(f"{context}: {_tag(element)} attributes {sorted(element.attrib)} "
                         "are not supported")
    points = []
    for i, step in enumerate(element):
        tag = _tag(step)
        if tag != ("PolyBegin" if i == 0 else "PolyStepSegment"):
            what = "curved steps are not supported" if tag == "PolyStepCurve" else "not supported"
            raise ValueError(f"{context}: {tag} at position {i+1} of {_tag(element)}: {what}; "
                             "expected PolyBegin then PolyStepSegment")
        if set(step.attrib) != {"x", "y"} or len(step):
            raise ValueError(f"{context}: {tag} must have exactly x and y and no children")
        points.append((_number(step.get("x"), context), _number(step.get("y"), context)))
    if len(points) < 4 or points[0] != points[-1]:
        raise ValueError(f"{context}: {_tag(element)} must be a closed ring of at least three "
                         "edges (last step back at PolyBegin)")
    return points


def _point(p):
    return f"({p[0]}, {p[1]})"


def _chain_ring(chain, context):
    if len(chain) < 3:
        raise ValueError(f"{context}: a slit leads to a degenerate ring of {len(chain)} edges")
    for (_, q), (p, _) in zip(chain, chain[1:] + chain[:1]):
        if q != p:
            raise ValueError(f"{context}: edges do not join at {_point(q)} after slit removal")
    return [p for p, _ in chain] + [chain[0][0]]


def _split_slits(points, context):
    """Rings left after removing every edge whose reverse is also an edge of the ring.

    Pairs must nest like brackets (a KiCad slit never crosses another); each pair cuts
    the ring into the part between its two edges and the rest.
    """
    edges = list(zip(points, points[1:]))
    index = {}
    for i, (p, q) in enumerate(edges):
        if p == q:
            raise ValueError(f"{context}: zero-length edge at {_point(p)}")
        if (p, q) in index:
            raise ValueError(f"{context}: edge {_point(p)} -> {_point(q)} occurs twice")
        index[(p, q)] = i
    partner = {i: index[(q, p)] for i, (p, q) in enumerate(edges) if (q, p) in index}
    if not partner:
        return [points]
    stack, rings = [(None, [])], []
    for i, edge in enumerate(edges):
        j = partner.get(i)
        if j is None:
            stack[-1][1].append(edge)
        elif j > i:
            stack.append((i, []))
        else:
            opener, chain = stack.pop()
            if opener != j:
                raise ValueError(f"{context}: opposite edge pairs cross; not a fractured outline")
            rings.append(_chain_ring(chain, context))
    rings.append(_chain_ring(stack[0][1], context))
    return rings


def _signed_area(ring):
    return sum(x0*y1 - x1*y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:])) / 2


def _contour_rings(element, ref, context):
    """(outline, holes) as exact source-unit rings from a Contour: one Polygon (slits
    split as described above) then Cutouts. Used for fills and custom pad shapes."""
    children = list(_children(element, ref))
    tags = [tag for _, tag, _ in children]
    if element.attrib or not tags or tags[0] != "Polygon" or \
            any(tag != "Cutout" for tag in tags[1:]):
        raise ValueError(f"{context}: Contour must hold one Polygon then Cutout elements "
                         f"only, found {tags}")
    rings = _split_slits(_ring(children[0][0], context), context)
    areas = [_signed_area(r) for r in rings]
    total = sum(areas)
    if total == 0 or any(a == 0 for a in areas):
        raise ValueError(f"{context}: outline ring with zero area")
    outer = [i for i, a in enumerate(areas) if (a > 0) == (total > 0)]
    if len(outer) != 1:
        raise ValueError(f"{context}: {len(outer)} outlines after slit removal; expected one")
    holes = [r for i, r in enumerate(rings) if i != outer[0]]
    holes += [_ring(child, context) for child, _, _ in children[1:]]
    return rings[outer[0]], holes


def _attributes(element, required, optional=(), context=""):
    """Exact attribute check: all of ``required``, any of ``optional``, nothing else."""
    names = set(element.attrib)
    if not set(required) <= names or names - set(required) - set(optional) or len(element):
        raise ValueError(f"{context}: {_tag(element)} must have attributes {sorted(required)}"
                         + (f" (optionally {sorted(optional)})" if optional else "")
                         + f" and no children; found {sorted(names)}"
                         + (f" and {[_tag(c) for c in element]}" if len(element) else ""))


class _CopperLayerReader:
    def __init__(self, layer, transform, fills, tracks, pads, shapes, unread, pad_index,
                 pad_pins):
        self.layer, self.fills, self.unread, self.pad_pins = layer, fills, unread, pad_pins
        self.transform = [Decimal(repr(v)) for v in transform]
        self.index = 0
        self.tracks, self.track_index = tracks, 0
        self.pads, self.shapes, self.via_index, self.pad_ids = pads, shapes, 0, Counter()
        self.local_shapes = {}   # dictionary id -> outline in the pad frame (source units)
        # (layer, padstackDefRef, net, exact x, exact y) -> indices into pads, for drills.
        self.pad_index = pad_index
        self.matrix = tuple(float(v) for v in transform)
        # The chord tolerance in source units: 1 µm divided by the source-to-metre scale.
        self.track_tol = TRACK_TOL_M / abs(self.matrix[0])

    def count(self, kind, net, n=1):
        self.unread[(self.layer, kind, net)] += n

    def to_metres(self, ring):
        a, b, tx, c, d, ty = self.transform
        return [(float(a*x + b*y + tx), float(c*x + d*y + ty)) for x, y in ring]

    def set(self, element, ref, nested=False):
        net = element.get("net")
        context = _context(self.layer, net, ref)
        extra = set(element.attrib) - _SET_ATTRIBUTES
        if extra:
            raise ValueError(f"{context}: Set attributes {sorted(extra)} are not supported")
        usage = element.get("geometryUsage")
        if usage is not None:
            if usage not in _USAGE_KINDS:
                raise ValueError(f"{context}: Set geometryUsage {usage!r} is not supported")
            raise ValueError(f"{context}: {_USAGE_KINDS[usage]} (geometryUsage {usage!r}) is "
                             "not supported; remove it from copper or wait for support")
        if element.get("componentRef") is not None:
            self._component_set(element, context)
            return
        pad_usage = element.get("padUsage")
        if pad_usage not in (None, "VIA"):
            raise ValueError(f"{context}: Set padUsage {pad_usage!r} is not supported")
        for child, tag, child_ref in _children(element, ref):
            if tag == "Pad":
                self._pad(child, net, pad_usage == "VIA", child_ref)
            elif tag == "Features" and not nested:
                self._features(child, net, child_ref)
            elif tag == "Set" and not nested:
                self.set(child, child_ref, nested=True)
            else:
                where = "a nested Set" if nested else "a Set"
                raise ValueError(f"{context}: {tag} in {where} is not supported")

    def _component_set(self, element, context):
        # KiCad writes a footprint-owned zone as a componentRef Set without its net.
        if element.find(f".//{_q('Contour')}") is not None:
            raise ValueError(f"{context}: zone fill owned by component "
                             f"{element.get('componentRef')!r}; the export omits its net, so "
                             "it cannot be read")
        allowed = {"Set", "Features", "Location", "UserSpecial"}
        if any(_tag(e) not in allowed for e in element.iter()):
            raise ValueError(f"{context}: component Set content is not supported")

    def _features(self, element, net, ref):
        context = _context(self.layer, net, ref)
        children = list(_children(element, ref))
        locations = [c for c, tag, _ in children if tag == "Location"]
        if len(locations) > 1:
            raise ValueError(f"{context}: more than one Location")
        shifted = bool(locations) and any(
            _number(locations[0].get(k), context) != 0 for k in ("x", "y"))
        for child, tag, child_ref in children:
            if tag == "Location":
                continue
            if tag == "UserPrimitiveRef":
                raise ValueError(f"{context}: board graphic shape on copper (UserPrimitiveRef "
                                 f"{child.get('id')!r}) is not supported; draw it as a zone")
            elif tag == "UserSpecial":
                if shifted and len(child):
                    raise ValueError(f"{context}: contour or track under a nonzero Location "
                                     "is not supported")
                self._special(child, net, child_ref)
            else:
                raise ValueError(f"{context}: {tag} in Features is not supported")

    def _special(self, element, net, ref):
        for child, tag, child_ref in _children(element, ref):
            if tag == "Contour":
                self._contour(child, net, child_ref)
            elif tag in ("Line", "Arc"):
                self._track(child, tag, net, child_ref)
            else:
                raise ValueError(f"{_context(self.layer, net, ref)}: {tag} in UserSpecial is "
                                 "not supported")

    def _contour(self, element, net, ref):
        context = _context(self.layer, net, ref)
        outline, holes = _contour_rings(element, ref, context)
        polygon = Polygon(self.to_metres(outline), [self.to_metres(h) for h in holes])
        if not polygon.is_valid:
            raise ValueError(f"{context}: invalid fill polygon: {explain_validity(polygon)}")
        self.fills.append(CopperFeature(f"fill:{self.layer}:{self.index}", net, self.layer,
                                        "fill", polygon, source_ref=ref))
        self.index += 1

    def _pad(self, element, net, via, ref):
        context = _context(self.layer, net, ref)
        if set(element.attrib) - {"padstackDefRef"}:
            raise ValueError(f"{context}: Pad attributes {sorted(element.attrib)} are not "
                             "supported; only padstackDefRef")
        parts = {}
        for child, tag, _ in _children(element, ref):
            if tag not in ("Xform", "Location", "StandardPrimitiveRef", "PinRef"):
                raise ValueError(f"{context}: {tag} in Pad is not supported")
            if tag in parts:
                raise ValueError(f"{context}: more than one {tag} in Pad")
            parts[tag] = child
        for tag in ("Location", "StandardPrimitiveRef"):
            if tag not in parts:
                raise ValueError(f"{context}: Pad has no {tag}")
        rotation = 0.0
        if "Xform" in parts:
            # KiCad writes only the rotation; mirror, scale or offsets would change the shape.
            _attributes(parts["Xform"], (), ("rotation",), context)
            rotation = float(_number(parts["Xform"].get("rotation", "0"), context))
        _attributes(parts["Location"], ("x", "y"), (), context)
        _attributes(parts["StandardPrimitiveRef"], ("id",), (), context)
        x, y = (float(_number(parts["Location"].get(k), context)) for k in ("x", "y"))
        local = self._local_shape(parts["StandardPrimitiveRef"].get("id"), context)
        try:
            placed = place(local, x, y, rotation)
        except ValueError as error:
            raise ValueError(f"{context}: {error}") from None
        a, b, tx, c, d, ty = self.matrix
        move = lambda ring: [(a*px + b*py + tx, c*px + d*py + ty) for px, py in ring]
        polygon = Polygon(move(placed.exterior.coords), [move(r.coords) for r in placed.interiors])
        if not polygon.is_valid:
            raise ValueError(f"{context}: invalid pad outline: {explain_validity(polygon)}")
        pin = parts.get("PinRef")
        if via:
            if pin is not None:
                raise ValueError(f"{context}: via pad with a PinRef")
            feature_id, kind = f"via:{self.layer}:{self.via_index}", "via"
            self.via_index += 1
        else:
            if pin is None:
                raise ValueError(f"{context}: component pad without a PinRef")
            _attributes(pin, ("componentRef", "pin"), (), context)
            base = f"pad:{self.layer}:{pin.get('componentRef')}.{pin.get('pin')}"
            self.pad_ids[base] += 1
            feature_id = base if self.pad_ids[base] == 1 else f"{base}#{self.pad_ids[base]}"
            kind = "pad"
            a, b, tx, c, d, ty = self.matrix
            self.pad_pins.append(PadPin(feature_id, pin.get("componentRef"), pin.get("pin"),
                                        (a*x + b*y + tx, c*x + d*y + ty)))
        exact = tuple(_number(parts["Location"].get(k), context) for k in ("x", "y"))
        self.pad_index.setdefault((self.layer, element.get("padstackDefRef"), net) + exact,
                                  []).append(len(self.pads))
        self.pads.append(CopperFeature(feature_id, net, self.layer, kind, polygon,
                                       source_ref=ref))

    def _local_shape(self, shape_id, context):
        """Pad-frame outline of a dictionary shape, in source units (built once per id)."""
        if shape_id not in self.local_shapes:
            if shape_id not in self.shapes:
                raise ValueError(f"{context}: pad shape {shape_id!r} is not in DictionaryStandard")
            shape, ref = self.shapes[shape_id]
            where = f"{context}, shape {shape_id!r} ({ref})"
            self.local_shapes[shape_id] = _dictionary_outline(shape, ref, where, self.track_tol)
        return self.local_shapes[shape_id]

    def _track(self, element, tag, net, ref):
        context = _context(self.layer, net, ref)
        expected = _LINE_ATTRIBUTES if tag == "Line" else _ARC_ATTRIBUTES
        if set(element.attrib) != expected:
            raise ValueError(f"{context}: {tag} attributes must be exactly {sorted(expected)}, "
                             f"found {sorted(element.attrib)}")
        children = list(element)
        if len(children) != 1 or _tag(children[0]) != "LineDesc":
            raise ValueError(f"{context}: {tag} must hold exactly one inline LineDesc, found "
                             f"{[_tag(c) for c in children]}")
        desc = children[0]
        if set(desc.attrib) != {"lineWidth", "lineEnd"} or len(desc):
            raise ValueError(f"{context}: LineDesc must have exactly lineWidth and lineEnd")
        if desc.get("lineEnd") != "ROUND":
            raise ValueError(f"{context}: lineEnd {desc.get('lineEnd')!r} is not supported; "
                             "only ROUND")
        width = float(_number(desc.get("lineWidth"), context))
        point = lambda x, y: (float(_number(element.get(x), context)),
                              float(_number(element.get(y), context)))
        start, end = point("startX", "startY"), point("endX", "endY")
        try:
            if tag == "Line":
                outline = segment_outline(start, end, width, self.track_tol)
            else:
                flag = element.get("clockwise")
                if flag not in ("true", "false"):
                    raise ValueError(f"clockwise must be 'true' or 'false', found {flag!r}")
                outline = arc_outline(start, end, point("centerX", "centerY"), flag == "true",
                                      width, self.track_tol, self.track_tol)
        except ValueError as error:
            raise ValueError(f"{context}: {error}") from None
        a, b, tx, c, d, ty = self.matrix
        polygon = Polygon([(a*x + b*y + tx, c*x + d*y + ty) for x, y in outline.exterior.coords])
        if not polygon.is_valid:
            raise ValueError(f"{context}: invalid track outline: {explain_validity(polygon)}")
        self.tracks.append(CopperFeature(f"track:{self.layer}:{self.track_index}", net,
                                         self.layer, "track", polygon, source_ref=ref))
        self.track_index += 1


_CORNER_FLAGS = ("upperRight", "upperLeft", "lowerRight", "lowerLeft")


def _dictionary_outline(shape, ref, context, tol):
    """Pad-frame outline (source units, Y up, centred) of one DictionaryStandard shape."""
    tag = _tag(shape)
    size = lambda name: float(_number(shape.get(name), context))
    try:
        if tag == "Circle":
            _attributes(shape, ("diameter",), (), context)
            return circle_outline(size("diameter"), tol)
        if tag == "RectCenter":
            _attributes(shape, ("width", "height"), (), context)
            return rect_outline(size("width"), size("height"))
        if tag == "Oval":
            _attributes(shape, ("width", "height"), (), context)
            return oval_outline(size("width"), size("height"), tol)
        if tag == "RectRound":
            _attributes(shape, ("width", "height", "radius") + _CORNER_FLAGS, (), context)
            if any(shape.get(flag) != "true" for flag in _CORNER_FLAGS):
                raise ValueError("RectRound with some corners not rounded is not supported")
            return roundrect_outline(size("width"), size("height"), size("radius"), tol)
        if tag == "RectCham":
            _attributes(shape, ("width", "height", "chamfer"), _CORNER_FLAGS, context)
            if any(shape.get(flag) not in (None, "true") for flag in _CORNER_FLAGS):
                raise ValueError("RectCham corner flags must be 'true' when present")
            corners = {flag for flag in _CORNER_FLAGS if shape.get(flag) == "true"}
            return chamfer_outline(size("width"), size("height"), size("chamfer"), corners)
        if tag == "Contour":
            outline, holes = _contour_rings(shape, ref, context)
            as_float = lambda ring: [(float(x), float(y)) for x, y in ring]
            polygon = Polygon(as_float(outline), [as_float(h) for h in holes])
            if not polygon.is_valid:
                raise ValueError(f"invalid custom pad outline: {explain_validity(polygon)}")
            return polygon
    except ValueError as error:
        message = str(error)
        raise ValueError(message if message.startswith(context) else f"{context}: {message}") \
            from None
    raise ValueError(f"{context}: pad shape {tag} is not supported")


def _shape_dictionary(root):
    """DictionaryStandard EntryStandard id -> (its one shape element, element path)."""
    shapes = {}
    for dictionary in root.iter(_q("DictionaryStandard")):
        for entry, tag, ref in _children(dictionary, "DictionaryStandard"):
            if tag != "EntryStandard":
                continue
            shape_id, content = entry.get("id"), list(entry)
            if shape_id in shapes:
                raise ValueError(f"{ref}: duplicate dictionary id {shape_id!r}")
            if len(content) != 1:
                raise ValueError(f"{ref}: EntryStandard {shape_id!r} must hold exactly one shape")
            shapes[shape_id] = (content[0], f"{ref}/{_tag(content[0])}[1]")
    return shapes


_HOLE_KINDS = {"VIA": ("via", "plated"), "PLATED": ("pin", "plated"),
               "NONPLATED": ("mechanical", "nonplated")}
_HOLE_ATTRIBUTES = ("name", "diameter", "platingStatus", "plusTol", "minusTol", "x", "y")


def _read_drills(feature, layer_element, ref, copper, transform, pads, pad_index):
    """DrillFeature records of one DRILL LayerFeature, and drill_id set on matching pads.

    A hole belongs to the pad or via copper of the same padstack and net at exactly its
    position, on each copper layer of the drill layer's span. Holes are not cut from that
    copper here: the finished hole size is a setup value (see the module docstring).
    """
    spans = layer_element.findall(_q("Span"))
    if len(spans) != 1 or set(spans[0].attrib) != {"fromLayer", "toLayer"}:
        raise ValueError(f"{ref}: drill layer must have one Span with fromLayer and toLayer")
    start, end = spans[0].get("fromLayer"), spans[0].get("toLayer")
    if start not in copper or end not in copper or copper.index(start) > copper.index(end):
        raise ValueError(f"{ref}: drill span {start!r} to {end!r} is not an ordered pair of "
                         "copper layers")
    span = copper[copper.index(start):copper.index(end) + 1]
    a, b, tx, c, d, ty = (Decimal(repr(v)) for v in transform)
    if b or c or abs(a) != abs(d):
        raise ValueError("drill diameters need a transform without rotation or shear")
    drills = []
    for group, tag, group_ref in _children(feature, ref):
        if tag != "Set":
            raise ValueError(f"{group_ref}: {tag} in a drill LayerFeature is not supported")
        net = group.get("net")
        context = f"drill layer {feature.get('layerRef')!r}, " + (
            f"net {net!r}" if net is not None else "no net") + f" ({group_ref})"
        if set(group.attrib) - {"geometry", "net"}:
            raise ValueError(f"{context}: Set attributes {sorted(group.attrib)} are not supported")
        holes = list(_children(group, group_ref))
        if len(holes) != 1 or holes[0][1] != "Hole":
            raise ValueError(f"{context}: drill Set must hold exactly one Hole, found "
                             f"{[t for _, t, _ in holes]} (slots and backdrills are not supported)")
        hole, _, hole_ref = holes[0]
        context = context.replace(group_ref, hole_ref)
        _attributes(hole, _HOLE_ATTRIBUTES, (), context)
        status = hole.get("platingStatus")
        if status not in _HOLE_KINDS:
            raise ValueError(f"{context}: platingStatus {status!r} is not supported")
        for tolerance in ("plusTol", "minusTol"):
            if _number(hole.get(tolerance), context) != 0:
                raise ValueError(f"{context}: nonzero {tolerance} is not supported")
        x, y = (_number(hole.get(k), context) for k in ("x", "y"))
        diameter = _number(hole.get("diameter"), context)
        kind, plating = _HOLE_KINDS[status]
        drill_id = f"drill:{hole.get('name')}"
        drills.append(DrillFeature(drill_id, (float(a*x + b*y + tx), float(c*x + d*y + ty)),
                                   float(diameter * abs(a)), plating, start, end, kind))
        linked = 0
        for layer in span:
            matches = pad_index.get((layer, group.get("geometry"), net, x, y), [])
            if len(matches) > 1:
                raise ValueError(f"{context}: {len(matches)} pads on {layer!r} share this hole")
            for i in matches:
                pad = pads[i]
                if pad.drill_id is not None:
                    raise ValueError(f"{context}: pad {pad.id} already has drill {pad.drill_id}")
                if (pad.kind == "via") != (kind == "via"):
                    raise ValueError(f"{context}: {status} hole at {pad.kind} {pad.id}")
                pads[i] = replace(pad, drill_id=drill_id)
                linked += 1
        if plating == "plated" and not linked:
            raise ValueError(f"{context}: plated hole with no pad or via copper on any layer of "
                             f"its span {span}")
    return drills


def parse_ipc2581_copper(data):
    """Nets and zone-fill features from the bytes of an IPC-2581 C file (see module doc)."""
    root, meta = parse_ipc2581_tree(data)
    cad = root.find(f"{_q('Ecad')}/{_q('CadData')}")
    steps = cad.findall(_q("Step"))
    if len(steps) != 1:
        raise ValueError(f"expected exactly one Step in CadData, found {len(steps)}")
    for tag in ("LogicalNet", "PhyNetGroup", "PhyNet"):
        if steps[0].find(_q(tag)) is not None:
            raise ValueError(f"Step contains {tag}: this reader takes nets from Set net "
                             "attributes only and does not read net lists")
    layer_elements = {layer.get("name"): layer for layer in cad.findall(_q("Layer"))}
    defined = set(layer_elements)
    copper = [layer.id for layer in meta.copper_layers]
    nets, fills, tracks, pads, unread, seen, uninspected = set(), [], [], [], Counter(), set(), []
    shapes = _shape_dictionary(root)
    pad_index, drill_features, pad_pins = {}, [], []
    for feature, tag, _ in _children(steps[0], "Step"):
        if tag != "LayerFeature":
            continue
        layer = feature.get("layerRef")
        ref = f"LayerFeature[@layerRef={layer!r}]"
        if layer not in defined:
            raise ValueError(f"{ref}: layer is not defined in CadData")
        if layer in seen:
            raise ValueError(f"{ref}: more than one LayerFeature for this layer")
        seen.add(layer)
        for group in feature.iter(_q("Set")):
            name = group.get("net")
            if name is not None and not name.strip():
                raise ValueError(f"{ref}: Set with a blank net name")
            nets.add(name)
        nets.discard(None)
        function = layer_elements[layer].get("layerFunction")
        if function == "DRILL":
            drill_features.append((feature, ref))   # read once all pads are known
            continue
        if function == "ROUT" and len(feature):
            raise ValueError(f"{ref}: routed slots are not supported")
        if layer not in copper:
            uninspected.append(layer)
            continue
        reader = _CopperLayerReader(layer, meta.source.xy_transform, fills, tracks, pads, shapes,
                                    unread, pad_index, pad_pins)
        for child, child_tag, child_ref in _children(feature, ref):
            if child_tag != "Set":
                raise ValueError(f"{child_ref}: {child_tag} in a copper LayerFeature is not "
                                 "supported")
            reader.set(child, child_ref)
    drills = []
    for feature, ref in drill_features:
        drills += _read_drills(feature, layer_elements[feature.get("layerRef")], ref, copper,
                               meta.source.xy_transform, pads, pad_index)
    source = replace(meta.source, assumptions=ASSUMPTIONS)
    net_records = tuple(Net(name, name) for name in sorted(nets))
    # Reuse the model's reference checks (unique IDs, known layers and nets); not returned.
    BoardModel(source, meta.copper_layers, net_records, tuple(fills) + tuple(tracks) + tuple(pads),
               drills=tuple(drills))
    order = {layer: i for i, layer in enumerate(copper)}
    counts = tuple(UnreadCount(kind, layer, net, n) for (layer, kind, net), n in
                   sorted(unread.items(), key=lambda item: (order[item[0][0]], item[0][1],
                                                            item[0][2] or "")))
    return Ipc2581Copper(source, meta.copper_layers, net_records, tuple(fills), tuple(tracks),
                         tuple(pads), tuple(pad_pins), tuple(drills), counts,
                         tuple(uninspected))


def read_ipc2581_copper(path):
    """Read one file once; the same bytes are hashed and parsed."""
    return parse_ipc2581_copper(Path(path).read_bytes())
