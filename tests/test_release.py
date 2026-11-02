"""Release-automation and browser fallback regression tests."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from automation import StopRun, choose_browser_channel, browser_launch_settings

ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_release_only_on_successful_main_build(self):
        workflow = (ROOT / '.github/workflows/windows-build.yml').read_text()
        self.assertIn('needs: build-windows-exe', workflow)
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/main'", workflow)
        self.assertIn('contents: write', workflow)
        self.assertIn('GH_REPO: ${{ github.repository }}', workflow)
        self.assertIn('actions/download-artifact@v8', workflow)
        self.assertIn('sha256sum --check', workflow)
        self.assertIn('gh release create', workflow)
        self.assertIn('--target "$GITHUB_SHA"', workflow)
        self.assertIn('--latest', workflow)

    def test_uses_existing_playwright_chromium(self):
        with TemporaryDirectory() as tmp:
            executable = Path(tmp) / 'chrome.exe'
            executable.touch()
            self.assertEqual(choose_browser_channel('chromium', executable), 'chromium')

    def test_system_browser_fallback(self):
        with patch('automation.find_browser', return_value=('chrome', 'C:/chrome.exe')) as f:
            self.assertEqual(choose_browser_channel('chromium', '/missing/chromium.exe'), 'chrome')
            f.assert_called_once()
        with TemporaryDirectory() as tmp:
            path, options = browser_launch_settings(tmp, 'chrome')
            self.assertEqual(Path(path).name, 'google-login-chrome')
            self.assertEqual(options, {'channel': 'chrome'})

    def test_saved_channel_is_not_overridden(self):
        with patch('automation.find_browser') as f:
            self.assertEqual(choose_browser_channel('msedge', '/missing/chromium.exe'), 'msedge')
            f.assert_not_called()

    def test_no_installed_browser_is_actionable_error(self):
        with patch('automation.find_browser', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(StopRun, 'Install Chrome or Edge'):
                choose_browser_channel('chromium', '/missing/chromium.exe')


if __name__ == '__main__':
    unittest.main()
