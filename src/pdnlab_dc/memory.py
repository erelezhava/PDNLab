"""Memory: how much there is, how much a run uses, and a ceiling so a run cannot freeze
the machine. Decided by Erekle (2026-11-22): protect the computer before speed.

* ``total_bytes()`` / ``available_bytes()``: the machine's RAM and what is free now
  (``MemAvailable`` on Linux; None where unknown).
* ``peak_bytes()``: the peak resident memory of this process plus that of its finished
  child processes (Gmsh), a conservative measure of what a round used.
* ``apply_ceiling(limit)``: a hard limit on this process's address space, inherited by
  Gmsh, so that running out raises MemoryError (or Gmsh fails) instead of swapping the
  machine to a standstill. Address space includes memory reserved but not used (thread
  stacks, allocator arenas), so the ceiling is the limit plus a margin. Returns a note
  saying what was applied; on systems without the limit it says so and applies none.
"""

import os
import sys

CEILING_MARGIN_BYTES = 2 * 1024**3       # reserved-but-unused address space (threads, arenas)


def total_bytes():
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def available_bytes():
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def current_bytes():
    """Resident memory of this process now (Linux), or None."""
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def peak_bytes():
    """Peak resident memory of this process plus its largest finished child (Gmsh)."""
    try:
        import resource
    except ImportError:
        return None
    unit = 1 if sys.platform == "darwin" else 1024          # ru_maxrss: bytes on macOS, KiB on Linux
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * unit
    return own + children


def default_limit_bytes():
    """Half of the machine's RAM, or None when unknown."""
    total = total_bytes()
    return None if total is None else total // 2


def apply_ceiling(limit_bytes):
    """Hard address-space limit of ``limit_bytes`` + CEILING_MARGIN_BYTES; returns a note."""
    try:
        import resource
        ceiling = int(limit_bytes + CEILING_MARGIN_BYTES)
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            ceiling = min(ceiling, hard)
        resource.setrlimit(resource.RLIMIT_AS, (ceiling, hard))
    except (ImportError, ValueError, OSError, AttributeError) as error:
        return f"no hard memory ceiling on this system ({type(error).__name__}); the per-round " \
               "estimate still applies"
    return f"hard memory ceiling {ceiling / 1024**3:.1f} GB (limit + " \
           f"{CEILING_MARGIN_BYTES / 1024**3:.0f} GB for reserved address space)"
