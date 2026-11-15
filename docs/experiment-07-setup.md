# Experiment 7: external reference tools

**Status (2026-11-15): padne is installed on the maintainer's machine and was
run on one net of a local test board; see the 2026-11-15 entry in
[DECISIONS.md](DECISIONS.md) and the section "padne on a local test board"
below. The StickHub benchmark has not been run with either tool. KiPI is not
installed.**

The text below was written on 14 November in a development terminal that could
not resolve `github.com`. The StickHub part of the experiment remains pending
until the tools have been tried on that board and their settings and results
or specific tool failures have been recorded.

## Install separately from PDNLab

Run these commands from the PDNLab repository in your normal terminal. These
are setup instructions, not commands that have already succeeded here.

For padne, use the author's all-in-one Linux executable. Its runtime bundles
dependencies, including KiCad. This avoids building its C++ dependencies in
PDNLab's environment.

```sh
mkdir -p reference-tools
curl --fail --location https://atx.github.io/padne/padne-linux-x64 -o reference-tools/padne-linux-x64
chmod +x reference-tools/padne-linux-x64
reference-tools/padne-linux-x64 --version
reference-tools/padne-linux-x64 solve --help
```

The binary URL is mutable. The preflight records its SHA-256 and reported
version. Check that its `solve --help` includes `--directives` and
`--no-schematic-directives`; a binary older than the documented source may not
support them.

For KiPI, use the official repository, pinned to release `v1.0.1`, in a separate
Python 3.12 environment. The installed PDNLab Python is 3.14; KiPI's documented
standalone setup recommends 3.10–3.13. These commands download another Python
and the standalone GUI/solver dependencies; they can take several minutes.

```sh
git clone --branch v1.0.1 --depth 1 https://github.com/drewsum/KiPI.git reference-tools/KiPI
uv python install 3.12
uv venv --python 3.12 reference-tools/kipi-venv
uv pip install --python reference-tools/kipi-venv/bin/python ./reference-tools/KiPI
```

Do not use `pip install kipi`: the PyPI name belongs to a different project.
Do not add either solver's packages to PDNLab's core dependencies. Third-party
source and binaries stay in the ignored `reference-tools/` directory.

Sources checked on 14 November 2026:

- [padne installation and CLI](https://github.com/atx/padne)
- [padne CLI source](https://github.com/atx/padne/blob/master/padne/cli.py)
- [padne directive implementation](https://github.com/atx/padne/blob/master/padne/kicad.py)
- [KiPI standalone installation](https://github.com/drewsum/KiPI#standalone)
- [KiPI release v1.0.1](https://github.com/drewsum/KiPI/releases/tag/v1.0.1)
- [KiPI standalone requirements](https://github.com/drewsum/KiPI/blob/v1.0.1/requirements-standalone.txt)
- [KiPI standalone entry point](https://github.com/drewsum/KiPI/blob/v1.0.1/pyproject.toml)

## Prepared comparison

```sh
uv run python experiments/exp07_reference_tools.py --output-dir /tmp/pdnlab-exp07
```

This copies only the native StickHub project files and their licence into a
working directory, validates the chosen terminals, writes `benchmark.json` and
`padne-directives.txt`, and reports installation/preflight blockers in
`results.json`. Exit code **2** means reference results are pending. The source
demo is not modified. The board hash is the same as experiment 6.

The benchmark is a synthetic distribution test, not a model of all the board's
real operating loads:

| Setting | Target |
| --- | --- |
| Net | +5V |
| Voltage source | 5 V at J2.1 relative to J2.4 |
| Load | 0.5 A drawn at J7.1, returned directly to J2.4 |
| Return model | Ideal common return at the source; exclude remote GND copper drop |
| Probes | J2.1, J3.1, J4.1, J5.1, J6.1, J7.1, J8.1 |
| Copper | 35 µm each layer, ρ = 1.724e-8 Ω·m at 20 °C |
| Barrel plating | 25 µm, explicitly assumed for this comparison |
| Etch undercut | 0 |
| Thermal / active components | Disabled / unmodelled |

Compare power-net drop relative to the same source reference. Do not compare
KiPI's power-net-only results with a padne case containing extra return-copper
drop. Confirm the tool's actual return model before accepting measurements.
Material and meshing targets must also be confirmed in each tool; defaults are
not assumed to match. Record unsupported settings rather than quietly changing
the benchmark.

### padne

Once installed, preflight and attempt the solve with:

```sh
uv run python experiments/exp07_reference_tools.py --output-dir /tmp/pdnlab-exp07 --padne reference-tools/padne-linux-x64 --run-padne
```

The runner checks supported CLI flags first, ignores schematic directives to
avoid unintended extra loads, and supplies external directives for this test.
It captures version/help and solve output. A saved `.padne` solution is still
not a recorded voltage comparison: probe values, material settings, mesh
settings and warnings need to be extracted and reviewed afterward.

### KiPI

Open the prepared board using the standalone app:

```sh
reference-tools/kipi-venv/bin/kipi /tmp/pdnlab-exp07/board/StickHub.kicad_pcb
```

The installed `kipi` entry point uses the explicitly installed environment. The official
launcher (`kipi_launcher.py`) is an alternative that manages its own dependency
environment; do not mix the two setup routes.

Configure one DC source and one load according to `benchmark.json`, disable
thermal and other loads, and save the setup and report. Confirm the copper
material, plating and return treatment. Record the application version/commit,
mesh settings, source current and voltage at each probe relative to J2.4.
Export the report/settings before closing. Exact field names and extraction
automation will be checked against the installed release before the run.

## padne on a local test board

This is the run recorded on 2026-11-15. It compares padne with the experiment 8
result for one net, so `experiments/exp08_analyze_3v3.py` must have written its
output directory first. `B` is the board's directory; test boards are not in
the repository.

```sh
B="tests/test boards/test brd 1"
reference-tools/padne-linux-x64 solve "$B/MAIN_MCU_STM32H743.kicad_pro" "$B/_exp07/solution.padne" \
    --no-schematic-directives --directives "$B/_exp07/padne-directives.txt" 2>&1 | tee "$B/_exp07/padne-solve.log"
reference-tools/padne-linux-x64 paraview "$B/_exp07/solution.padne" "$B/_exp07/vtu"
reference-tools/padne-linux-x64 solve "$B/MAIN_MCU_STM32H743.kicad_pro" "$B/_exp07/solution_h015.padne" \
    --mesh-size 0.15 --no-schematic-directives --directives "$B/_exp07/padne-directives.txt" 2>&1 | tee "$B/_exp07/padne-solve_h015.log"
reference-tools/padne-linux-x64 paraview "$B/_exp07/solution_h015.padne" "$B/_exp07/vtu_h015"
uv run python experiments/exp07_padne_compare.py "$B/MAIN_MCU_STM32H743.kicad_pcb" "$B/_exp08" \
    "$B/_exp07/vtu" "$B/_exp07/vtu_h015" --output "$B/_exp07/padne-comparison.json"
```

The directive file holds one `VOLTAGE` for the source pins, one `CURRENT` per
load pin returning to the source's negative pad, and one `COPPER` line. Lengths
in `COPPER` are in metres with an SI prefix and no unit letter: `plating=25u`.
`plating=25um` does not parse.

The comparison script exits 1 if any pin fails gate 6 after referencing both
tools to the plane voltage at the feed vias. It reports the raw comparison as
well; on this board the raw comparison fails because the two tools model the
source pads differently.

## Completion evidence

For each tool, retain its version or commit, working-board hash, saved setup,
mesh/material settings, log, and probe voltages in volts. If installation,
import, meshing or solving fails, record the exact failure and reproduction
command. A missing installation is not a completed numerical cross-check.

PDNLab cannot solve StickHub yet, so this experiment only establishes external
reference data for a later comparison. Do not apply the release 0.1 comparison
tolerance or claim agreement with PDNLab until real-board solving exists.
