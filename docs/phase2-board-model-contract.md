# Phase 2: proposed minimum board-model contract

Initial contract inspected on 2026-11-15; the first records/validation increment
was then implemented at Erekle's request in `pdnlab_dc.board`. This remains a
development interface, not a completed production importer or frozen schema.

## Existing reader boundary

`experiments/exp08_ipc2581_net.py:read_net_ipc2581` reads one named net from
revision C in millimetres. It returns an experiment-specific dictionary:

| Field | Current meaning | Migration requirement |
| --- | --- | --- |
| `copper` | Ordered copper-layer names | Preserve order and unique layer IDs |
| `centres` | Layer centre Z and copper thickness, mm | Named fields in metres; positive thickness |
| `tracks` | Layer, endpoints and width, mm | Preserve net/layer identity and source feature traceability |
| `zones` | Layer-indexed filled polygons with cutouts, mm | Preserve actual fill and holes; no silent topology repair |
| `pads` | Reference/pin, kind, layers, centre, polygon and optional drill | Structured pin identity and per-layer copper shapes |
| `vias` | Centre, pad diameter, hole diameter and end-layer span | Separate copper pads, drill span and eventual electrical contacts |
| `relief` | Empty for the export reader | Absence means unknown, not zero gap |

The reader reverses IPC Y to match the native-board experiment. Its pad `type`
is empty, and it has no operating loads. It imports the experiment's solve
module directly. The production model must not depend on either experiment,
the XML tree, Gmsh or KiCad bindings.

## Recommended minimum records

| Record | Required content |
| --- | --- |
| Source info | Input hash, format/revision, original units, exporter version if available, coordinate transform/assumptions |
| Copper layer | Unique ID, preserved label, ordered position, centre Z in metres, thickness in metres |
| Net | Unique ID and preserved name |
| Copper feature | Feature ID, net ID, layer ID, source kind and board-coordinate polygon(s) with holes |
| Pin pad | Structured `(component_ref, pin_number)` identity, net ID, position, per-layer copper feature references and optional drill reference |
| Drill feature | ID, position, diameter in metres, plating state and explicit start/end layer IDs; associated pin/via identity |

For the first migration, recommend keeping the experiment's board XY frame
and stack-Z convention, converting all lengths to metres at the adapter
boundary. Record the transform; reverse IPC Y exactly once. Geometry and
stack Z must share one documented frame, checked by asymmetric fixtures.
Original layer names are labels, not solver-specific layer semantics.

Drill span does **not** establish electrical contact on every intervening
layer. Preserve per-layer pad copper; determine contacted layers later from
the physical copper geometry. Do not turn a through-hole span into automatic
electrical ties. Plating thickness and finished-hole/drill interpretation
remain explicit setup assumptions, recorded when constructing barrel models.

Pin electrical type, thermal-relief hints and exporter version may be unknown;
represent absence explicitly. Sources, loads, contact regions and material/
plating assumptions remain separate setup data. Filled copper already carries
thermal-spoke geometry; missing relief metadata must not erase those spokes.
Confirmed on the public board in `tests/fixtures/thermal_relief` (KiCad 10.0.8):
spokes and solid connections are part of the fill outline and overlap their pads.

## First implementation and stop boundary

Implemented frozen records: `SourceInfo`, `CopperLayer`, `Net`, `CopperFeature`,
`PinPad`, `DrillFeature`, and aggregate `BoardModel`. Constructors validate
finite SI values, unique IDs, positive dimensions, valid 2D Polygon/MultiPolygon
topology, references/net consistency, layer order/nonoverlap and drill spans.
Input collections are snapshotted as tuples; geometry uses immutable Shapely 2
objects. No geometry is repaired or transformed by these constructors.

`SourceInfo.xy_transform` records source-XY to stored-SI-XY coefficients as
`(a, b, tx, c, d, ty)`, meaning `(a*x+b*y+tx, c*x+d*y+ty)`; the importer must
apply them exactly once. Source units are recorded as `m`, `mm` or `in`.
Unknown exporter version, pin electrical type and thermal gap are `None`.
Unassigned feature/pad nets use `net_id=None`, without inventing a net name.

