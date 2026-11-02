"""Ensure Google sign-in happens in a real, non-Playwright browser."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from google_login import LOGIN_URL, browser_profile, open_google_signin, find_browser
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
                    self.assertEqual(open_google_signin(Path(tmp)), 'chrome')
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
                    self.assertEqual(open_google_signin(Path(tmp)), 'msedge')
            args = popen.call_args.args[0]
            self.assertIn('google-login-msedge', args[1])
            path, opts = browser_launch_settings(Path(tmp), 'msedge')
            self.assertTrue(path.endswith('google-login-msedge'))
            self.assertEqual(opts['channel'], 'msedge')

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
