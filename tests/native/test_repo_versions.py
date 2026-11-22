"""Canonical version sources agree; run with python -m unittest discover -s tests/native."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("repo_versions", ROOT / "scripts/repo_versions.py")
repo_versions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repo_versions)


class RepoVersionsTests(unittest.TestCase):
    def test_canonical_files_agree(self):
        self.assertEqual([], repo_versions.problems(repo_versions.load()))

