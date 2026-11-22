"""Cache invalidation contracts; run with python -m unittest discover -s tests/native."""

import importlib.util
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cache_key", ROOT / "scripts/native-cache-key.py")
cache_key = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache_key)
spec = importlib.util.spec_from_file_location("repo_versions", ROOT / "scripts/repo_versions.py")
repo_versions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repo_versions)
VERSIONS = repo_versions.load()
# A second supported version of each multi-version component, so tests can change one without the other.
SKIA = VERSIONS["skiasharp"]
OTHER_SKIA = next((v for v in VERSIONS["skiasharp-versions"] if v != SKIA), None)
HOSTLIBS = VERSIONS["hostlibs"]
OTHER_HOSTLIBS = next((v for v in VERSIONS["hostlibs-versions"] if v != HOSTLIBS), None)
AVALONIA = VERSIONS["avalonia-native"]
# The RIDs whose HostLibs build in an upstream container.
HOSTLIBS_LINUX_RIDS = ("linux-x64", "linux-musl-x64", "linux-musl-arm64")


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

    def key(self, version=SKIA, rid="win-x64", component="skiasharp", toolchain="compiler-1"):
        return cache_key.fingerprint(self.root, component, version, rid, toolchain)["key"]

    def change(self, name):
        with (self.root / name).open("a", encoding="utf-8") as file:
            file.write("\n# Changed input\n")

    def test_selected_profile_and_platform_do_invalidate(self):
        original = self.key()
        self.change("native/skiasharp/windows.py")
        self.assertNotEqual(original, self.key())
        original = self.key()
        self.change("native/skiasharp/profiles/windows-x64")
        self.assertNotEqual(original, self.key())

    def test_harfbuzz_template_is_scoped_to_skiasharp_3(self):
        lines = {version.split(".")[0]: version for version in VERSIONS["skiasharp-versions"]}
        if not {"3", "4"} <= set(lines):
            self.skipTest("needs a SkiaSharp 3 and a SkiaSharp 4 version")
        before = {major: self.key(version) for major, version in lines.items()}
        linux = self.key(lines["3"], "linux-x64")
        self.change("scripts/libHarfBuzzSharp.vcxproj.in")
        self.assertNotEqual(before["3"], self.key(lines["3"]))
        self.assertEqual(before["4"], self.key(lines["4"]), "SkiaSharp 4 builds the upstream HarfBuzzSharp project")
        self.assertEqual(linux, self.key(lines["3"], "linux-x64"))

    def test_version_source_is_scoped_to_selected_version(self):
        if OTHER_SKIA is None:
            self.skipTest("needs two supported versions")
        selected, other = self.key(), self.key(OTHER_SKIA)
        file = self.root / "native/skiasharp/versions.json"
        versions = json.loads(file.read_text())
        versions[OTHER_SKIA][0] = "different-source-commit"
        file.write_text(json.dumps(versions))
        self.assertEqual(selected, self.key())
        self.assertNotEqual(other, self.key(OTHER_SKIA))

    def test_hostlibs_source_is_scoped_to_selected_runtime(self):
        if OTHER_HOSTLIBS is None:
            self.skipTest("needs two supported versions")
        selected, other = self.key(HOSTLIBS, component="hostlibs"), self.key(OTHER_HOSTLIBS, component="hostlibs")
        file = self.root / "native/hostlibs/versions.json"
        versions = json.loads(file.read_text())
        versions[OTHER_HOSTLIBS]["commit"] = "different-vmr-commit"
        file.write_text(json.dumps(versions))
        self.assertEqual(selected, self.key(HOSTLIBS, component="hostlibs"))
        self.assertNotEqual(other, self.key(OTHER_HOSTLIBS, component="hostlibs"))

    def test_avalonianative_source_is_scoped_to_selected_version(self):
        others = [version for version in VERSIONS["avalonia-sources"] if version != AVALONIA]
        if not others:
            self.skipTest("needs two Avalonia sources")
        file = self.root / "native/avalonianative/conandata.yml"
        text = file.read_text(encoding="utf-8")

        def keys():
            return {rid: self.key(AVALONIA, rid, "avalonianative") for rid in ("osx-x64", "osx-arm64")}

        def entry(version):
            return re.compile(rf'^  "{re.escape(version)}":\n(?:    .*\n)+', re.M)

        original = keys()
        file.write_text(entry(others[0]).sub("", text.replace(VERSIONS["avalonia-sources"][others[-1]]["sha256"],
                                                                "0" * 64)), encoding="utf-8")
        self.assertEqual(original, keys(), "other versions removed or changed")
        file.write_text(text.replace(VERSIONS["avalonia-sources"][AVALONIA]["sha256"], "0" * 64), encoding="utf-8")
        for rid, key in keys().items():
            self.assertNotEqual(original[rid], key, rid)
        file.write_text(text + 'patches:\n  "0.0.0":\n    - patch_file: "patches/x.patch"\n', encoding="utf-8")
        for rid, key in keys().items():
            self.assertNotEqual(original[rid], key, f"{rid}: another section")
        file.write_text(entry(AVALONIA).sub("", text), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no source"):
            keys()

    def test_hostlibs_container_is_scoped_to_selected_rid(self):
        for changed in HOSTLIBS_LINUX_RIDS:
            with self.subTest(rid=changed):
                keys = {rid: self.key(HOSTLIBS, rid, "hostlibs") for rid in (*HOSTLIBS_LINUX_RIDS, "win-x64")}
                file = self.root / "native/hostlibs/versions.json"
                versions = json.loads(file.read_text())
                versions[HOSTLIBS]["containers"][changed] += "-different"
                file.write_text(json.dumps(versions))
                self.assertNotEqual(keys.pop(changed), self.key(HOSTLIBS, changed, "hostlibs"))
                self.assertEqual(keys, {rid: self.key(HOSTLIBS, rid, "hostlibs") for rid in keys})

    def test_hostlibs_linux_image_is_shared_by_the_linux_rids(self):
        keys = {rid: self.key(HOSTLIBS, rid, "hostlibs") for rid in (*HOSTLIBS_LINUX_RIDS, "win-x64")}
        self.change("native/skiasharp/linux-x64.Dockerfile")
        self.assertEqual(keys, {rid: self.key(HOSTLIBS, rid, "hostlibs") for rid in keys})
        self.change("native/hostlibs/linux.Dockerfile")
        self.assertEqual(keys.pop("win-x64"), self.key(HOSTLIBS, "win-x64", "hostlibs"))
        for rid, key in keys.items():
            self.assertNotEqual(key, self.key(HOSTLIBS, rid, "hostlibs"), rid)

    def test_sysroot_and_checksums_are_rid_specific(self):
        checksums = {"linux-x64": "fontconfig-ubuntu18.04-amd64", "linux-arm64": "fontconfig-ubuntu18.04-arm64",
                     "linux-musl-x64": "fontconfig-alpine3.17-x86_64", "linux-musl-arm64": "fontconfig-alpine3.17-aarch64"}

        def keys():
            return {rid: (self.key(rid=rid), cache_key.rootfs_key(self.root, rid)) for rid in checksums}

        for changed, name in checksums.items():
            with self.subTest(rid=changed):
                before = keys()
                self.change(f"native/skiasharp/{name}.sha256")
                after = keys()
                self.assertNotEqual(before.pop(changed), after.pop(changed))
                self.assertEqual(before, after)

    def test_toolchain_and_relevant_task_invalidate(self):
        original = self.key()
        self.assertNotEqual(original, self.key(toolchain="compiler-2"))
        taskfile = self.root / "Taskfile.yml"
        text = taskfile.read_text(encoding="utf-8")
        text = text.replace("  _skiasharp-windows:\n", "  _skiasharp-windows:\n    # New compiler setting\n")
        taskfile.write_text(text, encoding="utf-8")
        self.assertNotEqual(original, self.key())

