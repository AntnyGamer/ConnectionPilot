"""Regression checks for the isolated test runner."""
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.run_tests import run_all


class TestRunnerTests(unittest.TestCase):
    def test_clean_exit_requires_zero_status(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0)
        self.assertEqual(run_all([Path("test_sample.py")], runner=runner), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["timeout"], 120)

    def test_nonzero_exit_fails(self):
        self.assertEqual(run_all([Path("test_sample.py")], runner=lambda *_args, **_kw: SimpleNamespace(returncode=1)), 1)

    def test_hung_test_is_not_counted_as_success(self):
        def stuck(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], timeout=120)
        self.assertEqual(run_all([Path("test_sample.py")], runner=stuck), 1)
