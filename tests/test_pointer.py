from __future__ import annotations

import unittest

from violet.pointer import PointerClickTracker


class PointerClickTrackerTests(unittest.TestCase):
    def test_coordinate_only_click_sequence_is_monotonic(self) -> None:
        tracker = PointerClickTracker()

        first = tracker._record(120, 240, 2.5)
        second = tracker._record(-4, 15, 3.0)

        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(tracker.latest(), second)
        self.assertEqual((second.x, second.y, second.observed_at), (-4, 15, 3.0))


if __name__ == "__main__":
    unittest.main()
