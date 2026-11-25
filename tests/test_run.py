"""Solving a setup (run.simulate) and writing its results.

The exact case: one layer, a power strip and a GND strip, each 10 mm x 1 mm, with an
SMD pad over the first and the last millimetre. Current through a uniform strip is
represented exactly by linear triangles, so the load must see 3.3 V - I (R_power +
R_gnd) with R = rho 8 mm / (1 mm t) to roundoff, the GND rise must be positive, and
every pin must carry +-I. Needs a gmsh executable (skipped without one).
"""

import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

from pdnlab_dc.board import CopperFeature, PinPad
from pdnlab_dc.copper_cleanup import clean_copper
from pdnlab_dc.ipc2581_board import read_ipc2581_board
from pdnlab_dc.run import RefinementError, simulate
from pdnlab_dc.setup import bind_setup, parse_setup

pytestmark = pytest.mark.skipif(shutil.which("gmsh") is None, reason="external Gmsh not installed")

THERMAL = Path(__file__).parent / "fixtures" / "thermal_relief" / "thermal_relief.xml"
MM = 1e-3
STRIPS = """
board = "x.xml"
plating_um = 25
[[source]]
name = "supply"
volts = 3.3
power_pins = ["S1.1"]
gnd_pins = ["S1.2"]
[[load]]
name = "chip"
amps = 2
power_pins = ["L1.1"]
gnd_pins = ["L1.2"]
"""


def strips_board():
    base = read_ipc2581_board(THERMAL)
    x0, y0 = 0.19, 0.08
    features, pads = [], []
    for net, dy, pin in (("+3V3", 0, "1"), ("GND", 3 * MM, "2")):
        y = y0 + dy
        features.append(CopperFeature(f"track:F.Cu:{net}", net, "F.Cu", "track",
                                      box(x0, y, x0 + 10 * MM, y + MM)))
        for comp, x in (("S1", x0), ("L1", x0 + 9 * MM)):
            fid = f"pad:F.Cu:{comp}.{pin}"
            features.append(CopperFeature(fid, net, "F.Cu", "pad", box(x, y, x + MM, y + MM)))
            pads.append(PinPad(f"pin:{comp}.{pin}", comp, pin, net, (x + MM / 2, y + MM / 2),
                               (fid,), None))
    return replace(base, features=tuple(features), pads=tuple(pads), drills=(),
                   component_layers=(("L1", "F.Cu"), ("S1", "F.Cu")))


@pytest.fixture(scope="module")
def strips():
    board = strips_board()
    setup = parse_setup(STRIPS, Path("/tmp/strips/setup.toml"))
    bound = bind_setup(setup, board)
    return board, bound, simulate(bound, board, clean_copper(board, setup.plating_m))


def test_load_voltage_against_the_exact_answer(strips):
    board, bound, run = strips
    (rail,) = run.rails
    (load,) = rail.loads
    t = next(L for L in board.layers if L.id == "F.Cu").thickness_m
    r_strip = 1.724e-8 * 8 * MM / (1 * MM * t)
    assert load.v_power == pytest.approx(3.3 - 2 * r_strip, rel=1e-9)
    assert load.v_gnd_rise == pytest.approx(2 * r_strip, rel=1e-9)      # positive: a rise
    assert load.v_seen == pytest.approx(3.3 - 4 * r_strip, rel=1e-9)
    assert load.drop == pytest.approx(4 * r_strip, rel=1e-9)
    # Exact at every scale: the first halving changes nothing, so it stops at 0.5.
    assert rail.scale == 0.5 and rail.last_change_v["chip"] < 1e-12
    assert rail.power.checks["passed"] and rail.gnd.checks["passed"]


