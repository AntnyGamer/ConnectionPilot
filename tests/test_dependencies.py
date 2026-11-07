"""Make dependency pin updates safe and synchronized with the launcher."""
import unittest
from unittest.mock import patch

import bootstrap


class DependencyTests(unittest.TestCase):
    def test_pins_match_requirements_file(self):
        pins = bootstrap.pinned_dependencies()
        contents = (bootstrap.BASE_DIR / "requirements.txt").read_text(encoding="utf-8")
        expected = {}
        for raw in contents.splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                name, separator, wanted = line.partition("==")
                self.assertEqual(separator, "==")
                expected[name.strip()] = wanted.strip()
        self.assertEqual(pins, expected)
        self.assertIn("playwright", pins)
        self.assertEqual(pins["tzdata"], "2026.6")

    def test_launcher_avoids_chromium_download_when_chrome_or_edge_is_present(self):
        from unittest.mock import MagicMock
        browser = MagicMock()
        browser.chromium.executable_path = "/path/to/missing-chromium"
        playwright = MagicMock()
        playwright.__enter__.return_value = browser
        with patch.object(bootstrap, "pinned_dependencies", return_value={"playwright": "1.64.1"}), \
             patch("bootstrap.version", return_value="1.64.1"), \
             patch("bootstrap.Path.is_file", return_value=False), \
             patch("bootstrap.find_browser", return_value=("chrome", "C:/Chrome/chrome.exe")), \
             patch("playwright.sync_api.sync_playwright", return_value=playwright), \
             patch("bootstrap.subprocess.check_call") as launch:
            bootstrap.main()
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0][-1], str(bootstrap.BASE_DIR / "main.py"))

    def test_invalid_pin_is_rejected(self):
        from unittest.mock import Mock
        from pathlib import Path
        original = Path.read_text

        def fake_read_text(path, **kwargs):
            if path.name == "requirements.txt":
                return "playwright>=1.64.1\n"
            return original(path, **kwargs)

        with patch.object(Path, "read_text", fake_read_text):
            with self.assertRaises(ValueError):
                bootstrap.pinned_dependencies()

    def test_blank_or_comment_lines_are_ignored(self):
        from pathlib import Path
        with patch.object(Path, "read_text", return_value="# dependencies\n\nplaywright==1.64.1 # pinned\n"):
            self.assertEqual(bootstrap.pinned_dependencies(), {"playwright": "1.64.1"})


if __name__ == "__main__":
    unittest.main()
