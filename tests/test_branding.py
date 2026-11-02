"""Asset and build checks independent of a Windows runner."""
import unittest
import struct
from pathlib import Path
from unittest.mock import patch

import main
from scripts import build_windows


class BrandingTests(unittest.TestCase):
    def test_source_assets_exist_and_are_nonempty(self):
        for name in ('connectionpilot.png', 'connectionpilot.ico', 'social-preview.png'):
            path = main.asset_path(name)
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 1000)


    def test_png_is_transparent_rgba_at_256(self):
        data = main.asset_path('connectionpilot.png').read_bytes()
        self.assertTrue(data.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))))
        self.assertEqual(struct.unpack('>IIBB', data[16:26]), (256, 256, 8, 6))

    def test_windows_ico_contains_small_and_large_sizes(self):
        data = main.asset_path('connectionpilot.ico').read_bytes()
        self.assertEqual(struct.unpack_from('<HH', data), (0, 1))
        entries = struct.unpack_from('<H', data, 4)[0]
        sizes = set()
        for i in range(entries):
            width, height = struct.unpack_from('<BB', data, 6 + i * 16)
            sizes.add((width or 256, height or 256))
        self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)}.issubset(sizes))

    def test_header_icon_stays_small_on_all_asset_sizes(self):
        self.assertEqual(main.icon_preview_scale(128, 128), 3)
        self.assertEqual(main.icon_preview_scale(256, 256), 6)
        self.assertEqual(main.icon_preview_scale(512, 512), 11)
        self.assertEqual(main.icon_preview_scale(32, 32), 1)

    def test_release_attaches_official_brand_files(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/windows-build.yml').read_text()
        self.assertIn('assets/connectionpilot.png#Transparent app logo', workflow)
        self.assertIn('assets/connectionpilot.ico#Windows app icon', workflow)
        self.assertIn('assets/social-preview.png#GitHub social preview', workflow)

    def test_bundled_asset_location(self):
        with patch.object(main.sys, '_MEIPASS', '/tmp/connectionpilot-test', create=True):
            self.assertEqual(main.asset_path('connectionpilot.png'),
                             Path('/tmp/connectionpilot-test/assets/connectionpilot.png'))

    def test_windows_builder_not_cross_compilable(self):
        with patch.object(build_windows.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(RuntimeError, 'Windows'):
                build_windows.build()
