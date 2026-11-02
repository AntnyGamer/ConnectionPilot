"""Asset and build checks independent of a Windows runner."""
import struct
import unittest
import zlib
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

    def test_png_is_transparent_rgba(self):
        image = main.asset_path("connectionpilot.png").read_bytes()
        self.assertTrue(image.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))))
        width, height, depth, mode = struct.unpack(">IIBB", image[16:26])
        self.assertEqual((width, height, depth, mode), (256, 256, 8, 6))
        offset, compressed = 8, bytearray()
        while offset < len(image):
            length = struct.unpack_from(">I", image, offset)[0]
            kind = image[offset + 4:offset + 8]
            if kind == b"IDAT":
                compressed.extend(image[offset + 8:offset + 8 + length])
            offset += length + 12
            if kind == b"IEND":
                break
        first_scanline = zlib.decompress(compressed)
        self.assertIn(first_scanline[0], range(5))
        self.assertEqual(first_scanline[4], 0, "PNG corners must be transparent")

    def test_ico_has_standard_windows_sizes(self):
        image = main.asset_path("connectionpilot.ico").read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", image)
        self.assertEqual((reserved, kind), (0, 1))
        sizes = set()
        for i in range(count):
            w, h = struct.unpack_from("<BB", image, 6 + i * 16)
            sizes.add((w or 256, h or 256))
        self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes)

    def test_small_header_icon(self):
        self.assertEqual(main.icon_preview_scale(256, 256), 6)
        self.assertEqual(main.icon_preview_scale(128, 128), 3)

    def test_release_includes_matching_logo_files(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/windows-build.yml").read_text()
        self.assertIn("assets/connectionpilot.png", workflow)
        self.assertIn("assets/connectionpilot.ico", workflow)
        self.assertIn("release/connectionpilot.png", workflow)
        self.assertIn("release/connectionpilot.ico", workflow)

    def test_bundled_asset_location(self):
        with patch.object(main.sys, '_MEIPASS', '/tmp/connectionpilot-test', create=True):
            self.assertEqual(main.asset_path('connectionpilot.png'),
                             Path('/tmp/connectionpilot-test/assets/connectionpilot.png'))

    def test_app_and_exe_use_shared_assets(self):
        root = Path(__file__).resolve().parents[1]
        builder = (root / "scripts/build_windows.py").read_text()
        main_code = (root / "main.py").read_text()
        self.assertIn("assets/connectionpilot.ico", builder)
        self.assertIn("assets/connectionpilot.png", builder)
        self.assertIn('asset_path("connectionpilot.ico")', main_code)
        self.assertIn('asset_path("connectionpilot.png")', main_code)
        self.assertIn("root.iconphoto(True, photo)", main_code)
        self.assertIn("icon_preview_scale(logo.width(), logo.height())", main_code)

    def test_windows_builder_not_cross_compilable(self):
        with patch.object(build_windows.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(RuntimeError, 'Windows'):
                build_windows.build()