def test_pin_currents_and_current_density(strips):
    board, _, run = strips
    (rail,) = run.rails
    current = {(p.net, p.pin): p.current_a for p in rail.power.pins + rail.gnd.pins}
    assert current == pytest.approx({("+3V3", "S1.1"): 2.0, ("+3V3", "L1.1"): -2.0,
                                     ("GND", "L1.2"): 2.0, ("GND", "S1.2"): -2.0}, abs=1e-9)
    t = next(L for L in board.layers if L.id == "F.Cu").thickness_m
    uniform = 2.0 / (1 * MM * t) * 1e-6                                 # A/mm² in the strip
    for solve in (rail.power, rail.gnd):
        (f,) = solve.fields
        centre = f.nodes[f.triangles].mean(axis=1)
        between = (centre[:, 0] > 0.19 + 2 * MM) & (centre[:, 0] < 0.19 + 8 * MM)
        jx, jy = f.current_density[between].T
        assert np.allclose(np.abs(jx), uniform, rtol=1e-6) and np.allclose(jy, 0, atol=1e-6 * uniform)
        (spot,) = solve.hot_spots
        assert spot.averaged_a_mm2 <= spot.peak_a_mm2 * (1 + 1e-9)
        # Losses: I^2 R on the strip.
        r_strip = 1.724e-8 * 8 * MM / (1 * MM * t)
        assert f.loss_w == pytest.approx(4 * r_strip, rel=1e-6)
    # Power current flows from source to load (+x), GND current back (-x).
    # Between the pads (inside them the voltage is one value and the density zero).
    (fp,), (fg,) = rail.power.fields, rail.gnd.fields
    def middle(f):
        x = f.nodes[f.triangles].mean(axis=1)[:, 0]
        return f.current_density[(x > 0.19 + 2 * MM) & (x < 0.19 + 8 * MM), 0]
    assert middle(fp).min() > 0 > middle(fg).max()


def test_refinement_error_when_it_does_not_settle(strips):
    board, bound, _ = strips
    # A tolerance below zero can never be met (a change is at least 0), so the check must
    # raise after one refinement. Zero was not enough: on an exact case the change can be
    # exactly 0 (it was on CI once), which settles. The setup file refuses negative values;
    # the test sets one directly.
    tight = replace(bound, setup=replace(bound.setup, tolerance_v=-1e-12, tolerance_rel=0.0))
    with pytest.raises(RefinementError, match=r"rail '\+3V3': the voltage at load 'chip' still changed"):
        simulate(tight, board, clean_copper(board, 25e-6), max_refinements=1)


def test_group_pin_shares_add_up_with_a_hole_inside_a_pad():
    # Thermal board GND: source TP2.1 (SMD); load group TP1.1 (through-hole, its hole H3
    # inside its F.Cu pad, so part of its current goes down its own barrel) and R1.2.
    from types import SimpleNamespace
    from pdnlab_dc.run import _solve_net
    from pdnlab_dc.setup import Load, Source
    board = read_ipc2581_board(THERMAL)
    clean = clean_copper(board, 25e-6)
    setup = SimpleNamespace(resistivity_ohm_m=1.724e-8, barrel_resistivity_ohm_m=1.724e-8)
    source = Source("tp2", 1.0, ("TP2.1",), ())
    load = Load("group", 3.0, ("TP1.1", "R1.2"), ())
    solve = _solve_net(board, clean, "GND", "power", source, (load,), setup, 1.0)
    current = {p.pin: p.current_a for p in solve.pins}
    assert current["TP2.1"] == pytest.approx(3.0, rel=1e-9)
    assert current["TP1.1"] + current["R1.2"] == pytest.approx(-3.0, rel=1e-9)
    assert current["TP1.1"] < 0 and current["R1.2"] < 0
    # The group is one equipotential: one voltage.
    assert set(solve.group_voltage_v) == {"tp2", "group"}
    assert solve.vias and solve.vias[0].current_a >= solve.vias[-1].current_a


def test_results_are_written(strips, tmp_path):
    import csv
    import meshio
    from pdnlab_dc.results import write_results
    _, _, run = strips
    written = write_results(run, tmp_path / "out", "strips test")
    names = sorted(p.name for p in written)
    assert names == sorted(["summary.txt", "loads.csv", "pins.csv", "vias.csv", "timing.csv",
                            "+3V3_F.Cu.vtu", "+3V3_F.Cu_voltage.png", "+3V3_F.Cu_current.png",
                            "GND_F.Cu.vtu", "GND_F.Cu_voltage.png", "GND_F.Cu_current.png"])
    summary = (tmp_path / "out" / "summary.txt").read_text()
    assert "Rail +3V3 (GND GND), source 'supply' at 3.3 V" in summary and "settled" in summary
    (row,) = list(csv.DictReader((tmp_path / "out" / "loads.csv").open()))
    assert float(row["v_seen_V"]) == pytest.approx(run.rails[0].loads[0].v_seen, abs=1e-9)
    pins = list(csv.DictReader((tmp_path / "out" / "pins.csv").open()))
    assert {(r["pin"], r["share_percent"]) for r in pins} == {
        ("S1.1", "100.000"), ("L1.1", "100.000"), ("S1.2", "100.000"), ("L1.2", "100.000")}
    mesh = meshio.read(tmp_path / "out" / "+3V3_F.Cu.vtu")
    assert mesh.point_data["voltage_V"].max() == pytest.approx(3.3)
    assert mesh.point_data["map_mV"].min() == pytest.approx(0, abs=1e-9)    # drop 0 at the source
    for png in (p for p in written if p.suffix == ".png"):
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and png.stat().st_size > 10000


