"""The setup file (setup.py) and ``pdnlab-dc init`` (cli.py).

Against the public arc board: +3.3V on R1.1 and R2.1, GND on R1.2 and R2.2, so R1 can
be a source and R2 a load.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from pdnlab_dc.cli import main
from pdnlab_dc.ipc2581_board import read_ipc2581_board
from pdnlab_dc.setup import (Load, SetupError, Source, bind_setup, parse_setup, read_setup,
                             setup_template)

FIXTURES = Path(__file__).parent / "fixtures"
ARC = FIXTURES / "arc_track" / "arc_track.xml"

GOOD = """
board = "../fixtures/arc_track/arc_track.xml"
plating_um = 25

[[source]]
name = "regulator"
volts = 3.3
power_pins = ["R1.1"]
gnd_pins = ["R1.2"]

[[load]]
name = "MCU"
amps = 0.5
power_pins = ["R2.1"]
gnd_pins = ["R2.2"]
"""


def parse(text, where=None):
    return parse_setup(text, where or (Path(__file__).parent / "setups" / "run.toml"))


def change(old, new):
    assert GOOD.count(old) == 1
    return GOOD.replace(old, new)


@pytest.fixture(scope="module")
def arc():
    return read_ipc2581_board(ARC)


def test_a_good_setup_and_its_defaults():
    setup = parse(GOOD)
    assert setup.board_path.resolve() == ARC.resolve()      # relative to the setup's folder
    assert setup.plating_m == pytest.approx(25e-6)
    (source,), (load,) = setup.sources, setup.loads
    assert (source.name, source.volts, source.power_pins, source.gnd_pins) == \
        ("regulator", 3.3, ("R1.1",), ("R1.2",))
    assert (load.name, load.amps, load.power_pins, load.gnd_pins) == ("MCU", 0.5, ("R2.1",), ("R2.2",))
    assert setup.resistivity_ohm_m == setup.barrel_resistivity_ohm_m == 1.724e-8
    assert (setup.tolerance_v, setup.tolerance_rel) == (pytest.approx(10e-6), pytest.approx(0.01))


def test_options_are_read():
    setup = parse(GOOD + "\n[options]\nresistivity_ohm_m = 1.8e-8\ntolerance_uv = 5\n"
                  "tolerance_percent = 0.5\n")
    assert setup.resistivity_ohm_m == setup.barrel_resistivity_ohm_m == 1.8e-8
    assert (setup.tolerance_v, setup.tolerance_rel) == (pytest.approx(5e-6), pytest.approx(0.005))
    barrel = parse(GOOD + "\n[options]\nbarrel_resistivity_ohm_m = 2e-8\n")
    assert (barrel.resistivity_ohm_m, barrel.barrel_resistivity_ohm_m) == (1.724e-8, 2e-8)


@pytest.mark.parametrize("text, message", [
    (change("plating_um = 25", "plating_um = 25\namp = 1"), r"unknown key 'amp'"),
    (change("amps = 0.5", "amp = 0.5"), r"\[\[load\]\] #1: unknown key 'amp'"),
    (GOOD + "\n[options]\ntolerance = 1\n", r"\[options\]: unknown key 'tolerance'"),
    (change("plating_um = 25\n", ""), r"'plating_um' is required"),
    (change("volts = 3.3\n", ""), r"\[\[source\]\] #1: 'volts' is required"),
    (change("plating_um = 25", "plating_um = 0"), r"plating_um: must be more than zero, got 0"),
    (change("plating_um = 25", "plating_um = -25"), r"plating_um: must be more than zero"),
    (change("plating_um = 25", "plating_um = true"), r"plating_um: must be a number"),
    (change("plating_um = 25", 'plating_um = "25"'), r"plating_um: must be a number"),
    (change("volts = 3.3", "volts = 0"), r"'regulator' volts: must be more than zero"),
    (change("amps = 0.5", "amps = -1"), r"'MCU' amps: must be zero or more"),
    (change('power_pins = ["R2.1"]', "power_pins = []"), r"'MCU' power_pins: must be a non-empty list"),
    (change('power_pins = ["R2.1"]', 'power_pins = ["R2"]'), r"'R2' is not a pin name"),
    (change('power_pins = ["R2.1"]', 'power_pins = ["R2.1", "R2.1"]'), r"a pin is listed twice"),
    (change('power_pins = ["R2.1"]', 'power_pins = ["R1.1"]'),
     r"pin 'R1.1' is used by both 'regulator' and 'MCU'"),
    (change('name = "MCU"', 'name = "regulator"'), r"both named 'regulator'"),
    (change('name = "MCU"', 'name = ""'), r"'name' must be a non-empty text"),
    ("board = \n", r"not valid TOML"),
])
def test_format_errors(text, message):
    with pytest.raises(SetupError, match=message):
        parse(text)


def test_zero_amps_is_allowed():
    assert parse(change("amps = 0.5", "amps = 0")).loads[0].amps == 0


def test_bind_finds_the_nets_from_the_pins(arc):
    bound = bind_setup(parse(GOOD), arc)
    assert bound.gnd_net == "GND"
    (rail,) = bound.rails
    assert (rail.power_net, rail.gnd_net, rail.source.name, [l.name for l in rail.loads]) == \
        ("+3.3V", "GND", "regulator", ["MCU"])


@pytest.mark.parametrize("old, new, message", [
    ('power_pins = ["R2.1"]', 'power_pins = ["R9.1"]', r"'MCU' power_pins: pin 'R9.1' is not on the board"),
    ('power_pins = ["R1.1"]\ngnd_pins = ["R1.2"]', 'power_pins = ["R1.2"]\ngnd_pins = ["R1.1"]',
     r"gnd_pins are on different nets"),
])
def test_bind_errors(arc, old, new, message):
    with pytest.raises(SetupError, match=message):
        bind_setup(parse(change(old, new)), arc)


def test_group_and_gnd_net_errors(arc):
    # The arc board has only 4 pins, so these are built directly, not as text.
    base = parse(GOOD)
    mixed = Source("regulator", 3.3, ("R1.1", "R2.2"), ("R1.2",))
    with pytest.raises(SetupError, match=r"'regulator' power_pins: pins on different nets: "
                                         r"\+3.3V: R1.1; GND: R2.2"):
        bind_setup(replace(base, sources=(mixed,), loads=()), arc)
    # GND pins on two nets needs more distinct pins than the arc board has: thermal board
    # (GND on TP2.1, TP1.1, R1.2; +3V3 on R1.1).
    thermal = read_ipc2581_board(FIXTURES / "thermal_relief" / "thermal_relief.xml")
    source = Source("regulator", 3.3, ("R1.2",), ("TP2.1",))
    off_gnd = Load("MCU", 0.5, ("TP1.1",), ("R1.1",))       # its GND pin is on +3V3
    with pytest.raises(SetupError, match=r"gnd_pins are on different nets: 'regulator' on "
                                         r"'GND'; 'MCU' on '\+3V3'"):
        bind_setup(replace(base, sources=(source,), loads=(off_gnd,)), thermal)


def test_pin_in_both_lists_of_one_group():
    with pytest.raises(SetupError, match=r"pin 'R2.1' is in both power_pins and gnd_pins of 'MCU'"):
        parse(change('gnd_pins = ["R2.2"]', 'gnd_pins = ["R2.1"]'))


def test_two_sources_on_one_power_net(arc):
    base = parse(GOOD)
    a = Source("a", 3.3, ("R1.1",), ("R1.2",))
    b = Source("b", 3.3, ("R2.1",), ("R2.2",))
    with pytest.raises(SetupError, match=r"two sources on the power net '\+3.3V' \('a' and 'b'\); "
                                         "one source per power net"):
        bind_setup(replace(base, sources=(a, b), loads=()), arc)


def two_rail_board(arc):
    """The arc board plus a copy of R1 and R2 as R3 and R4 on a second power net, +5V,
    10 mm below: two rails sharing GND."""
    from shapely.affinity import translate
    from pdnlab_dc.board import Net
    features = {f.id: f for f in arc.features}
    more_f, more_p = [], []
    for p in arc.pads:
        ref = {"R1": "R3", "R2": "R4"}[p.component_ref]
        net = "+5V" if p.net_id == "+3.3V" else p.net_id
        old = features[p.copper_feature_ids[0]]
        fid = old.id.replace(p.component_ref, ref)
        more_f.append(replace(old, id=fid, net_id=net, geometry=translate(old.geometry, 0, 0.01)))
        more_p.append(replace(p, id=p.id.replace(p.component_ref, ref), component_ref=ref,
                              net_id=net, position_m=(p.position_m[0], p.position_m[1] + 0.01),
                              copper_feature_ids=(fid,)))
    return replace(arc, nets=arc.nets + (Net("+5V", "+5V"),),
                   features=arc.features + tuple(more_f), pads=arc.pads + tuple(more_p),
                   component_layers=arc.component_layers + (("R3", "F.Cu"), ("R4", "F.Cu")))


def test_two_rails_sharing_gnd_are_refused_for_now(arc):
    # Solving GND per rail leaves out the other rail's return current (increment G).
    board = two_rail_board(arc)
    base = parse(GOOD)
    one = bind_setup(base, board)                      # one rail on the same board: fine
    assert [r.power_net for r in one.rails] == ["+3.3V"]
    second = (Source("five", 5.0, ("R3.1",), ("R3.2",)), Load("io", 0.2, ("R4.1",), ("R4.2",)))
    two = replace(base, sources=base.sources + second[:1], loads=base.loads + second[1:])
    with pytest.raises(SetupError, match=r"^2 rails share the GND net 'GND' \('\+3.3V', '\+5V'\): "
                                         r"simulating them together is not supported yet "
                                         r"\(planned increment G\).*one setup file per rail"):
        bind_setup(two, board)


def test_the_form_asks_for_one_rail_per_file(arc):
    form = setup_template(arc, "arc.xml")
    assert "One rail (one [[source]] and its [[load]]s) per file for now" in form
    assert "add one [[source]] per power net" not in form


def test_power_pins_on_the_gnd_net(arc):
    base = parse(GOOD)
    on_gnd = Source("g", 1.0, ("R1.2",), ("R2.2",))
    with pytest.raises(SetupError, match=r"'g': power_pins are on the GND net 'GND'"):
        bind_setup(replace(base, sources=(on_gnd,), loads=()), arc)


def test_load_on_a_net_without_source(arc):
    base = parse(GOOD)
    load = Load("orphan", 1.0, ("R2.1",), ("R2.2",))
    # Its source is gone; its power net +3.3V has no source.
    with pytest.raises(SetupError, match=r"'orphan': its power_pins are on '\+3.3V', which has no source"):
        bind_setup(replace(base, sources=(), loads=(load,)), arc)


def test_at_least_one_source_and_load():
    with pytest.raises(SetupError, match=r"at least one \[\[load\]\] is required"):
        parse("load = []\n" + GOOD[:GOOD.index("[[load]]")])


def test_unconnected_pin_is_an_error():
    board = read_ipc2581_board(FIXTURES / "pad_shapes" / "pad_shapes.xml")
    # GND pins both on Net-(Q1-B) (Q1.1, R1.2); the source's power pin TP1.1 has no net.
    text = (GOOD.replace('power_pins = ["R1.1"]\ngnd_pins = ["R1.2"]',
                         'power_pins = ["TP1.1"]\ngnd_pins = ["Q1.1"]')
            .replace('gnd_pins = ["R2.2"]', 'gnd_pins = ["R1.2"]').replace('"R2.1"', '"R1.1"'))
    with pytest.raises(SetupError, match="pin 'TP1.1' is not connected to any net"):
        bind_setup(parse(text), board)


def test_template_lists_nets_and_reads_back_once_filled(arc, tmp_path):
    text = setup_template(arc, "arc_board.xml", min_pins=2)
    assert "# +3.3V (2 pins)\n#   pins: R1.1 R2.1\n#   components: R1 R2" in text
    assert "# GND (2 pins)" in text
    # As written, it is a form: reading it says what to fill in first.
    with pytest.raises(SetupError, match="plating_um: must be more than zero, got 0"):
        parse(text)
    filled = (text.replace("plating_um = 0 ", "plating_um = 25 ")
              .replace('name = ""                         # e.g. "regulator"', 'name = "reg"')
              .replace("volts = 0 ", "volts = 3.3 ")
              .replace('power_pins = []                   # e.g. ["U5.2", "U5.3"] or ["J4.6", "J4.*"]',
                       'power_pins = ["R1.1"]')
              .replace('gnd_pins = []                     # e.g. ["U5.4"]', 'gnd_pins = ["R1.2"]')
              .replace('name = ""                         # e.g. "MCU"', 'name = "load"')
              .replace("amps = 0 ", "amps = 0.5 ")
              .replace("power_pins = []\ngnd_pins = []", 'power_pins = ["R2.1"]\ngnd_pins = ["R2.2"]'))
    (tmp_path / "setup.toml").write_text(filled)
    (tmp_path / "arc_board.xml").write_bytes(ARC.read_bytes())
    setup = read_setup(tmp_path / "setup.toml")
    assert setup.board_path == tmp_path / "arc_board.xml"
    assert bind_setup(setup, arc).rails[0].power_net == "+3.3V"
    # The default lists nets with 3 or more pins: none on this small board.
    assert "(2 nets with fewer than 3 pins are not listed)" in setup_template(arc, "a.xml")


def test_init_command(tmp_path, capsys):
    board = tmp_path / "boards" / "arc_board.xml"
    board.parent.mkdir()
    board.write_bytes(ARC.read_bytes())
    out = tmp_path / "runs" / "setup.toml"
    out.parent.mkdir()
    assert main(["init", str(board), "-o", str(out), "--min-pins", "2"]) == 0
    assert 'board = "../boards/arc_board.xml"' in out.read_text()
    assert main(["init", str(board), "-o", str(out)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert main(["init", str(board), "-o", str(out), "--force"]) == 0
    assert main(["init", str(board)]) == 0
    assert (board.parent / "setup.toml").exists()


def test_init_and_check_protect_memory_and_report_board_errors_in_one_line(tmp_path, capsys,
                                                                           ceiling_calls):
    from pdnlab_dc.memory import default_limit_bytes
    board = tmp_path / "arc_board.xml"
    board.write_bytes(ARC.read_bytes())
    assert main(["init", str(board), "--min-pins", "2"]) == 0
    assert ceiling_calls == [default_limit_bytes()]          # half the RAM, as for run
    # A board the reader refuses: one line naming what is wrong, no traceback.
    bad = tmp_path / "bad.xml"
    bad.write_bytes(ARC.read_bytes().replace(b'revision="C"', b'revision="B"', 1))
    assert main(["init", str(bad), "-o", str(tmp_path / "bad.toml")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("board not usable: ") and "revision" in err and "Traceback" not in err
    # check uses the setup's max_memory_gb, and reports a refused board the same way.
    good = GOOD.replace(f'board = "../fixtures/arc_track/{ARC.name}"', 'board = "arc_board.xml"')
    (tmp_path / "limited.toml").write_text(good + "\n[options]\nmax_memory_gb = 3\n")
    assert main(["check", str(tmp_path / "limited.toml")]) == 0
    assert ceiling_calls[-1] == 3 * 1024**3
    (tmp_path / "bad_board.toml").write_text(good.replace('"arc_board.xml"', '"bad.xml"'))
    assert main(["check", str(tmp_path / "bad_board.toml")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("setup not usable: ") and "revision" in err and "Traceback" not in err


def test_check_command(tmp_path, capsys):
    (tmp_path / "board.xml").write_bytes(ARC.read_bytes())
    good = GOOD.replace(f'board = "../fixtures/arc_track/{ARC.name}"', 'board = "board.xml"')
    (tmp_path / "setup.toml").write_text(good)
    assert main(["check", str(tmp_path / "setup.toml")]) == 0
    out = capsys.readouterr().out
    assert "GND net 'GND'" in out and "rail '+3.3V': source 'regulator' 3.3 V on R1.1" in out
    assert "load 'MCU' 0.5 A on R2.1 (GND R2.2)" in out
    (tmp_path / "bad.toml").write_text(good.replace('"R2.1"', '"R9.1"'))
    assert main(["check", str(tmp_path / "bad.toml")]) == 1
    assert "pin 'R9.1' is not on the board" in capsys.readouterr().err


# ---- Pin wildcards, the kicad_pcb key and the export from KiCad ----

THERMAL = FIXTURES / "thermal_relief" / "thermal_relief.xml"


@pytest.fixture(scope="module")
def with_u1():
    """The thermal board plus a U1 with U1.1 and U1.2 on GND and U1.3 on +3V3."""
    from shapely.geometry import box
    from pdnlab_dc.board import CopperFeature, PinPad
    board = read_ipc2581_board(THERMAL)
    pins = {"1": "GND", "2": "GND", "3": "+3V3"}
    features = tuple(CopperFeature(f"pad:F.Cu:U1.{n}", net, "F.Cu", "pad",
                                   box(0.19 + 0.001 * i, 0.09, 0.1905 + 0.001 * i, 0.0905))
                     for i, (n, net) in enumerate(pins.items()))
    pads = tuple(PinPad(f"pin:U1.{n}", "U1", n, net, (0.19 + 0.001 * i, 0.09), (f.id,), None)
                 for i, ((n, net), f) in enumerate(zip(pins.items(), features)))
    return replace(board, features=board.features + features, pads=board.pads + pads,
                   component_layers=board.component_layers + (("U1", "F.Cu"),))


WILD = """
board = "x.xml"
plating_um = 25
[[source]]
name = "reg"
volts = 3.3
power_pins = ["R1.1"]
gnd_pins = ["TP2.1"]
[[load]]
name = "MCU"
amps = 1
power_pins = ["U1.3", "U1.*"]
gnd_pins = ["U1.1", "U1.*"]
"""


def test_wildcard_adds_the_components_other_pins_on_the_lists_net(with_u1):
    bound = bind_setup(parse(WILD), with_u1)
    (load,) = bound.rails[0].loads
    assert load.gnd_pins == ("U1.1", "U1.2")          # U1.3 is on +3V3, not GND
    assert load.power_pins == ("U1.3",)               # nothing more of U1 on +3V3
    assert set(bound.expanded) == {("MCU", "power_pins", "U1.*", 0), ("MCU", "gnd_pins", "U1.*", 1)}


@pytest.mark.parametrize("old, new, message", [
    ('gnd_pins = ["U1.1", "U1.*"]', 'gnd_pins = ["U1.*"]', r"'MCU' gnd_pins: a list with \"X.\*\" needs "
                                                           r"at least one named pin"),
    ('gnd_pins = ["U1.1", "U1.*"]', 'gnd_pins = ["U1.1", "U7.*"]', r"no component 'U7' on the board"),
    ('gnd_pins = ["TP2.1"]', 'gnd_pins = ["TP2.1", "U1.*"]',
     r"pin 'U1.1' is used by both 'reg' and 'MCU' \(after expanding"),
])
def test_wildcard_errors(with_u1, old, new, message):
    assert WILD.count(old) == 1
    with pytest.raises(SetupError, match=message):
        bind_setup(parse(WILD.replace(old, new)), with_u1)


def test_kicad_pcb_key(tmp_path):
    setup = parse(GOOD.replace("plating_um = 25", 'kicad_pcb = "../brd/x.kicad_pcb"\nplating_um = 25'),
                  tmp_path / "pdnlab" / "setup.toml")
    assert setup.kicad_pcb_path == tmp_path / "brd" / "x.kicad_pcb"       # normalised
    assert parse(GOOD).kicad_pcb_path is None
    with pytest.raises(SetupError, match=r"'kicad_pcb' must name a .kicad_pcb file"):
        parse(GOOD.replace("plating_um = 25", 'kicad_pcb = "x.xml"\nplating_um = 25'))


@pytest.fixture
def fake_kicad(tmp_path, monkeypatch):
    """A stand-in kicad-cli: writes the arc board's export to --output, counts its calls."""
    log = tmp_path / "kicad-calls.txt"
    script = tmp_path / "kicad-cli"
    script.write_text(f"""#!/bin/sh
echo "$@" >> "{log}"
while [ $# -gt 0 ]; do
  if [ "$1" = "--output" ]; then out="$2"; fi
  shift
done
cp "{ARC}" "$out"
""")
    script.chmod(0o755)
    monkeypatch.setenv("PDNLAB_KICAD_CLI", str(script))
    return log