Physical pad instances have unique `PinPad.id` values. Their structured
`pin_key=(component_ref, pin_number)` may repeat for separate lands of one
electrical pin; conflicting nets for the same key are rejected. Per-layer
copper features may have different shapes. Via pad features can reference a
drill ID without inventing copper or electrical contacts on other span layers.
Drill diameter remains separate from setup's finished-hole/plating assumptions.

Synthetic validation cases live in `tests/test_board.py`. This increment stops
at these records and validators.

Then stop. XML parsing/migration, copper unions/cleanup, contact discovery,
meshing, setup-file parsing and real-board solves are separate small tasks.
Initial importer support can remain narrower than the model: unsupported
geometry must raise with feature/net/layer context, rather than be omitted.

No KiCad 11 export or new numerical solve was run for these records. Current
development-documentation support for IPC-2581 is not importer compatibility
validation; cross-version export fixtures remain future work.

## Header and stackup metadata reader (2026-11-16)

`pdnlab_dc.ipc2581_metadata` is the first XML increment. It reads only the
file header and stackup, and returns `Ipc2581Metadata(source, copper_layers)`:
a `SourceInfo` and ordered `CopperLayer` records. It is not a `BoardModel`
and reads no nets, tracks, pads, zones or drills; a caller cannot mistake it
for an imported board with empty copper.

```python
from pdnlab_dc.ipc2581_metadata import read_ipc2581_metadata

meta = read_ipc2581_metadata("board.xml")
meta.source.input_sha256, meta.source.exporter_version
[(c.id, c.z_m, c.thickness_m) for c in meta.copper_layers]
```

Supported subset; anything else raises `ValueError`:

| Item | Supported | Rejected |
| --- | --- | --- |
| Document | Uncompressed XML, root `IPC-2581` in the IPC namespace, revision C | Other roots/namespaces, revision B, zip archives, malformed XML |
| Units | `CadHeader` and `Content` dictionaries in `MILLIMETER` | `INCH`, `MICRON`, missing units |
| Layers | Unique names; copper is `layerFunction="CONDUCTOR"` | Duplicate or unnamed layers; `SIGNAL`, `PLANE`, `MIXED`, `CONDFOIL`, `CONDFILM` |
| Stackup | One `Stackup`, one `StackupGroup`, unique integer `sequence`, each layer once | Missing or multiple stackups/groups, unknown or repeated references |
| Copper span | `CONDUCTOR` and `DIEL*` rows with finite positive thickness, dielectric between coppers | Other functions between coppers, adjacent coppers, copper missing from the stackup |

Stack Z: zero at the outer face of the first copper layer in stackup
`sequence`, increasing with sequence; `z_m` is the copper centre. Rows before
the first and after the last copper are ignored. Thicknesses are summed as
exact decimals in millimetres and converted to metres once. Layer `id` and
`label` are the exported layer name.

`SourceInfo.xy_transform` is `(1e-3, 0, 0, 0, -1e-3, 0)`: millimetres to
metres with IPC Y reversed once, as recommended above. This reader stores no
XY data, so it does not apply the transform; the geometry reader must apply
it exactly once. `exporter_version` is `"<name> <revision>"` of the single
`HistoryRecord/FileRevision/SoftwarePackage`, or `None` when that is absent,
incomplete or ambiguous. `input_sha256` is the hash of the parsed bytes.

These conventions are recommendations pending Erekle's approval. The reader
has been run on synthetic XML only (`tests/test_ipc2581_metadata.py`); reading
a real KiCad 10 export, dielectric sublayers and other exporters are untested.

## Nets and filled-zone contour reader (2026-11-16)

Renamed with the track increment: the module is now `pdnlab_dc.ipc2581_copper`,
the functions `parse_ipc2581_copper`/`read_ipc2581_copper` and the result
`Ipc2581Copper`, which also has `tracks`, `pads` and `drills` (sections below). Fill
behaviour is unchanged. It reuses `parse_ipc2581_tree` from the metadata reader (one parse;
header, units, layers and provenance checked there only). The result holds
`source`, `copper_layers`, `nets`, `fills`, `tracks`, `pads`, `drills`,
`unread`, `uninspected_layers` and `scope`. It is not a `BoardModel` and is not a
complete board.

