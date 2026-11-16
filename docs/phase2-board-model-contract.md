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