def test_export_and_refresh_when_the_board_is_newer(tmp_path, fake_kicad):
    import os
    from pdnlab_dc.setup import refresh_export
    pcb = tmp_path / "x.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    text = GOOD.replace(f'board = "../fixtures/arc_track/{ARC.name}"',
                        'kicad_pcb = "../x.kicad_pcb"\nboard = "x.xml"')
    (tmp_path / "pdnlab").mkdir()
    setup = parse(text, tmp_path / "pdnlab" / "setup.toml")
    assert refresh_export(setup) is True                      # no export yet: made
    assert setup.board_path.read_bytes() == ARC.read_bytes()
    calls = fake_kicad.read_text().splitlines()
    assert calls == [f"pcb export ipc2581 --version C --units mm --output {setup.board_path} {pcb}"]
    assert refresh_export(setup) is False                     # export newer: kept
    later = setup.board_path.stat().st_mtime + 10
    os.utime(pcb, (later, later))                             # board edited after the export
    assert refresh_export(setup) is True
    assert len(fake_kicad.read_text().splitlines()) == 2


def test_init_from_a_kicad_board_and_check_re_exports(tmp_path, fake_kicad, capsys):
    import os
    project = tmp_path / "my project"
    project.mkdir()
    pcb = project / "brd.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    assert main(["init", str(pcb), "--min-pins", "2"]) == 0
    folder = project / "pdnlab"
    assert sorted(p.name for p in folder.iterdir()) == ["brd.xml", "setup.toml"]
    text = (folder / "setup.toml").read_text()
    assert 'kicad_pcb = "../brd.kicad_pcb"' in text and 'board = "brd.xml"' in text
    filled = GOOD.replace(f'board = "../fixtures/arc_track/{ARC.name}"',
                          'kicad_pcb = "../brd.kicad_pcb"\nboard = "brd.xml"')
    (folder / "setup.toml").write_text(filled)
    later = (folder / "brd.xml").stat().st_mtime + 10
    os.utime(pcb, (later, later))
    capsys.readouterr()
    assert main(["check", str(folder / "setup.toml")]) == 0
    assert "re-exported" in capsys.readouterr().out


def test_kicad_cli_missing_or_failing(tmp_path, monkeypatch):
    from pdnlab_dc.kicad import KicadError, export_ipc2581
    pcb = tmp_path / "x.kicad_pcb"
    pcb.write_text("(kicad_pcb)")
    monkeypatch.delenv("PDNLAB_KICAD_CLI", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))                 # no kicad-cli there
    with pytest.raises(KicadError, match="kicad-cli not found"):
        export_ipc2581(pcb, tmp_path / "x.xml")
    failing = tmp_path / "bad-cli"
    failing.write_text("#!/bin/sh\necho 'cannot open board' >&2\nexit 3\n")
    failing.chmod(0o755)
    monkeypatch.setenv("PDNLAB_KICAD_CLI", str(failing))
    with pytest.raises(KicadError, match=r"export failed \(exit 3\):\ncannot open board"):
        export_ipc2581(pcb, tmp_path / "x.xml")
    with pytest.raises(KicadError, match="no such KiCad board"):
        export_ipc2581(tmp_path / "missing.kicad_pcb", tmp_path / "x.xml")
