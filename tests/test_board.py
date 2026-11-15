"""Neutral model validation only: no XML, mesher, solver or private boards."""

from dataclasses import FrozenInstanceError, replace

import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from pdnlab_dc.board import BoardModel, CopperFeature, CopperLayer, DrillFeature, Net, PinPad, SourceInfo


def source():
    return SourceInfo("0"*64, "IPC-2581", "C", "mm", [.001, 0, 0, 0, -.001, 0])


def board():
    layers = [CopperLayer("top", "F.Cu", 17.5e-6, 35e-6),
              CopperLayer("inner", "In1.Cu", .0008, 18e-6),
              CopperLayer("bottom", "B.Cu", .0015825, 35e-6)]
    features = [CopperFeature("pad:top", "power", "top", "pad", box(0, 0, .002, .001)),
                CopperFeature("pad:bottom", "power", "bottom", "pad", box(0, 0, .001, .002))]
    drill = DrillFeature("hole", [.0005, .0005], .0003, "plated", "top", "bottom", "pin")
    pad = PinPad("J1:1:a", "J1", "1", "power", [.0005, .0005],
                 ["pad:top", "pad:bottom"], "hole")
    return BoardModel(source(), layers, [Net("power", "+3V3")], features, [pad], [drill])


def test_si_records_preserve_per_layer_shapes_and_do_not_infer_contacts():
    b = board()
    assert b.layers[0].thickness_m == 35e-6
    assert b.source.xy_transform == (.001, 0., 0., 0., -.001, 0.)
    assert b.pads[0].position_m == (.0005, .0005)  # No second units/axis conversion.
    assert b.pads[0].pin_key == ("J1", "1")
    assert b.features[0].geometry.bounds != b.features[1].geometry.bounds
    assert {f.layer_id for f in b.features} == {"top", "bottom"}
    assert b.drills[0].start_layer_id == "top" and b.drills[0].end_layer_id == "bottom"
    assert all(f.layer_id != "inner" for f in b.features)  # Span did not invent a pad/contact.
    assert b.pads[0].electrical_type is None
    assert b.features[0].thermal_gap_m is None
    assert b.source.exporter_version is None


def test_frozen_records_snapshot_nested_input_sequences():
    coords, refs, assumptions = [0., 0.], ["a"], ["hole meaning supplied separately"]
    pad = PinPad("pad", "U1", "A.1", None, coords, refs)
    s = replace(source(), assumptions=assumptions)
    layer = CopperLayer("top", "top", 0., 35e-6)
    feature = CopperFeature("a", None, "top", "pad", box(0, 0, .001, .001))
    layers, features = [layer], [feature]
    b = BoardModel(s, layers, features=features, pads=[pad])
    coords[0], refs[0], assumptions[0] = 999., "changed", "changed"
    layers.clear()
    features.clear()
    assert b.pads[0].position_m == (0., 0.) and b.pads[0].copper_feature_ids == ("a",)
    assert b.source.assumptions == ("hole meaning supplied separately",)
    assert len(b.layers) == len(b.features) == 1
    with pytest.raises(FrozenInstanceError):
        b.layers = ()
    with pytest.raises(FrozenInstanceError):
        pad.id = "changed"


def test_polygon_holes_and_disconnected_parts_are_preserved_without_repair():
    g = Polygon(box(0, 0, .003, .003).exterior.coords, [box(.001, .001, .002, .002).exterior.coords])
    feature = CopperFeature("fill", None, "top", "fill", MultiPolygon([g, box(.004, 0, .005, .001)]))
    assert len(feature.geometry.geoms) == 2
    assert len(feature.geometry.geoms[0].interiors) == 1
    assert feature.geometry.area == pytest.approx(9e-6)
    assert replace(feature, thermal_gap_m=0.).thermal_gap_m == 0.  # Zero is distinct from unknown.


