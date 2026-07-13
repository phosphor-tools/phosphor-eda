"""Lightweight profiling helpers for PCB rendering."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

ProfileValue = int | float | str | bool | None


@dataclass(frozen=True)
class RenderProfileEvent:
    name: str
    seconds: float
    data: dict[str, ProfileValue] = field(default_factory=dict)


class RenderProfiler:
    """Collect render timings and size/count metrics for diagnostics."""

    def __init__(self) -> None:
        self._events: list[RenderProfileEvent] = []

    @contextmanager
    def span(self, name: str, **data: ProfileValue) -> Generator[None]:
        start = perf_counter()
        try:
            yield
        finally:
            self._events.append(RenderProfileEvent(name, perf_counter() - start, dict(data)))

    def metric(self, name: str, **data: ProfileValue) -> None:
        self._events.append(RenderProfileEvent(name, 0.0, dict(data)))

    def to_dict(self) -> dict[str, object]:
        total_seconds = sum(event.seconds for event in self._events if "." not in event.name)
        return {
            "totalProfiledSeconds": round(total_seconds, 6),
            "events": [
                {
                    "name": event.name,
                    "seconds": round(event.seconds, 6),
                    **({"data": dict(event.data)} if event.data else {}),
                }
                for event in self._events
            ],
        }


def profile_span(
    profiler: RenderProfiler | None,
    name: str,
    **data: ProfileValue,
) -> AbstractContextManager[None]:
    """Return a timing span when *profiler* is set, else a no-op context.

    Lets callers wrap a block in ``with profile_span(profiler, "name"):``
    without branching on whether profiling is enabled.
    """
    if profiler is None:
        return nullcontext()
    return profiler.span(name, **data)