@pytest.fixture
def no_ceiling(monkeypatch):
    """The command applies a hard memory ceiling to its own process; in a test that process
    is pytest itself, so the ceiling is replaced (it is tested in a child process)."""
    import pdnlab_dc.memory
    monkeypatch.setattr(pdnlab_dc.memory, "apply_ceiling", lambda limit: "ceiling skipped in tests")


def test_run_command_on_the_public_arc_board(tmp_path, capsys, no_ceiling):
    from pdnlab_dc.cli import main
    arc = Path(__file__).parent / "fixtures" / "arc_track" / "arc_track.xml"
    (tmp_path / "arc.xml").write_bytes(arc.read_bytes())
    setup = tmp_path / "setup.toml"
    setup.write_text(STRIPS.replace('"x.xml"', '"arc.xml"').replace("S1.", "R1.").replace("L1.", "R2."))
    assert main(["run", str(setup)]) == 0
    out = capsys.readouterr().out
    assert "Rail +3.3V (GND GND)" in out and "results in" in out
    assert "memory limit" in out and "ceiling skipped in tests" in out
    folder = tmp_path / "results" / "setup"
    assert (folder / "summary.txt").exists() and (folder / "+3.3V_F.Cu_voltage.png").exists()
    assert main(["run", str(setup)]) == 1
    assert "already exists; use --force" in capsys.readouterr().err
    assert main(["run", str(setup), "--force"]) == 0
    out = capsys.readouterr().out
    assert "written in" in out
    timing = (folder / "timing.csv").read_text().splitlines()
    assert [r.split(",")[5] for r in timing[1:4]] == ["read board", "cleanup", "mesh"]


STAGES = ["mesh", "network", "prepare", "solve", "fields"]


def test_every_round_of_every_net_is_timed_stage_by_stage(strips, tmp_path):
    import csv
    from pdnlab_dc.results import write_results
    _, _, run = strips
    (rail,) = run.rails
    # Every round, both nets, each with the five stages in order (fields are not kept for
    # earlier rounds, only their timing).
    assert [(r.scale, r.role) for r in rail.rounds] == [
        (s, role) for s, _ in rail.history for role in ("power", "gnd")]
    for r in rail.rounds:
        assert [st.name for st in r.stages] == STAGES
        assert all(st.seconds >= 0 and st.cpu_seconds >= 0 for st in r.stages)
        assert 0 < r.factorization_s <= r.stages[2].seconds        # inside "prepare"
        assert all(st.peak_bytes >= st.rss_bytes > 0 for st in r.stages), r.stages
        assert r.stages[0].child_peak_bytes > 0                     # Gmsh ran in "mesh"
        # Gmsh's processor time counts: meshing is mostly Gmsh, a child process.
        assert r.stages[0].cpu_seconds > 0.3 * r.stages[0].seconds
        assert r.triangles == {"power": rail.power, "gnd": rail.gnd}[r.role].triangles \
            or r.scale != rail.scale
    last = [r for r in rail.rounds if r.scale == rail.scale]
    assert sum(st.seconds for st in last[0].stages) <= rail.power.seconds
    written = write_results(run, tmp_path / "out", "timing test")
    summary = (tmp_path / "out" / "summary.txt").read_text()
    assert "Time and memory by stage" in summary
    assert "+3V3 scale 1: mesh" in summary and "GND (+3V3) scale 1: prepare" in summary
    assert "of which LU factorisation" in summary
    rows = list(csv.DictReader((tmp_path / "out" / "timing.csv").open()))
    assert len(rows) == len(rail.rounds) * (len(STAGES) + 1)
    assert {r["stage"] for r in rows} == set(STAGES) | {"factorisation (in prepare)"}
    first = rows[0]
    assert (first["rail"], first["net"], first["role"], first["scale"], first["stage"]) == \
        ("+3V3", "+3V3", "power", "1", "mesh")
    assert int(first["gmsh_peak_bytes"]) > 0 and float(first["wall_s"]) > 0


