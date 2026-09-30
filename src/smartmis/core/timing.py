"""Lightweight execution timing used to report stage durations."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Timer:
    """Holds the elapsed time of a timed block once it has finished."""

    label: str
    started: float = field(default_factory=time.perf_counter)
    elapsed: float = 0.0

    def stop(self) -> float:
        self.elapsed = time.perf_counter() - self.started
        return self.elapsed


@contextmanager
def timed(label: str, logger: logging.Logger | None = None) -> Iterator[Timer]:
    """Time a block of code.

    Example::

        with timed("validation", log) as t:
            report = validator.validate(df)
        stage_timings["validation"] = t.elapsed
    """
    timer = Timer(label)
    try:
        yield timer
    finally:
        timer.stop()
        if logger is not None:
            logger.info("Stage '%s' finished in %.3f s", label, timer.elapsed)
