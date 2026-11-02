"""Make dependency pin updates safe and synchronized with the launcher."""
import unittest
from unittest.mock import patch

import bootstrap


class DependencyTests(unittest.TestCase):
    def test_pins_match_requirements_file(self):
        self.assertEqual(bootstrap.pinned_dependencies(), {
            "playwright": "1.64.1",
            "tzdata": "2026.6",
        })

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