def test_a_stage_that_waits_shows_wall_time_without_cpu_time():
    import time
    from pdnlab_dc.timing import Stopwatch
    watch = Stopwatch()
    time.sleep(0.3)
    waited = watch.lap("wait")
    total = 0
    for i in range(3_000_000):
        total += i
    worked = watch.lap("work")
    assert waited.seconds >= 0.3 and waited.cpu_seconds < 0.1
    assert worked.cpu_seconds > 0.5 * worked.seconds > 0
    assert [s.name for s in watch.stages] == ["wait", "work"]


def test_a_group_across_two_layers_acts_as_one_contact():
    # The power strip on F.Cu and an identical one on B.Cu, the source's and the load's
    # power pins grouped across both layers: two resistors in parallel. The source is held
    # at its F.Cu terminal, so the B.Cu strip's current passes both 1 µOhm links: the B.Cu
    # path is R + 2 µOhm (1 µV at 2 A, as the summary states).
    board = strips_board()
    x0, y0 = 0.19, 0.08
    features, pads = list(board.features), list(board.pads)
    features.append(CopperFeature("track:B.Cu:+3V3", "+3V3", "B.Cu", "track",
                                  box(x0, y0, x0 + 10 * MM, y0 + MM)))
    for comp, x in (("S1", x0), ("L1", x0 + 9 * MM)):
        fid = f"pad:B.Cu:{comp}.3"
        features.append(CopperFeature(fid, "+3V3", "B.Cu", "pad", box(x, y0, x + MM, y0 + MM)))
        pads.append(PinPad(f"pin:{comp}.3", comp, "3", "+3V3", (x + MM / 2, y0 + MM / 2), (fid,), None))
    board = replace(board, features=tuple(features), pads=tuple(pads))
    text = STRIPS.replace('power_pins = ["S1.1"]', 'power_pins = ["S1.1", "S1.3"]').replace(
        'power_pins = ["L1.1"]', 'power_pins = ["L1.1", "L1.3"]')
    bound = bind_setup(parse_setup(text, Path("/tmp/strips/setup.toml")), board)
    run = simulate(bound, board, clean_copper(board, 25e-6))
    (rail,) = run.rails
    (load,) = rail.loads
    t_f = next(L for L in board.layers if L.id == "F.Cu").thickness_m
    t_b = next(L for L in board.layers if L.id == "B.Cu").thickness_m
    r_f, r_b = (1.724e-8 * 8 * MM / (MM * t) for t in (t_f, t_b))
    from pdnlab_dc.board_network import LINK_OHM
    r_b_path = r_b + 2 * LINK_OHM
    parallel = r_f * r_b_path / (r_f + r_b_path)
    assert load.v_power == pytest.approx(3.3 - 2 * parallel, rel=1e-9)
    assert 0.9e-6 < 2 * (parallel - r_f * r_b / (r_f + r_b)) < 1.1e-6     # the links: 1 µV
    current = {p.pin: p.current_a for p in rail.power.pins}
    share = 2 * r_b_path / (r_f + r_b_path)                             # into the F.Cu strip
    assert current == pytest.approx({"S1.1": share, "S1.3": 2 - share,
                                     "L1.1": -share, "L1.3": -(2 - share)}, abs=1e-6)
    assert {p.group for p in rail.power.pins} == {"supply", "chip"}


