"""Asset and build checks independent of a Windows runner."""
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from scripts import build_windows


class BrandingTests(unittest.TestCase):
    def test_source_assets_exist_and_are_nonempty(self):
        for name in ('connectionpilot.png', 'connectionpilot.ico'):
            path = main.asset_path(name)
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 1000)

    def test_bundled_asset_location(self):
        with patch.object(main.sys, '_MEIPASS', '/tmp/connectionpilot-test', create=True):
            self.assertEqual(main.asset_path('connectionpilot.png'),
                             Path('/tmp/connectionpilot-test/assets/connectionpilot.png'))

    def test_windows_builder_not_cross_compilable(self):
        with patch.object(build_windows.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(RuntimeError, 'Windows'):
                build_windows.build()