```python
from pdnlab_dc.ipc2581_copper import read_ipc2581_copper

copper = read_ipc2581_copper("board.xml")
[(f.id, f.net_id, f.layer_id, f.geometry.area) for f in copper.fills]
copper.unread              # copper a later increment reads; empty for KiCad boards now
copper.uninspected_layers  # mask, paste, legend, ... LayerFeatures not looked at
```

What KiCad 10.0.8 writes, from its exporter source (`pcb_io_ipc2581.cpp` at tag
`10.0.8`); this is source reading, not export evidence:

| Question | Finding | Reader behaviour |
| --- | --- | --- |
| Where nets are listed | Only as `Set@net`. `generateLogicalNets` exists but is not called; no `PhyNet`/`PhyNetGroup`. `net` is omitted for net code 0. Names pass through `genString`: `:` becomes `_`, and `_N` is appended on a clash with another generated name. | Nets are the distinct `Set@net` values in the Step, with exported names. A copper `Set` without `net` is unassigned, `net_id=None`. `LogicalNet`/`PhyNet`/`PhyNetGroup` raise. |
| Which copper `Contour`s are zone fills | Board zones: `Contour` in the net's `Set/Features/UserSpecial`. Also `Contour`s in teardrop sets (`geometryUsage="TEARDROP"`), copper text (`"TEXT"`) and footprint-owned zones (`Set@componentRef`, no `net`). Board graphic polygons/rectangles/circles are `UserPrimitiveRef`s to the user dictionary. | Only the first kind is read as `fill`. Teardrops, copper text, footprint graphics and `UserPrimitiveRef` shapes raise, with or without a net. A footprint-owned zone with a `Contour` raises because its net is not exported. |
| Mixed sets | One net `Set` per layer holds fills, track `Line`/`Arc` and via `Pad`s (`padUsage="VIA"`); pin pads go in a second `Set`. Board graphic segments/arcs share the `UserSpecial` with tracks and are indistinguishable from them. | `Line` and `Arc` are read as tracks, `Pad`s as pads or vias (sections below). |
| Fill topology | The zone filler calls `Fracture()`: each fill is one outline that reaches every hole along a zero-width slit (an edge and, later, the same edge reversed). Shapely rejects such a ring as self-intersecting. | Each reversed edge pair is removed, splitting the ring into one outline and its holes. This is the criterion of KiCad's own `unfractureSingle`. No vertex is moved; area is unchanged. Explicit `Cutout`s are kept as holes. |

Supported fill geometry, anything else raising with layer, net and element path:
`Contour` = one `Polygon` then `Cutout`s; rings of `PolyBegin` then
`PolyStepSegment` with exactly `x` and `y`, closed, finite decimals; no `Xform`
anywhere between the `LayerFeature` and the `Contour`, and no nonzero
`Location`. Edge pairs must nest; crossing pairs, repeated or zero-length edges,
degenerate or zero-area rings, more than one outline after slit removal, and an
invalid polygon raise. Unknown elements, other namespaces, unknown `Set`
attributes (for example `polarity`), unknown `geometryUsage` or `padUsage`,
duplicate or undefined `LayerFeature` layers and more than one `Step` raise.

Coordinates are exact decimals in millimetres; `SourceInfo.xy_transform` is
applied once and each stored value rounded once to metres. One `CopperFeature`
per `Contour`, kind `fill`, ID `fill:<layer>:<k>` with `k` counting fills on that
layer in document order, `source_ref` the element path, for example
`LayerFeature[@layerRef='F.Cu']/Set[1]/Features[1]/UserSpecial[1]/Contour[2]`.
IDs are stable for the same file, not across re-exports. Overlapping fills are
left overlapping; nothing is unioned. Unread content inside counted elements is
not validated; that belongs to the track and pad increments.

