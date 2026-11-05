"""Offline checks for size-optimized, consistently packaged official branding."""
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from scripts import build_windows


class BrandingTests(unittest.TestCase):
    def test_new_install_starts_blank_but_existing_terms_are_restored(self):
        from tempfile import TemporaryDirectory
        from storage import Store
        with TemporaryDirectory() as directory:
            store = Store(Path(directory) / "data.sqlite3")
            self.assertEqual(main.initial_search_inputs(store), ("", ""))
            store.set_setting("ui:searches", "government NJ\\npublic policy")
            store.set_setting("ui:matches", "politics, Rowan")
            self.assertEqual(main.initial_search_inputs(store),
                             ("government NJ\\npublic policy", "politics, Rowan"))
            # Explicitly empty saved values must stay empty.
            store.set_setting("ui:searches", "")
            self.assertEqual(main.initial_search_inputs(store), ("", "politics, Rowan"))

    def test_ui_log_is_bounded_without_erasing_recent_entries(self):
        class LogWidget:
            def __init__(self):
                self.lines = []
            def configure(self, **kwargs):
                pass
            def insert(self, pos, text):
                self.lines.append(text)
            def index(self, pos):
                self_test.assertEqual(pos, "end-1c")
                return f"{len(self.lines)+1}.0"
            def delete(self, start, end):
                self_test.assertEqual(start, "1.0")
                del self.lines[:int(end.split(".")[0]) - 1]
            def see(self, pos):
                pass
        self_test = self
        app = main.App.__new__(main.App)
        app.log_widget = LogWidget()
        for index in range(main.MAX_UI_LOG_LINES + 12):
            app.put_log(f"Event {index}")
        self.assertEqual(len(app.log_widget.lines), main.MAX_UI_LOG_LINES)
        self.assertIn("Event 12", app.log_widget.lines[0])
        self.assertIn(f"Event {main.MAX_UI_LOG_LINES + 11}", app.log_widget.lines[-1])

    def test_ui_pump_processes_bursts_in_small_batches(self):
        import queue
        from unittest.mock import MagicMock
        app = main.App.__new__(main.App)
        app.events = queue.Queue()
        app.root = MagicMock()
        app.status = MagicMock()
        app.put_log = MagicMock()
        for index in range(main.MAX_UI_EVENTS_PER_TICK + 7):
            app.events.put(("status", f"Event {index}"))
        app.pump()
        self.assertEqual(app.put_log.call_count, main.MAX_UI_EVENTS_PER_TICK)
        self.assertEqual(app.events.qsize(), 7)
        app.root.after.assert_called_once_with(25, app.pump)
        app.pump()
        self.assertEqual(app.events.qsize(), 0)
        app.root.after.assert_called_with(250, app.pump)

    def test_ui_refresh_requests_share_one_database_redraw(self):
        import queue
        from unittest.mock import MagicMock
        app = main.App.__new__(main.App)
        app.events = queue.Queue()
        app.root = MagicMock()
        app.refresh = MagicMock()
        app.status = MagicMock()
        app.put_log = MagicMock()
        for _ in range(12):
            app.events.put(("refresh", ""))
        app.pump()
        app.refresh.assert_called_once()
        app.root.after.assert_called_with(250, app.pump)

    def test_done_and_refresh_in_one_batch_update_counters_once(self):
        import queue
        from unittest.mock import MagicMock
        app = main.App.__new__(main.App)
        app.events = queue.Queue()
        app.root = MagicMock()
        app.refresh = MagicMock()
        app.pause_event = MagicMock()
        app.start_btn = MagicMock()
        app.signin_btn = MagicMock()
        app.retry_btn = MagicMock()
        app.reset_btn = MagicMock()
        app.pause_btn = MagicMock()
        app.stop_btn = MagicMock()
        app.running = True
        app.events.put(("refresh", ""))
        app.events.put(("done", ""))
        app.pump()
        app.refresh.assert_called_once()
        self.assertFalse(app.running)
        app.start_btn.configure.assert_called_once_with(state="normal")
        app.pause_event.clear.assert_called_once()

    def test_release_version_is_consistent(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/windows-build.yml").read_text()
        win_version = (root / "assets/connectionpilot_version.txt").read_text()
        self.assertEqual(main.APP_VERSION, "v1.0.5")
        self.assertIn("filevers=(1, 0, 5, 0)", win_version)
        self.assertIn("prodvers=(1, 0, 5, 0)", win_version)
        self.assertEqual(win_version.count("'1.0.5.0'"), 2)
        self.assertIn("FileVersion -ne '1.0.5.0'", workflow)
        self.assertIn('TAG="v1.0.5"', workflow)
        self.assertIn('ConnectionPilot-v1.0.5-Windows-EXE', workflow)

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
