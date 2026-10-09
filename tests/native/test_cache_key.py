"""Cache invalidation contracts; run with python -m unittest discover -s tests/native."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cache_key", ROOT / "scripts/native-cache-key.py")
cache_key = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache_key)


class NativeCacheKeyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hostforge-cache-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(ROOT / "native", self.root / "native", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("Taskfile.yml", "pyproject.toml", "scripts/libHarfBuzzSharp.vcxproj.in"):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)

    def key(self, version="3.119.4", rid="win-x64", component="skiasharp", toolchain="compiler-1"):
        return cache_key.fingerprint(self.root, component, version, rid, toolchain)["key"]

    def change(self, name):
        with (self.root / name).open("a", encoding="utf-8") as file:
            file.write("\n# Changed input\n")

    def test_unrelated_platform_and_task_do_not_invalidate(self):
        original = self.key()
        self.change("native/skiasharp/macos.py")
        self.change("native/skiasharp/profiles/windows-arm64")
        self.change("native/avalonianative/conanfile.py")
        with (self.root / "Taskfile.yml").open("a", encoding="utf-8") as file:
            file.write("\n  unrelated-task:\n    cmds: [echo hello]\n")
        self.assertEqual(original, self.key())

    def test_selected_profile_and_platform_do_invalidate(self):
        original = self.key()
        self.change("native/skiasharp/windows.py")
        self.assertNotEqual(original, self.key())
        original = self.key()
        self.change("native/skiasharp/profiles/windows-x64")
        self.assertNotEqual(original, self.key())

    def test_version_source_is_scoped_to_selected_version(self):
        old, modern = self.key(), self.key("4.153.1")
        file = self.root / "native/skiasharp/versions.json"
        versions = json.loads(file.read_text())
        versions["4.153.1"][0] = "different-source-commit"
        file.write_text(json.dumps(versions))
        self.assertEqual(old, self.key())
        self.assertNotEqual(modern, self.key("4.153.1"))

    def test_sysroot_and_checksums_are_rid_specific(self):
        glibc, musl = self.key(rid="linux-x64"), self.key(rid="linux-musl-arm64")
        self.change("native/skiasharp/fontconfig-alpine3.17-aarch64.sha256")
        self.assertEqual(glibc, self.key(rid="linux-x64"))
        self.assertNotEqual(musl, self.key(rid="linux-musl-arm64"))

    def test_toolchain_and_relevant_task_invalidate(self):
        original = self.key()
        self.assertNotEqual(original, self.key(toolchain="compiler-2"))
        taskfile = self.root / "Taskfile.yml"
        text = taskfile.read_text(encoding="utf-8")
        text = text.replace("  _skiasharp-windows:\n", "  _skiasharp-windows:\n    # New compiler setting\n")
        taskfile.write_text(text, encoding="utf-8")
        self.assertNotEqual(original, self.key())

    def test_other_components_only_use_selected_profile(self):
        for component, version, rid, other in (
            ("angle", "2.1.27548.20260419", "win-x64", "windows-arm64"),
            ("hostlibs", "10.0.12", "win-x64", "linux-x64"),
            ("avalonianative", "12.1.3", "osx-arm64", "osx-x64"),
        ):
            with self.subTest(component=component):
                original = self.key(version, rid, component)
                self.change(f"native/{component}/profiles/{other}")
                self.assertEqual(original, self.key(version, rid, component))

    def test_generator_update_rebuilds_payload_but_can_reuse_objects(self):
        before, after = [cache_key.fingerprint(self.root, "hostlibs", "10.0.12", "win-x64",
                                              "compiler-1", generator)
                         for generator in ("cmake 3.29", "cmake 4.0")]
        self.assertNotEqual(before["key"], after["key"])
        self.assertEqual(before["compiler-prefix"], after["compiler-prefix"])