def test_repeated_pin_number_retains_distinct_physical_pad_ids():
    b = board()
    extra = CopperFeature("extra", "power", "top", "pad", box(.003, 0, .004, .001))
    pad = PinPad("J1:1:b", "J1", "1", "power", (.0035, .0005), ("extra",))
    b = replace(b, features=(*b.features, extra), pads=(*b.pads, pad))
    assert b.pads[0].pin_key == b.pads[1].pin_key
    assert b.pads[0].id != b.pads[1].id
    with pytest.raises(ValueError, match="conflicting nets"):
        replace(b, nets=(*b.nets, Net("other", "other")),
                features=(*b.features[:-1], replace(extra, net_id="other")),
                pads=(*b.pads[:-1], replace(pad, net_id="other")))


@pytest.mark.parametrize("field,value", [("input_sha256", "bad"), ("format", ""),
    ("original_units", "mil"), ("xy_transform", [1, 0, 0, 0, 0, 0]),
    ("xy_transform", [1, 0, 0]), ("xy_transform", None),
    ("exporter_version", ""), ("assumptions", "guess")])
def test_invalid_source_metadata(field, value):
    with pytest.raises(ValueError):
        replace(source(), **{field: value})


@pytest.mark.parametrize("record,changes", [
    (CopperLayer("a", "A", 0., .001), {"thickness_m": 0.}),
    (CopperLayer("a", "A", 0., .001), {"z_m": float("inf")}),
    (CopperLayer("a", "A", 0., .001), {"thickness_m": True}),
    (CopperLayer("a", "A", 0., .001), {"z_m": 1.7e308, "thickness_m": 1e308}),
    (board().drills[0], {"diameter_m": -1.}),
    (board().drills[0], {"plating": "guess"}),
    (board().drills[0], {"kind": "via", "plating": "nonplated"}),
    (board().pads[0], {"position_m": [0., float("nan")]}),
    (board().pads[0], {"position_m": None}),
    (board().pads[0], {"copper_feature_ids": ["x", "x"]}),
    (board().features[0], {"thermal_gap_m": -.1}),
])
def test_invalid_record_values(record, changes):
    with pytest.raises(ValueError):
        replace(record, **changes)


@pytest.mark.parametrize("geometry", [Polygon(), Polygon([(0, 0), (1, 1), (1, 0), (0, 1)]),
    Polygon([(0, 0, 1), (1, 0, 1), (0, 1, 1)])])
def test_invalid_geometry_is_rejected(geometry):
    with pytest.raises(ValueError, match="geometry"):
        replace(board().features[0], geometry=geometry)


@pytest.mark.parametrize("field", ["layers", "nets", "features", "pads", "drills"])
def test_duplicate_ids_are_rejected_in_each_namespace(field):
    b = board()
    with pytest.raises(ValueError, match="duplicate"):
        replace(b, **{field: (*getattr(b, field), getattr(b, field)[0])})


@pytest.mark.parametrize("change", [
    {"layers": ()},
    {"layers": tuple(reversed(board().layers))},
    {"layers": (board().layers[0], replace(board().layers[1], z_m=20e-6))},
    {"features": (replace(board().features[0], layer_id="missing"),)},
    {"features": (replace(board().features[0], net_id="missing"),)},
    {"features": (replace(board().features[0], drill_id="missing"),)},
    {"pads": (replace(board().pads[0], copper_feature_ids=("missing",)),)},
    {"pads": (replace(board().pads[0], net_id=None),)},
    {"pads": (replace(board().pads[0], drill_id="missing"),)},
    {"drills": (replace(board().drills[0], start_layer_id="bottom", end_layer_id="top"),)},
    {"drills": (replace(board().drills[0], end_layer_id="absent"),)},
    {"drills": (replace(board().drills[0], end_layer_id="inner"),)},
])
def test_invalid_model_relationships(change):
    with pytest.raises(ValueError):
        replace(board(), **change)


def test_blind_via_span_and_unknown_plating_remain_explicit():
    b = board()
    via = DrillFeature("via", (.002, .002), .0002, "unknown", "top", "inner", "via")
    copper = CopperFeature("via-pad", "power", "inner", "via_pad", box(.0015, .0015, .0025, .0025),
                            drill_id="via")
    b = replace(b, drills=(*b.drills, via), features=(*b.features, copper))
    assert b.drills[-1].plating == "unknown"
    with pytest.raises(ValueError, match="span"):
        replace(b, features=(*b.features[:-1], replace(copper, layer_id="bottom")))
