from __future__ import annotations

import unittest

from violet.runtime import executable_path, installation_root, is_frozen, resource_path


class RuntimeTests(unittest.TestCase):
    def test_source_paths_are_resolved_and_stable(self) -> None:
        self.assertFalse(is_frozen())
        self.assertTrue(executable_path().is_file())
        self.assertTrue((installation_root() / "violet").is_dir())
        self.assertEqual(resource_path("violet").resolve(), installation_root() / "violet")


if __name__ == "__main__":
    unittest.main()
