"""Ensure Google sign-in happens in a real, non-Playwright browser."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from google_login import LOGIN_URL, browser_profile, open_google_signin, close_google_signin, find_browser
import main
from automation import browser_launch_settings


class GoogleLoginTests(unittest.TestCase):
    def test_profile_separation_and_reuse(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            chrome_profile = browser_profile(base, "chrome")
            edge_profile = browser_profile(base, "msedge")
            self.assertNotEqual(chrome_profile, edge_profile)
            self.assertNotEqual(chrome_profile, base / "browser-profile")
            launch_path, launch_options = browser_launch_settings(base, "chrome")
            self.assertEqual(Path(launch_path), chrome_profile)
            self.assertEqual(launch_options, {"channel": "chrome"})
            legacy_path, legacy_options = browser_launch_settings(base, "chromium")
            self.assertEqual(Path(legacy_path), base / "browser-profile")
            self.assertEqual(legacy_options, {})
            with self.assertRaises(ValueError):
                browser_launch_settings(base, "unknown")
            with self.assertRaises(ValueError):
                browser_profile(base, "unknown")

    def test_launches_normal_browser_and_no_credentials_are_used(self):
        with TemporaryDirectory() as tmp:
            with patch('google_login.find_browser', return_value=('chrome', 'C:/Chrome/chrome.exe')):
                with patch('google_login.subprocess.Popen') as popen:
                    channel, launched = open_google_signin(Path(tmp))
                    self.assertEqual(channel, 'chrome')
                    self.assertIs(launched, popen.return_value)
            args = popen.call_args.args[0]
            self.assertEqual(args[0], 'C:/Chrome/chrome.exe')
            self.assertIn('--new-window', args)
            self.assertEqual(args[-1], LOGIN_URL)
            self.assertTrue(any(x.startswith('--user-data-dir=') for x in args))
            self.assertFalse(any('password' in x.lower() or 'cookie' in x.lower() for x in args))
            self.assertTrue(browser_profile(Path(tmp), 'chrome').is_dir())

    def test_edge_uses_distinct_profile(self):
        with TemporaryDirectory() as tmp:
            with patch('google_login.find_browser', return_value=('msedge', 'C:/Edge/msedge.exe')):
                with patch('google_login.subprocess.Popen') as popen:
                    channel, launched = open_google_signin(Path(tmp))
                    self.assertEqual(channel, 'msedge')
                    self.assertIs(launched, popen.return_value)
            args = popen.call_args.args[0]
            self.assertIn('google-login-msedge', args[1])
            path, opts = browser_launch_settings(Path(tmp), 'msedge')
            self.assertTrue(path.endswith('google-login-msedge'))
            self.assertEqual(opts['channel'], 'msedge')

    def test_windows_close_ends_only_the_owned_signin_process_tree(self):
        process = MagicMock()
        process.pid = 1457
        process.poll.return_value = None
        with patch("google_login.sys.platform", "win32"), \
             patch("google_login.subprocess.run") as run:
            close_google_signin(process)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["taskkill", "/PID", "1457", "/T"])
        self.assertEqual(kwargs["timeout"], 4)
        process.wait.assert_called_once_with(timeout=2)

    def test_windows_close_forces_tree_only_after_graceful_timeout(self):
        import subprocess
        process = MagicMock()
        process.pid = 1458
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("taskkill", 2), None]
        with patch("google_login.sys.platform", "win32"), \
             patch("google_login.subprocess.run") as run:
            close_google_signin(process)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0],
                         ["taskkill", "/PID", "1458", "/T"])
        self.assertEqual(run.call_args_list[1].args[0],
                         ["taskkill", "/PID", "1458", "/T", "/F"])
        self.assertEqual(process.wait.call_count, 2)

    def test_already_closed_signin_browser_never_gets_killed(self):
        process = MagicMock()
        process.poll.return_value = 0
        with patch("google_login.subprocess.run") as run:
            close_google_signin(process)
        run.assert_not_called()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_app_close_releases_owned_login_process_before_destroy(self):
        app = main.App.__new__(main.App)
        app.root = MagicMock()
        app.signin_process = MagicMock()
        app.running = False
        app.closing = False
        app.worker = None
        with patch("main.close_google_signin") as cleanup:
            app.close()
        cleanup.assert_called_once()
        app.root.destroy.assert_called_once()
        self.assertIsNone(app.signin_process)
        self.assertTrue(app.closing)

    def test_app_waits_for_automation_worker_before_finishing_close(self):
        app = main.App.__new__(main.App)
        app.root = MagicMock()
        proc = MagicMock()
        app.signin_process = proc
        app.worker = MagicMock()
        app.worker.is_alive.side_effect = [True, False]
        app.running = True
        app.closing = False
        app.stop_event = MagicMock()
        app.pause_event = MagicMock()
        app.status = MagicMock()
        for field in ("start_btn", "pause_btn", "stop_btn"):
            setattr(app, field, MagicMock())
        with patch("main.messagebox.askyesno", return_value=True), \
             patch("main.close_google_signin") as cleanup:
            app.close()
            app.stop_event.set.assert_called_once()
            app.root.destroy.assert_not_called()
            app.finish_close()
            cleanup.assert_not_called()
            app.root.destroy.assert_not_called()
            app.finish_close()
            cleanup.assert_called_once_with(proc)
        app.root.destroy.assert_called_once()

    def test_repeated_signin_click_does_not_launch_second_browser(self):
        app = main.App.__new__(main.App)
        app.running = False
        app.signin_process = MagicMock()
        app.signin_process.poll.return_value = None
        with patch("main.open_google_signin") as launch, \
             patch("main.messagebox.showinfo") as info:
            app.google_signin()
        launch.assert_not_called()
        info.assert_called_once()

    def test_prefers_standard_install_over_path_binary(self):
        with patch('google_login.shutil.which', return_value='C:/Temp/chrome.exe'):
            with patch('google_login.Path.is_file', autospec=True, side_effect=lambda path: str(path).replace('\\', '/').endswith('Google/Chrome/Application/chrome.exe') or str(path) == 'C:/Temp/chrome.exe'):
                channel, path = find_browser()
        self.assertEqual(channel, 'chrome')
        self.assertTrue(path.replace('\\', '/').endswith('Google/Chrome/Application/chrome.exe'))

    def test_no_browser_installed_shows_actionable_error(self):
        with patch('google_login.shutil.which', return_value=None):
            with patch('google_login.Path.is_file', return_value=False):
                with self.assertRaisesRegex(FileNotFoundError, 'Chrome or Microsoft Edge'):
                    find_browser()


if __name__ == '__main__':
    unittest.main()
