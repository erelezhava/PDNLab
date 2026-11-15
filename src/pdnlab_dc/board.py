"""Immutable neutral board records. Geometry and lengths are already in SI.

These records do not parse, transform, repair, union, mesh or solve geometry.
Copper outlines retain their own cutouts; separately recorded drills have not
had setup-dependent hole/plating corrections applied. Unknown nets are None.
"""

from dataclasses import dataclass
import math
from numbers import Real
import re

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")


def _optional_text(value, field):
    if value is not None:
        _text(value, field)


def _number(value, field, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number in SI units")
    if positive and value <= 0:
        raise ValueError(f"{field} must be positive")


def _point(record, field):
    values = _sequence(getattr(record, field), field)
    if len(values) != 2:
        raise ValueError(f"{field} must contain two coordinates in metres")
    for v in values:
        _number(v, field)
    object.__setattr__(record, field, tuple(float(v) for v in values))


def _sequence(value, field):
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a sequence, not text")
    try:
        return tuple(value)
    except TypeError as exc:
        raise ValueError(f"{field} must be a sequence") from exc


@dataclass(frozen=True)
class SourceInfo:
    input_sha256: str
    format: str
    revision: str
    original_units: str
    xy_transform: tuple[float, float, float, float, float, float]
    exporter_version: str | None = None
    assumptions: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.input_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", self.input_sha256):
            raise ValueError("input_sha256 must be a 64-digit hexadecimal SHA-256")
        _text(self.format, "format")
        _text(self.revision, "revision")
        if self.original_units not in ("m", "mm", "in"):
            raise ValueError("original_units must be m, mm or in")
        # Source XY -> stored SI XY: (a*x+b*y+tx, c*x+d*y+ty).
        transform = _sequence(self.xy_transform, "xy_transform")
        if len(transform) != 6:
            raise ValueError("xy_transform must contain six affine coefficients")
        for v in transform:
            _number(v, "xy_transform")
        a, b, _, c, d, _ = transform
        determinant = a*d-b*c
        if not math.isfinite(determinant) or determinant == 0:
            raise ValueError("xy_transform must be invertible")
        object.__setattr__(self, "xy_transform", tuple(float(v) for v in transform))
        _optional_text(self.exporter_version, "exporter_version")
        if isinstance(self.assumptions, str):
            raise ValueError("assumptions must be a sequence of strings")
        assumptions = _sequence(self.assumptions, "assumptions")
        for v in assumptions:
            _text(v, "assumption")
        object.__setattr__(self, "assumptions", assumptions)


@dataclass(frozen=True)
class CopperLayer:
    id: str
    label: str
    z_m: float
    thickness_m: float

    def __post_init__(self):
        _text(self.id, "layer id")
        _text(self.label, "layer label")
        _number(self.z_m, "z_m")
        _number(self.thickness_m, "thickness_m", positive=True)
        if not all(math.isfinite(z) for z in (self.z_m-self.thickness_m/2, self.z_m+self.thickness_m/2)):
            raise ValueError("layer Z extent must be finite")


@dataclass(frozen=True)
class Net:
    id: str
    name: str

    def __post_init__(self):
        _text(self.id, "net id")
        _text(self.name, "net name")


@dataclass(frozen=True)
class CopperFeature:
    id: str
    net_id: str | None
    layer_id: str
    kind: str
    geometry: Polygon | MultiPolygon
    source_ref: str | None = None
    drill_id: str | None = None
    thermal_gap_m: float | None = None

    def __post_init__(self):
        for field in ("id", "layer_id", "kind"):
            _text(getattr(self, field), f"feature {field}")
        for field in ("net_id", "source_ref", "drill_id"):
            _optional_text(getattr(self, field), f"feature {field}")
        g = self.geometry
        if (not isinstance(g, (Polygon, MultiPolygon)) or g.is_empty or not g.is_valid
                or g.has_z or not np.isfinite(shapely.get_coordinates(g)).all()
                or not math.isfinite(g.area) or g.area <= 0):
            raise ValueError(f"feature {self.id}: geometry must be valid positive-area 2D polygons")
        if self.thermal_gap_m is not None:
            _number(self.thermal_gap_m, "thermal_gap_m")
            if self.thermal_gap_m < 0:
                raise ValueError("thermal_gap_m must be nonnegative or None")


@dataclass(frozen=True)
class PinPad:
    id: str
    component_ref: str
    pin_number: str
    net_id: str | None
    position_m: tuple[float, float]
    copper_feature_ids: tuple[str, ...]
    drill_id: str | None = None
    electrical_type: str | None = None

    def __post_init__(self):
        for field in ("id", "component_ref", "pin_number"):
            _text(getattr(self, field), f"pad {field}")
        for field in ("net_id", "drill_id", "electrical_type"):
            _optional_text(getattr(self, field), f"pad {field}")
        _point(self, "position_m")
        if isinstance(self.copper_feature_ids, str):
            raise ValueError("copper_feature_ids must be a sequence of feature IDs")
        refs = _sequence(self.copper_feature_ids, "copper_feature_ids")
        for ref in refs:
            _text(ref, "pad copper feature id")
        if not refs or len(set(refs)) != len(refs):
            raise ValueError("pad must reference nonempty distinct copper features")
        object.__setattr__(self, "copper_feature_ids", refs)

    @property
    def pin_key(self):
        """Electrical identity; multiple physical pad IDs may share this key."""
        return self.component_ref, self.pin_number


@dataclass(frozen=True)
class DrillFeature:
    id: str
    position_m: tuple[float, float]
    diameter_m: float
    plating: str
    start_layer_id: str
    end_layer_id: str
    kind: str

    def __post_init__(self):
        for field in ("id", "start_layer_id", "end_layer_id"):
            _text(getattr(self, field), f"drill {field}")
        _point(self, "position_m")
        _number(self.diameter_m, "diameter_m", positive=True)
        if self.plating not in ("plated", "nonplated", "unknown"):
            raise ValueError("plating must be plated, nonplated or unknown")
        if self.kind not in ("via", "pin", "mechanical"):
            raise ValueError("drill kind must be via, pin or mechanical")
        if self.kind == "via" and self.plating == "nonplated":
            raise ValueError("a via cannot be explicitly nonplated")


@dataclass(frozen=True)
class BoardModel:
    source: SourceInfo
    layers: tuple[CopperLayer, ...]
    nets: tuple[Net, ...] = ()
    features: tuple[CopperFeature, ...] = ()
    pads: tuple[PinPad, ...] = ()
    drills: tuple[DrillFeature, ...] = ()

    def __post_init__(self):
        if not isinstance(self.source, SourceInfo):
            raise ValueError("source must be SourceInfo")
        lookup = {}
        for field, record_type in (("layers", CopperLayer), ("nets", Net),
                                   ("features", CopperFeature), ("pads", PinPad),
                                   ("drills", DrillFeature)):
            records = _sequence(getattr(self, field), field)
            if any(not isinstance(r, record_type) for r in records):
                raise ValueError(f"{field} must contain {record_type.__name__} records")
            by_id = {r.id: r for r in records}
            if len(by_id) != len(records):
                raise ValueError(f"duplicate {field} id")
            object.__setattr__(self, field, records)
            lookup[field] = by_id
        if not self.layers:
            raise ValueError("at least one copper layer is required")
        for a, b in zip(self.layers, self.layers[1:]):
            if b.z_m <= a.z_m or a.z_m+a.thickness_m/2 > b.z_m-b.thickness_m/2:
                raise ValueError("layers must be ordered by increasing Z without thickness overlap")
        order = {layer.id: i for i, layer in enumerate(self.layers)}
        for drill in self.drills:
            if drill.start_layer_id not in order or drill.end_layer_id not in order:
                raise ValueError(f"drill {drill.id}: unknown span layer")
            start, end = order[drill.start_layer_id], order[drill.end_layer_id]
            if start > end or (drill.kind == "via" and start == end):
                raise ValueError(f"drill {drill.id}: invalid ordered layer span")
        for feature in self.features:
            if feature.layer_id not in order or (feature.net_id is not None and feature.net_id not in lookup["nets"]):
                raise ValueError(f"feature {feature.id}: unknown layer or net")
            if feature.drill_id is not None:
                self._check_drill(feature.drill_id, feature.layer_id, order, lookup)
        pin_nets = {}
        for pad in self.pads:
            if pad.pin_key in pin_nets and pin_nets[pad.pin_key] != pad.net_id:
                raise ValueError(f"pad {pad.id}: conflicting nets for electrical pin {pad.pin_key}")
            pin_nets[pad.pin_key] = pad.net_id
            if pad.net_id is not None and pad.net_id not in lookup["nets"]:
                raise ValueError(f"pad {pad.id}: unknown net")
            if pad.drill_id is not None:
                if pad.drill_id not in lookup["drills"] or lookup["drills"][pad.drill_id].kind != "pin":
                    raise ValueError(f"pad {pad.id}: drill must reference a pin hole")
            for ref in pad.copper_feature_ids:
                feature = lookup["features"].get(ref)
                if feature is None or feature.net_id != pad.net_id:
                    raise ValueError(f"pad {pad.id}: missing or net-mismatched copper feature {ref}")
                if feature.drill_id is not None and feature.drill_id != pad.drill_id:
                    raise ValueError(f"pad {pad.id}: inconsistent drill association")
                if pad.drill_id is not None:
                    self._check_drill(pad.drill_id, feature.layer_id, order, lookup)

    @staticmethod
    def _check_drill(drill_id, layer_id, order, lookup):
        drill = lookup["drills"].get(drill_id)
        if drill is None:
            raise ValueError(f"unknown drill reference {drill_id}")
        if not order[drill.start_layer_id] <= order[layer_id] <= order[drill.end_layer_id]:
            raise ValueError(f"copper layer {layer_id} lies outside drill {drill_id} span")
