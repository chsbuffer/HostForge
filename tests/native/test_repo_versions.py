"""Canonical version sources agree; run with python -m unittest discover -s tests/native."""

import importlib.util
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("repo_versions", ROOT / "scripts/repo_versions.py")
repo_versions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repo_versions)


class RepoVersionsTests(unittest.TestCase):
    def test_canonical_files_agree(self):
        self.assertEqual([], repo_versions.problems(repo_versions.load()))

    def test_angle_recipe_matches_props(self):
        recipe = (ROOT / "native/angle/conanfile.py").read_text(encoding="utf-8")
        self.assertEqual(repo_versions.load()["angle"], re.search(r'^    version = "([^"]+)"', recipe, re.M).group(1))

    def test_values_follow_their_sources(self):
        with tempfile.TemporaryDirectory(prefix="hostforge-versions-test-") as temporary:
            root = Path(temporary)
            shutil.copyfile(ROOT / "Directory.Build.props", root / "Directory.Build.props")
            shutil.copytree(ROOT / "native", root / "native", ignore=shutil.ignore_patterns("__pycache__"))
            versions = repo_versions.load(root)
            runtime = versions["runtimes"]["net11.0"]

            hostlibs = root / "native/hostlibs/versions.json"
            sources = json.loads(hostlibs.read_text(encoding="utf-8"))
            sources["11.0.99"] = sources.pop(runtime)
            hostlibs.write_text(json.dumps(sources), encoding="utf-8")
            self.assertEqual(1, len(repo_versions.problems(repo_versions.load(root))))

            props = root / "Directory.Build.props"
            props.write_text(props.read_text(encoding="utf-8").replace(
                f"<HostLibsNet11Version>{runtime}<", "<HostLibsNet11Version>11.0.99<"), encoding="utf-8")
            changed = repo_versions.load(root)
            self.assertEqual([], repo_versions.problems(changed))
            self.assertEqual("11.0.99", changed["runtimes"]["net11.0"])
            self.assertEqual(versions["hostlibs"], changed["hostlibs"])

            props.write_text(props.read_text(encoding="utf-8").replace(
                f"<AvaloniaVersion>{versions['avalonia']}<", "<AvaloniaVersion>99.0.0<"), encoding="utf-8")
            self.assertEqual(1, len(repo_versions.problems(repo_versions.load(root))))