Erekle's rule (2026-11-16): copper on a net to be simulated must be included,
so only content a planned increment reads (tracks, pads, vias) may be counted;
everything else on copper raises. Footprint-owned zone fills raise; move such
zones to board level. Teardrops raise under the same rule (none in the
experiment-6 export). Erekle approved the slit handling on 2026-11-16.
Synthetic XML only (`tests/test_ipc2581_fills.py`); no real export has been
read by this module.

## Track reader (2026-11-16)

Every `Line` and `Arc` in a copper layer's `Set/Features/UserSpecial` becomes one
`CopperFeature` of kind `track` in `Ipc2581Copper.tracks`: ID `track:<layer>:<k>`
(`k` counts tracks on that layer in document order), `source_ref` the element
path, net from the enclosing `Set` (`None` if absent). Copper graphic segments
and arcs are written the same way by KiCad and are read as tracks too.

KiCad 10.0.8 writes a track segment as
`<Line startX startY endX endY><LineDesc lineWidth lineEnd="ROUND"/></Line>` and
an arc as `<Arc startX startY endX endY centerX centerY clockwise>` with the same
inline `LineDesc`. On the public arc board the `clockwise` flag is in IPC (Y-up)
coordinates, and the centre is rounded to 5 decimals, so start and end radii can
differ by a few nanometres.

Accepted, anything else raising with layer, net and element path: exactly those
attributes; exactly one inline `LineDesc` with exactly `lineWidth` and
`lineEnd="ROUND"`; a positive decimal width; `clockwise` `true` or `false`;
start and end radii within 1 µm of each other; arc radius larger than half the
width; distinct arc start and end; no nonzero `Location` above it.

