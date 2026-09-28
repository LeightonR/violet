from __future__ import annotations

import struct
import unittest

from violet.hardware import RELATION_PROCESSOR_CORE, _count_processor_core_records


def record(relationship: int, payload_size: int = 8) -> bytes:
    size = 8 + payload_size
    return struct.pack("<II", relationship, size) + (b"\0" * payload_size)


class HardwareTests(unittest.TestCase):
    def test_core_record_parser_counts_only_processor_cores(self) -> None:
        buffer = record(RELATION_PROCESSOR_CORE) + record(1) + record(RELATION_PROCESSOR_CORE)
        self.assertEqual(_count_processor_core_records(buffer), 2)

    def test_core_record_parser_rejects_malformed_record(self) -> None:
        self.assertEqual(_count_processor_core_records(struct.pack("<II", 0, 1024)), 0)


if __name__ == "__main__":
    unittest.main()
