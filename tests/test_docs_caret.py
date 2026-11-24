from __future__ import annotations

import json
import socket
import tempfile
import time
import unittest
from pathlib import Path

from violet.docs_caret import DocsCaretBridge, parse_caret_message


def caret_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "caret",
        "version": 1,
        "session": "local_docs_cursor_1",
        "sequence": 7,
        "left": 206.0,
        "top": 240.0,
        "right": 207.5,
        "bottom": 257.0,
        "viewport_width": 1074.0,
        "viewport_height": 892.0,
        "focused": True,
        "visible": True,
    }
    payload.update(changes)
    return payload


class DocsCaretGeometryTests(unittest.TestCase):
    def test_valid_caret_maps_css_viewport_to_physical_uia_canvas(self) -> None:
        geometry = parse_caret_message(caret_payload(), received_at=10.0)

        self.assertIsNotNone(geometry)
        assert geometry is not None
        # A live Docs window at 137.5% display scale supplied these ratios:
        # the mapped geometry follows the visible browser canvas, not an
        # untrusted screen/window coordinate supplied by the extension.
        self.assertEqual(geometry.map_to_visible_target((30, 120, 1507, 1347)), (313, 450, 315, 474))
        self.assertTrue(geometry.is_fresh(now=10.8))
        self.assertFalse(geometry.is_fresh(now=11.3))

    def test_malformed_or_nonlocal_geometry_is_rejected(self) -> None:
        self.assertIsNone(parse_caret_message(caret_payload(focused=False), received_at=1.0))
        self.assertIsNone(parse_caret_message(caret_payload(right=400.0), received_at=1.0))
        self.assertIsNone(parse_caret_message(caret_payload(session="too short"), received_at=1.0))
        self.assertIsNone(parse_caret_message(caret_payload(sequence=True), received_at=1.0))
        self.assertIsNone(parse_caret_message(caret_payload(viewport_width=float("inf")), received_at=1.0))


class DocsCaretBridgeTests(unittest.TestCase):
    def test_authenticated_loopback_bridge_accepts_geometry_and_removes_own_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bridge = DocsCaretBridge(root)
            bridge.start()
            try:
                descriptor = json.loads(bridge.descriptor_path.read_text(encoding="utf-8"))
                self.assertEqual(descriptor["host"], "127.0.0.1")
                self.assertEqual(descriptor["version"], 1)
                with socket.create_connection((descriptor["host"], descriptor["port"]), timeout=1.0) as client:
                    stream = client.makefile("wb")
                    stream.write(json.dumps({"type": "hello", "version": 1, "token": descriptor["token"]}).encode() + b"\n")
                    stream.write(json.dumps(caret_payload()).encode() + b"\n")
                    stream.flush()
                    received = None
                    for _ in range(40):
                        received = bridge.latest()
                        if received is not None:
                            break
                        time.sleep(0.01)
                self.assertIsNotNone(received)
                assert received is not None
                self.assertEqual(received.session, "local_docs_cursor_1")
                self.assertEqual(received.sequence, 7)
            finally:
                bridge.stop()
            self.assertFalse(bridge.descriptor_path.exists())

    def test_bad_bearer_cannot_publish_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = DocsCaretBridge(Path(temporary))
            bridge.start()
            try:
                with socket.create_connection(("127.0.0.1", bridge.port), timeout=1.0) as client:
                    client.sendall(b'{"type":"hello","version":1,"token":"wrong"}\n')
                    client.sendall(json.dumps(caret_payload()).encode() + b"\n")
                time.sleep(0.03)
                self.assertIsNone(bridge.latest())
            finally:
                bridge.stop()


if __name__ == "__main__":
    unittest.main()
