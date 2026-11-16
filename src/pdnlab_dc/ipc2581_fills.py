"""IPC-2581 nets and filled-zone contours only. This is not a board importer.

Builds on ipc2581_metadata (header, units, stackup, provenance) and adds:
* Net records: every distinct Set ``net`` attribute in the Step. KiCad 10 writes no
  LogicalNet or PhyNet, and omits ``net`` for copper with no net; such copper gets
  ``net_id=None``. Net names are the exported names, which KiCad may have altered.
* CopperFeature records of kind "fill": each Contour written directly in a copper
  layer's Set/Features/UserSpecial, one feature per Contour, holes preserved.

Copper that later increments will read is counted in ``unread``, by kind, layer and
net, without reading or checking its geometry: track segments and arcs (a graphic
segment or arc on copper is indistinguishable from a track and will be read with them),
pads and via pads. Copper that no planned increment reads raises instead, so copper on
a net is never left out of a result: teardrops, copper text, footprint graphics and
board graphic shapes on copper. A footprint-owned zone raises, because the export drops
its net. LayerFeatures of other layers (drills, slots, mask, paste, legend) are listed
in ``uninspected_layers``. Anything else raises ValueError with layer, net and
element-path context; nothing is skipped silently.

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

from pdnlab_dc.board import BoardModel, CopperFeature, CopperLayer, Net, SourceInfo
from pdnlab_dc.ipc2581_metadata import (_DECIMAL, CONVENTIONS, NAMESPACE, _q,
                                        parse_ipc2581_tree)

SCOPE = ("Nets and filled-zone contours on copper layers only. Tracks, pads, vias and "
         "drills were not read: see unread and uninspected_layers. This is not a complete "
         "board.")
ASSUMPTIONS = (
    SCOPE,
    "xy_transform (source mm to stored m, IPC Y reversed once) was applied once, to fill "
    "coordinates only.",
    "Nets are the distinct Set net attributes in the Step, with their exported names; copper "
    "in a Set without one is unassigned (net_id None).",
    "A fill outline that returns along its own edge (KiCad's fractured form) was split at each "
    "such edge pair into an outline and holes; no other geometry change was made.",
) + CONVENTIONS

# Set geometryUsage values: copper no planned increment reads, so they raise.
_USAGE_KINDS = {"TEXT": "copper text", "GRAPHIC": "footprint graphic on copper",
                "TEARDROP": "teardrop"}
_SET_ATTRIBUTES = {"net", "padUsage", "geometryUsage", "componentRef"}
_UNREAD_SPECIAL = {"Line": "track or graphic segment", "Arc": "track or graphic arc"}


@dataclass(frozen=True)
class UnreadCount:
    """Copper content present in the export but not read: a count, not geometry."""
    kind: str
    layer_id: str
    net_id: str | None
    count: int


@dataclass(frozen=True)
class Ipc2581ZoneFills:
    """Nets and zone fills only. Not a BoardModel: see scope, unread, uninspected_layers."""
    source: SourceInfo
    copper_layers: tuple[CopperLayer, ...]
    nets: tuple[Net, ...]
    fills: tuple[CopperFeature, ...]
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


class _CopperLayerReader:
    def __init__(self, layer, transform, fills, unread):
        self.layer, self.fills, self.unread = layer, fills, unread
        self.transform = [Decimal(repr(v)) for v in transform]
        self.index = 0

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
                self.count("via pad" if pad_usage == "VIA" else "pad", net)
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
                if shifted and child.find(_q("Contour")) is not None:
                    raise ValueError(f"{context}: contour under a nonzero Location is not "
                                     "supported")
                self._special(child, net, child_ref)
            else:
                raise ValueError(f"{context}: {tag} in Features is not supported")

    def _special(self, element, net, ref):
        for child, tag, child_ref in _children(element, ref):
            if tag == "Contour":
                self._contour(child, net, child_ref)
            elif tag in _UNREAD_SPECIAL:
                self.count(_UNREAD_SPECIAL[tag], net)
            else:
                raise ValueError(f"{_context(self.layer, net, ref)}: {tag} in UserSpecial is "
                                 "not supported")

    def _contour(self, element, net, ref):
        context = _context(self.layer, net, ref)
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
        polygon = Polygon(self.to_metres(rings[outer[0]]), [self.to_metres(h) for h in holes])
        if not polygon.is_valid:
            raise ValueError(f"{context}: invalid fill polygon: {explain_validity(polygon)}")
        self.fills.append(CopperFeature(f"fill:{self.layer}:{self.index}", net, self.layer,
                                        "fill", polygon, source_ref=ref))
        self.index += 1


def parse_ipc2581_fills(data):
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
    defined = {layer.get("name") for layer in cad.findall(_q("Layer"))}
    copper = [layer.id for layer in meta.copper_layers]
    nets, fills, unread, seen, uninspected = set(), [], Counter(), set(), []
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
        if layer not in copper:
            uninspected.append(layer)
            continue
        reader = _CopperLayerReader(layer, meta.source.xy_transform, fills, unread)
        for child, child_tag, child_ref in _children(feature, ref):
            if child_tag != "Set":
                raise ValueError(f"{child_ref}: {child_tag} in a copper LayerFeature is not "
                                 "supported")
            reader.set(child, child_ref)
    source = replace(meta.source, assumptions=ASSUMPTIONS)
    net_records = tuple(Net(name, name) for name in sorted(nets))
    # Reuse the model's reference checks (unique IDs, known layers and nets); not returned.
    BoardModel(source, meta.copper_layers, net_records, tuple(fills))
    order = {layer: i for i, layer in enumerate(copper)}
    counts = tuple(UnreadCount(kind, layer, net, n) for (layer, kind, net), n in
                   sorted(unread.items(), key=lambda item: (order[item[0][0]], item[0][1],
                                                            item[0][2] or "")))
    return Ipc2581ZoneFills(source, meta.copper_layers, net_records, tuple(fills), counts,
                            tuple(uninspected))


def read_ipc2581_fills(path):
    """Read one file once; the same bytes are hashed and parsed."""
    return parse_ipc2581_fills(Path(path).read_bytes())