Outlines (`pdnlab_dc.track_outline`, unit-free) are built in source millimetres
and every vertex is transformed once to metres. A segment is a rectangle plus two
half-circle caps; a zero-length segment is a circle. An arc is four true circles:
outer edge (R + w/2) and inner edge (R - w/2) around the centre, R the mean of
the start and end radii, and caps of radius w/2 centred exactly on the exported
start and end. Every vertex lies on its true circle; each piece gets the fewest
equal steps that keep every chord within 1 µm of the circle. Chords cut copper
on convex pieces and add up to 1 µm of copper on an arc's inner edge; the total
area is always slightly below exact (Erekle's choice, 2026-11-16).

Not unioned with fills, pads or each other: overlaps and the joints between
segments remain for the cleanup increment.

## Pad and via reader (2026-11-21)

Every `Pad` in a copper layer's `Set` (or in a nested via `Set`) becomes one
`CopperFeature` in `Ipc2581Copper.pads`: kind `pad` with ID
`pad:<layer>:<component>.<pin>` from its `PinRef` (`#2`, `#3`... for a pin number
a footprint repeats), or kind `via` with ID `via:<layer>:<k>` when the `Set` has
`padUsage="VIA"`. `source_ref` is the element path; the net comes from the `Set`.

KiCad 10.0.8 writes a pad as
`<Pad padstackDefRef><Xform rotation/>?<Location x y/><StandardPrimitiveRef id/><PinRef componentRef pin/>?</Pad>`,
with `Location` already absolute and `rotation` the pad's absolute angle. Its shape
is an `EntryStandard` in `DictionaryStandard`:

| KiCad pad | Dictionary shape | Outline (`pdnlab_dc.pad_outline`, pad frame, Y up) |
| --- | --- | --- |
| circle | `Circle diameter` | inscribed circle, 1 µm rule |
| rectangle | `RectCenter width height` | exact |
| oval | `Oval width height` | stadium on the long side (a circle if equal) |
| round-rect | `RectRound width height radius` + all four corner flags `true` | four inscribed quarter circles |
| chamfered rect | `RectCham width height chamfer` + `upperLeft`/... flags `true` | exact 45-degree cuts at the named corners |
| trapezoid, custom | `Contour` (pad frame, Y already flipped) | as given; slits split as for fills; never repaired |

The outline is turned counter-clockwise by `rotation` (IPC, Y up) about the
pad's `Location`, then every vertex is transformed once to metres. A back-side
pad has no mirror flag: KiCad stores flipped footprints already mirrored (a
`top_left` chamfer becomes `bottom_left`), and the export copies that.
Through-hole pads and vias appear on every copper layer the export lists them on
and are read there; their outlines are solid until the drills increment cuts the
holes and adds the barrels.

Raises, with layer, net and element path: an `Xform` with anything but
`rotation`; a missing or repeated `Location` or `StandardPrimitiveRef`; a
`UserPrimitiveRef` or any other element in a pad; extra pad attributes; a
component pad without a `PinRef` or a via with one; an unknown shape id; a
`RectRound` with a corner not rounded, a `RectCham` flag other than `true`, any
other shape element, or a shape with children; sizes that are not positive, a
radius or chamfer over half the shorter side; curved steps in a custom outline.
A duplicate dictionary id or an entry with more than one shape raises with the
dictionary path.

Exporter limits, from KiCad 10.0.8's source, not detectable in the export:
shapes are written for `PADSTACK::ALL_LAYERS`, so a per-layer padstack (different
front, inner or back shapes) comes out wrong on the other layers; a pad offset is
added without rotating it, so a rotated pad with an offset is misplaced; a
chamfered rect that is also rounded loses its rounding. Erekle's test board has
none of these.

Checked against KiCad's own SVG plot of each copper layer (`kicad-cli pcb export
svg`), which is drawn by KiCad's plotter independently of the exporter: every pad
and via of `tests/fixtures/pad_shapes` matches within 0.433% of its area
(`tests/test_pad_shapes_fixture.py`), the difference being curve approximation.

## Drill reader (2026-11-21)

Every `Hole` in a `layerFunction="DRILL"` LayerFeature becomes one
`DrillFeature` in `Ipc2581Copper.drills`: ID `drill:<hole name>`, position in
metres with IPC Y reversed once, diameter in metres, start and end layer from
the drill layer's `Span`, and kind and plating from `platingStatus`:
`VIA` → `via`/`plated`, `PLATED` → `pin`/`plated`, `NONPLATED` →
`mechanical`/`nonplated`.

KiCad 10.0.8 writes one drill layer per span (`<Layer name="F.Cu_B.Cu"
layerFunction="DRILL"><Span fromLayer toLayer/>`): via holes on each via's own
top-to-bottom span, pad holes always `F.Cu` to `B.Cu`. Each hole is
`<Set geometry="<padstack>" net><Hole name diameter platingStatus plusTol="0.0"
minusTol="0.0" x y/></Set>`; oblong holes go to a `layerFunction="ROUT"` slot
layer as `SlotCavity`, and backdrills add references to the hole.

Nothing names the pad a hole belongs to. The pad and via copper of the same
padstack (`Set@geometry` = `Pad@padstackDefRef`) and net at exactly the hole's
position, on each copper layer of the span, gets `drill_id`. A layer without such
copper is fine (KiCad may remove unused inner rings), and so is a non-plated hole
without copper. A span does not by itself mean contact (see above).

Raises, with drill layer, net and element path: two pads on one layer for a
hole; a pad claimed by two holes; a `VIA` hole at a component pad or a `PLATED`
hole at a via; a plated hole with no copper on any span layer; another
`platingStatus`; a nonzero tolerance; a `Set` that is not exactly one `Hole`
(slots, backdrills); other `Set` attributes; a drill layer without one `Span`,
or a span that is not an ordered pair of copper layers; a `ROUT` (slot) layer
with content.

Holes are recorded, not cut: pad and via outlines stay solid. The diameter is
KiCad's drill value; whether that is the drilled or the finished size, and the
plating thickness, are setup assumptions, applied when the cleanup increment
cuts holes and the contact model adds barrels.

Checked against KiCad's board file (`tests/test_pad_shapes_fixture.py`): every
via of `tests/fixtures/pad_shapes` matches its KiCad via in position, drill,
span and net, with via copper linked on all four layers, and every plated pin
hole matches its pad's drill, plating and net and sits at its pad's centre.
Spans other than `F.Cu` to `B.Cu` are tested with synthetic XML only (a blind
via on a three-layer stack).
