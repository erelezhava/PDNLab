"""Tracks read from a real KiCad 10 export against the same board's .kicad_pcb file.

tests/fixtures/arc_track is a small public board drawn by Erekle (2026-11-16): two
resistors on +3.3V and GND, joined by straight and arc tracks on F.Cu, arcs bending
both ways. arc_track.xml was exported with
``kicad-cli pcb export ipc2581 --version C --units mm --precision 6``.

The .kicad_pcb gives each arc as start, mid and end points in KiCad's frame
(millimetres, Y down); the export gives start, end, centre and a direction flag in
IPC's frame (Y up). Reading the KiCad file independently checks the direction flag,
the Y reversal, the unit scale and the widths on real exporter output.
"""

import math
import re
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point

from pdnlab_dc.ipc2581_copper import read_ipc2581_copper

FIXTURE = Path(__file__).parent / "fixtures" / "arc_track"
NUMBER = r"(-?\d+(?:\.\d+)?)"


def kicad_tracks():
    """Every copper segment and arc of the .kicad_pcb, in metres, KiCad frame (Y down)."""
    text = (FIXTURE / "arc_brd.kicad_pcb").read_text()
    tracks = []
    for kind, body in re.findall(r"\n\t\((segment|arc)\n(.*?)\n\t\)", text, re.S):
        point = lambda name: tuple(float(v) * 1e-3 for v in
                                   re.search(rf"\({name} {NUMBER} {NUMBER}\)", body).groups())
        track = {"kind": kind, "start": point("start"), "end": point("end"),
                 "width": float(re.search(rf"\(width {NUMBER}\)", body).group(1)) * 1e-3,
                 "layer": re.search(r'\(layer "([^"]+)"\)', body).group(1),
                 "net": re.search(r'\(net "([^"]*)"\)', body).group(1)}
        if kind == "arc":
            track["mid"] = point("mid")
        tracks.append(track)
    return tracks


def circle_through(a, b, c):
    """Centre and radius of the circle through three points."""
    (ax, ay), (bx, by), (cx, cy) = a, b, c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    return (ux, uy), math.dist((ux, uy), a)


@pytest.fixture(scope="module")
def pairs():
    """Each KiCad track with the exported track feature that starts and ends at its ends."""
    exported = read_ipc2581_copper(FIXTURE / "arc_track.xml")
    result = []
    for track in kicad_tracks():
        ends = [t for t in exported.tracks if t.geometry.contains(Point(track["start"]))
                and t.geometry.contains(Point(track["end"]))
                and "/" + ("Arc" if track["kind"] == "arc" else "Line") + "[" in t.source_ref]
        result.append((track, ends))
    return exported, result


def test_counts_match_kicad(pairs):
    exported, matched = pairs
    kicad = kicad_tracks()
    assert len(kicad) == 13 and len(exported.tracks) == 13
    assert sorted((t["net"], t["kind"]) for t in kicad) == sorted(
        (t.net_id, "arc" if "/Arc[" in t.source_ref else "segment") for t in exported.tracks)
    assert {t.layer_id for t in exported.tracks} == {"F.Cu"} and exported.fills == ()
    assert all(ends for _, ends in matched)


def test_every_arc_covers_kicad_mid_point_and_not_the_other_way_round(pairs):
    _, matched = pairs
    arcs = [(t, ends) for t, ends in matched if t["kind"] == "arc"]
    assert len(arcs) == 6
    for track, ends in arcs:
        centre, radius = circle_through(track["start"], track["mid"], track["end"])
        opposite = (2 * centre[0] - track["mid"][0], 2 * centre[1] - track["mid"][1])
        hits = [t for t in ends if t.geometry.contains(Point(track["mid"]))]
        assert len(hits) == 1, track
        feature = hits[0]
        assert feature.net_id == track["net"]
        assert not feature.geometry.intersects(Point(opposite))
        # Every vertex on the band edge or a cap: within 1 µm of half the width.
        half = track["width"] / 2
        for p in feature.geometry.exterior.coords:
            band = abs(math.dist(p, centre) - radius)
            cap = min(math.dist(p, track["start"]), math.dist(p, track["end"]))
            assert min(abs(band - half), abs(cap - half)) <= 1e-6


def test_every_segment_has_kicad_width_and_ends(pairs):
    _, matched = pairs
    segments = [(t, ends) for t, ends in matched if t["kind"] == "segment"]
    assert len(segments) == 7
    for track, ends in segments:
        centre_line = LineString([track["start"], track["end"]])
        fits = [t for t in ends if all(
            centre_line.distance(Point(p)) == pytest.approx(track["width"] / 2, abs=1e-9)
            for p in t.geometry.exterior.coords)]
        assert len(fits) == 1, track
        assert fits[0].net_id == track["net"]
        # Slightly less than exact; up to ~0.5% for a near-dot, whose area is almost all cap
        # (an 18-step cap circle has sin(10°)/(10° in radians) = 0.9949 of the true area).
        exact = centre_line.length * track["width"] + math.pi * (track["width"] / 2) ** 2
        assert 0.99 * exact < fits[0].geometry.area < exact
