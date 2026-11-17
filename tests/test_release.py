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
        self.assertIn('Verify public release and all required uploaded assets', workflow)
        self.assertIn('gh release view "$TAG" --json tagName,isDraft,isPrerelease,assets,url', workflow)
        self.assertIn('THIRD-PARTY-NOTICES.txt; do', workflow)
        self.assertIn('Published GitHub release is missing $required', workflow)
        self.assertIn('gh api "repos/$GH_REPO/git/ref/tags/$TAG"', workflow)
        self.assertLess(workflow.index('gh api "repos/$GH_REPO/git/ref/tags/$TAG"'),
                        workflow.index('gh release create'))
        self.assertIn('--target "$GITHUB_SHA"', workflow)
        self.assertIn('--latest', workflow)
        self.assertIn('gh release view "$TAG"', workflow)
        self.assertIn('Keeping the immutable release unchanged', workflow)
        self.assertIn('exit 0', workflow)

    def test_version_matches_app_exe_metadata_workflows_and_release(self):
        """A future bump must update every version-bearing release surface."""
        import re
        from main import APP_VERSION

        version = APP_VERSION
        self.assertRegex(version, r"^v[0-9]+\.[0-9]+\.[0-9]+$")
        major, minor, patch = (int(v) for v in version[1:].split("."))
        windows_version = f"{major}.{minor}.{patch}.0"
        windows_tuple = f"({major}, {minor}, {patch}, 0)"

        metadata = (ROOT / "assets/connectionpilot_version.txt").read_text()
        windows = (ROOT / ".github/workflows/windows-build.yml").read_text()
        tests = (ROOT / ".github/workflows/tests.yml").read_text()
        readme = (ROOT / "README.md").read_text()
        builder = (ROOT / "scripts/build_windows.py").read_text()

        self.assertIn(f"filevers={windows_tuple}", metadata)
        self.assertIn(f"prodvers={windows_tuple}", metadata)
        self.assertEqual(metadata.count(f"'{windows_version}'"), 2)
        self.assertIn(f"FileVersion -ne '{windows_version}'", windows)
        self.assertIn(f"^PASS {version}:", windows)
        self.assertIn(f"ConnectionPilot-{version}-Windows-EXE", windows)
        self.assertEqual(windows.count(f'TAG="{version}"'), 2)
        self.assertIn(f'--title "ConnectionPilot {version}"', windows)
        self.assertIn(f"ConnectionPilot {version} for Windows", windows)
        self.assertIn(f"Test ConnectionPilot {version}", tests)
        self.assertIn(f"Build branded ConnectionPilot {version}", windows)
        self.assertIn(f"Build the ConnectionPilot {version}", builder)
        self.assertIn(f"# ConnectionPilot {version}", readme)
        self.assertIn(f"**Version: {version}.**", readme)
        self.assertIn(f"ConnectionPilot-{version}-Windows-EXE", readme)
        # Versioned workflow metadata must never point at an older/newer
        # release while producing an EXE bearing a different version.
        self.assertEqual(set(re.findall(r"v\d+\.\d+\.\d+", windows)), {version})
        self.assertEqual(set(re.findall(r"v\d+\.\d+\.\d+", tests)), {version})

    def test_readme_documents_current_no_retry_safety_behavior(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("for the rest of the current run", readme)
        self.assertIn("HTTP 401, 403, 429 and 999", readme)
        self.assertIn("before pausing", readme)
        self.assertNotIn("Empty search pages are retried twice", readme)
        self.assertNotIn("page is retained for another inspection", readme)
        self.assertNotIn("that page remains pending", readme)

    def test_exe_smoke_validation_blocks_artifact_publication(self):
        workflow = (ROOT / '.github/workflows/windows-build.yml').read_text()
        builder = (ROOT / 'scripts/build_windows.py').read_text()
        app = (ROOT / 'main.py').read_text()
        self.assertIn('Smoke-test the actual packaged EXE offline', workflow)
        self.assertIn('PLAYWRIGHT_BROWSERS_PATH', workflow)
        self.assertIn("The browser uses a separate TemporaryDirectory", workflow)
        self.assertNotIn("$env:USERPROFILE =", workflow)
        self.assertIn('--smoke-test', workflow)
        self.assertLess(workflow.index('Smoke-test the actual packaged EXE offline'),
                        workflow.index('Upload Windows EXE'))
        self.assertIn('"--collect-data", "playwright"', builder)
        self.assertIn('"--collect-binaries", "playwright"', builder)
        self.assertNotIn('"--collect-all", "playwright"', builder)
        self.assertIn('"--collect-data", "tzdata"', builder)
        self.assertNotIn('"--collect-all", "tzdata"', builder)
        self.assertIn('def smoke_test_exe(result_path):', app)
        self.assertIn('page.goto("about:blank", timeout=10000)', app)
        self.assertIn('prepare_external_processes()', app)
        process_env = (ROOT / 'process_env.py').read_text()
        self.assertIn('SetDllDirectoryW(None)', process_env)

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