def test_run_reports_a_problem_without_a_traceback(tmp_path, capsys, no_ceiling):
    from pdnlab_dc.cli import main
    arc = Path(__file__).parent / "fixtures" / "arc_track" / "arc_track.xml"
    (tmp_path / "arc.xml").write_bytes(arc.read_bytes())
    setup = tmp_path / "setup.toml"
    setup.write_text(STRIPS.replace('"x.xml"', '"arc.xml"').replace("S1.", "R1.").replace("L1.", "R9."))
    assert main(["run", str(setup)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("not solved: ") and "Traceback" not in err
    assert "is not on the board" in err and "'R9." in err


def test_a_round_that_would_not_fit_in_memory_is_not_started(strips, tmp_path, capsys):
    # A limit far below what the next round needs: the scale-1 result is reported, marked.
    from pdnlab_dc.results import write_results
    board, bound, _ = strips
    lines = []
    run = simulate(bound, board, clean_copper(board, 25e-6), memory_limit_bytes=1, log=lines.append)
    (rail,) = run.rails
    assert not rail.settled and rail.scale == 1.0 and rail.last_change_v == {}
    assert "the refinement at scale 0.5 would need about" in rail.unsettled_reason
    assert any("stopped before scale 0.5" in line for line in lines)
    write_results(run, tmp_path / "out", "memory test")
    summary = (tmp_path / "out" / "summary.txt").read_text()
    assert "NOT SETTLED: the refinement at scale 0.5" in summary and "no refinement check done" in summary
    assert "settled\n" not in summary.split("Notes")[0].replace("not settled", "")
    assert (tmp_path / "out" / "loads.csv").read_text().rstrip().endswith(",no")


def test_hard_memory_ceiling_turns_an_allocation_into_a_clean_error(tmp_path):
    # In child processes (the ceiling must not limit the test run itself). A control child
    # without the ceiling must manage a 3 GB allocation; the child with a 256 MB limit
    # (2.25 GB with the margin) must get MemoryError for the same allocation.
    import subprocess
    import sys
    allocate = ("import numpy as np\n"
                "try:\n"
                "    a = np.ones(3 * 1024**3, dtype=np.uint8); print('ALLOCATED', int(a[-1]))\n"
                "except MemoryError:\n"
                "    print('MEMORYERROR')\n")
    control = subprocess.run([sys.executable, "-c", allocate], capture_output=True, text=True,
                             timeout=120)
    if "ALLOCATED" not in control.stdout:
        pytest.skip("this machine cannot allocate 3 GB even without a ceiling")
    limited = ("import resource\nfrom pdnlab_dc.memory import apply_ceiling, CEILING_MARGIN_BYTES\n"
               "print(apply_ceiling(256 * 1024**2))\n"
               "print('LIMIT', resource.getrlimit(resource.RLIMIT_AS)[0])\n" + allocate)
    done = subprocess.run([sys.executable, "-c", limited], capture_output=True, text=True, timeout=120)
    if "no hard memory ceiling" in done.stdout:
        pytest.skip("this system has no address-space limit")
    limit_line = next(l for l in done.stdout.splitlines() if l.startswith("LIMIT"))
    assert int(limit_line.split()[1]) == 256 * 1024**2 + 2 * 1024**3
    assert done.stdout.strip().endswith("MEMORYERROR")


def test_max_memory_option():
    setup = parse_setup(STRIPS + "\n[options]\nmax_memory_gb = 6\n", Path("/tmp/s/setup.toml"))
    assert setup.max_memory_bytes == 6 * 1024**3
    assert parse_setup(STRIPS, Path("/tmp/s/setup.toml")).max_memory_bytes is None


def test_run_command_exits_2_when_not_settled(tmp_path, capsys, no_ceiling):
    from pdnlab_dc.cli import main
    arc = Path(__file__).parent / "fixtures" / "arc_track" / "arc_track.xml"
    (tmp_path / "arc.xml").write_bytes(arc.read_bytes())
    setup = tmp_path / "setup.toml"
    setup.write_text(STRIPS.replace('"x.xml"', '"arc.xml"').replace("S1.", "R1.").replace("L1.", "R2.")
                     + "\n[options]\nmax_memory_gb = 0.000001\n")
    assert main(["run", str(setup)]) == 2
    assert "NOT SETTLED" in capsys.readouterr().err
    assert (tmp_path / "results" / "setup" / "summary.txt").exists()      # written all the same


def test_memory_prediction_has_a_margin_and_is_reported(strips, tmp_path):
    from pdnlab_dc.results import write_results
    from pdnlab_dc.run import MEMORY_SAFETY, _predicted_bytes
    assert MEMORY_SAFETY >= 1.5
    assert _predicted_bytes(1.0e9, 2.0e9, 4.0) == pytest.approx(1.0e9 + 1.0e9 * 4.0 * MEMORY_SAFETY)
    _, _, run = strips
    (rail,) = run.rails
    (s1, none, p1), (s2, need, p2) = rail.memory
    assert (s1, none, s2) == (1.0, None, 0.5) and p1 > 0 and need > 0 and p2 > 0
    write_results(run, tmp_path / "out", "memory report")
    summary = (tmp_path / "out" / "summary.txt").read_text()
    assert "memory at scale 1      peak" in summary and "predicted" in summary
