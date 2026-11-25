"""Per-stage time and memory of a run (performance stage, item 1; decided by Erekle,
2026-11-25): every later optimisation is measured against these numbers.

A ``Stopwatch`` records ``Stage`` laps. Each lap holds:

* ``seconds``: wall-clock time of the stage;
* ``cpu_seconds``: processor time of this process and its finished children (Gmsh) in
  the stage. Much less than ``seconds`` means the stage waited: for the disk, or for a
  processor busy with other programs. More than ``seconds`` means several threads.
* ``rss_bytes``: resident memory of this process after the stage (what it still holds);
* ``peak_bytes``: this process's peak resident memory so far;
* ``child_peak_bytes``: the largest finished child process (Gmsh) so far.

Peaks are "so far" because the operating system keeps only a running maximum: a stage
whose peak is higher than the previous stage's set the new maximum. Memory values are
None where the platform does not report them.
"""

from dataclasses import dataclass
from time import perf_counter

from .memory import child_peak_bytes, current_bytes, own_peak_bytes


@dataclass(frozen=True)
class Stage:
    name: str
    seconds: float
    cpu_seconds: float
    rss_bytes: int | None
    peak_bytes: int | None
    child_peak_bytes: int | None


def cpu_seconds():
    """Processor time used so far by this process and its finished children."""
    try:
        import resource
    except ImportError:
        import time
        return time.process_time()
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


class Stopwatch:
    def __init__(self):
        self.stages = []
        self._start()

    def _start(self):
        self._wall, self._cpu = perf_counter(), cpu_seconds()

    def lap(self, name):
        """Record the stage since the last lap (or since the stopwatch was made)."""
        held, peak = current_bytes(), own_peak_bytes()
        if held is not None and peak is not None:
            peak = max(peak, held)      # the kernel's running maximum can lag a page or two
        self.stages.append(Stage(name, perf_counter() - self._wall, cpu_seconds() - self._cpu,
                                 held, peak, child_peak_bytes()))
        self._start()
        return self.stages[-1]
