"""KiCad's own command line, used only to export IPC-2581 from a ``.kicad_pcb``.

The board is still read only through KiCad's IPC-2581 export (no pcbnew or SWIG).
``kicad-cli`` is found on PATH, or given by the ``PDNLAB_KICAD_CLI`` environment variable
(for a snap, flatpak or non-standard install). Decided by Erekle (2026-11-22): PDNLab
exports by itself and re-exports when the ``.kicad_pcb`` is newer than the export, so a
stale board cannot be simulated by accident.
"""

import os
import shutil
import subprocess
from pathlib import Path

EXPORT_ARGS = ("pcb", "export", "ipc2581", "--version", "C", "--units", "mm")


class KicadError(RuntimeError):
    """kicad-cli is missing or its export failed."""


def kicad_cli():
    """Path of the kicad-cli executable."""
    given = os.environ.get("PDNLAB_KICAD_CLI")
    found = given or shutil.which("kicad-cli")
    if not found:
        raise KicadError("kicad-cli not found: install KiCad, or set PDNLAB_KICAD_CLI to its "
                         "path, or export IPC-2581 yourself (File > Fabrication Outputs)")
    return found


def export_ipc2581(kicad_pcb, output, timeout=300):
    """Export ``kicad_pcb`` to ``output`` (IPC-2581 rev C, mm) with kicad-cli."""
    kicad_pcb, output = Path(kicad_pcb), Path(output)
    if not kicad_pcb.is_file():
        raise KicadError(f"{kicad_pcb}: no such KiCad board")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [kicad_cli(), *EXPORT_ARGS, "--output", str(output), str(kicad_pcb)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except OSError as error:
        raise KicadError(f"could not run kicad-cli: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise KicadError(f"kicad-cli took longer than {timeout} s") from error
    if done.returncode != 0 or not output.is_file():
        said = "\n".join(s.strip() for s in (done.stdout, done.stderr) if s.strip())
        raise KicadError(f"kicad-cli export failed (exit {done.returncode}):\n{said}".strip())
    return output


def export_is_stale(kicad_pcb, export):
    """True when the export is missing or older than the board."""
    export = Path(export)
    return not export.is_file() or Path(kicad_pcb).stat().st_mtime > export.stat().st_mtime
