"""Regression tests for clock-bound waits and pause/resume status updates."""
import unittest
from unittest.mock import patch

from automation import wait_or_stop


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds


class FakeStop:
    def __init__(self, clock):
        self.clock = clock

    def is_set(self):
        return False

    def wait(self, seconds):
        self.clock.wait(seconds)


class PauseOnce:
    def __init__(self):
        self.checks = 0

    def is_set(self):
        self.checks += 1
        # Pause after the first second; resume half a second later.
        return self.checks in (2, 3)


class SchedulingWaitTests(unittest.TestCase):
    def run_pause(self, preserve):
        clock = FakeClock()
        messages = []
        with patch("automation.time.monotonic", side_effect=clock.monotonic):
            wait_or_stop(2, FakeStop(clock), PauseOnce(), messages.append,
                         reason="Outside sending hours (8:00–20:00 Eastern).",
                         preserve_pause_delay=preserve)
        return clock.now, messages

    def test_clock_bound_wait_does_not_shift_during_pause(self):
        finished_at, messages = self.run_pause(False)
        self.assertAlmostEqual(finished_at, 2.0)
        self.assertTrue(any("Outside sending hours" in line for line in messages))

    def test_cooldown_wait_still_preserves_paused_time(self):
        finished_at, _ = self.run_pause(True)
        self.assertAlmostEqual(finished_at, 2.5)

    def test_resume_replaces_paused_status_with_countdown(self):
        _, messages = self.run_pause(False)
        pause_index = messages.index("Paused. No requests will be sent.")
        self.assertTrue(any("Next action in" in line for line in messages[pause_index + 1:]))


if __name__ == "__main__":
    unittest.main()
