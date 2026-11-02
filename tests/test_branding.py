"""Offline checks for size-optimized, consistently packaged official branding."""
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from scripts import build_windows


class BrandingTests(unittest.TestCase):
    def test_assets_present(self):
        for name in ('connectionpilot.png', 'connectionpilot-header.png',
                     'connectionpilot.ico', 'social-preview.png'):
            p = main.asset_path(name)
            self.assertTrue(p.is_file(), name)
            self.assertGreater(p.stat().st_size, 100)

    def test_logo_and_header_transparency_and_dimensions(self):
        for name, width in [('connectionpilot.png', 256),
                            ('connectionpilot-header.png', 48)]:
            raw = main.asset_path(name).read_bytes()
            self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(struct.unpack_from('>II', raw, 16), (width, width))
            self.assertEqual(raw[25], 6, 'PNG must have RGBA/alpha')

    def test_custom_ico_frames_have_real_dimensions_and_transparent_corners(self):
        content = main.asset_path('connectionpilot.ico').read_bytes()
        self.assertEqual(struct.unpack_from('<HH', content), (0, 1))
        total = struct.unpack_from('<H', content, 4)[0]
        sizes = set()
        for idx in range(total):
            w, h, _, _, planes, bits, size, offset = struct.unpack_from(
                '<BBBBHHII', content, 6 + idx * 16)
            w, h = w or 256, h or 256
            sizes.add(w)
            self.assertEqual((w,h), (w,w))
            self.assertEqual((planes,bits), (1,32))
            png = content[offset:offset+size]
            self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(struct.unpack_from('>II',png,16), (w,w))
            self.assertEqual(png[25], 6)
        self.assertEqual(sizes, {16, 20, 24, 32, 40, 48, 64, 128, 256})

    def test_windows_ico_has_final_priority_over_png(self):
        class FakeRoot:
            calls = []
            def iconphoto(self, *args): self.calls.append('png')
            def iconbitmap(self, *args, **kwargs): self.calls.append('ico')
        root = FakeRoot()
        with patch.object(main.sys, 'platform', 'win32'), \
             patch.object(main.tk, 'PhotoImage', return_value=object()):
            main.set_window_branding(root)
        self.assertEqual(root.calls, ['png', 'ico'])

    def test_app_and_exe_include_header_png(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / 'main.py').read_text()
        builder = (root / 'scripts/build_windows.py').read_text()
        self.assertIn('asset_path("connectionpilot-header.png")', app)
        self.assertIn('assets/connectionpilot-header.png', builder)
        self.assertIn('assets/connectionpilot.ico', builder)
        self.assertIn('root.iconbitmap(default=str(ico))', app)

    def test_bundled_asset_location(self):
        with patch.object(main.sys,'_MEIPASS','/tmp/connectionpilot-test',create=True):
            self.assertEqual(main.asset_path('connectionpilot.png'),
                             Path('/tmp/connectionpilot-test/assets/connectionpilot.png'))

    def test_release_logo_assets(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/windows-build.yml').read_text()
        self.assertIn('assets/connectionpilot.png', workflow)
        self.assertIn('assets/connectionpilot.ico', workflow)

    def test_windows_builder_not_cross_compilable(self):
        with patch.object(build_windows.sys,'platform','linux'):
            with self.assertRaisesRegex(RuntimeError,'Windows'):
                build_windows.build()


if __name__ == '__main__':
    unittest.main()
