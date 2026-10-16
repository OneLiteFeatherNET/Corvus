"""Consumer lag watch: is the scorer keeping up with the incoming windows?

Pure state machine so it is testable without NATS or a clock: feed it the pending count of the
capture consumer once per poll.
"""

from __future__ import annotations


class LagWatch:
    """Reports once when the pending count grew over ``polls`` consecutive observations."""

    def __init__(self, polls: int) -> None:
        self._polls = polls
        self._last: int | None = None
        self._growing = 0
        self._reported = False

    def observe(self, pending: int) -> bool:
        """True exactly when this observation completes a run of ``polls`` growing counts."""
        if self._last is not None and pending > self._last:
            self._growing += 1
        else:
            self._growing = 0
            self._reported = False
        self._last = pending
        if self._polls > 0 and self._growing >= self._polls and not self._reported:
            self._reported = True
            return True
        return False

    @property
    def behind(self) -> bool:
        return self._reported
