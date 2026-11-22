"""The native recipes and deployers, without Conan; run with python -m unittest discover -s tests/native."""

import fnmatch
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


class ConanException(Exception):
    pass


def _mkdir(conanfile, path):
    os.makedirs(path, exist_ok=True)


def _rmdir(conanfile, path):
    if os.path.isdir(path):
        shutil.rmtree(path)


def _copy(conanfile, pattern, src, dst, keep_path=True, excludes=()):
    for file in Path(src).rglob("*"):
        relative = file.relative_to(src).as_posix()
        if file.is_file() and fnmatch.fnmatch(relative, pattern):
            target = Path(dst) / (relative if keep_path else file.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)


class _ConanModule(types.ModuleType):
    """A conan module whose names the code under test never calls."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})


def _load(name, path):
    """Loads a recipe or deployer with stand-ins for Conan, which these tests do not depend on."""
    modules = {module: _ConanModule(module) for module in (
        "conan", "conan.errors", "conan.tools", "conan.tools.build", "conan.tools.env", "conan.tools.files",
        "conan.tools.microsoft", "conan.tools.scm", "conan.tools.system", "conan.tools.system.package_manager")}
    modules["conan.errors"].ConanException = ConanException
    modules["conan.errors"].ConanInvalidConfiguration = type("ConanInvalidConfiguration", (ConanException,), {})
    modules["conan.tools.files"].copy = _copy
    modules["conan.tools.files"].mkdir = _mkdir
    modules["conan.tools.files"].rmdir = _rmdir
    # patch.dict also removes the sibling modules (windows, linux, macos) that a recipe imports.
    with mock.patch.dict(sys.modules, modules), mock.patch.object(sys, "path", [str(path.parent), *sys.path]):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


angle = _load("angle_recipe", ROOT / "native/angle/conanfile.py")
hostlibs = _load("hostlibs_recipe", ROOT / "native/hostlibs/conanfile.py")
skiasharp = _load("skiasharp_recipe", ROOT / "native/skiasharp/conanfile.py")
skiasharp_deploy = _load("skiasharp_deploy", ROOT / "native/skiasharp/deploy.py")


def _archive(path, members):
    """Writes an MSVC-style archive: a symbol table, then names longer than 15 bytes in a NUL-separated "//"."""
    names = b""
    entries = [("/", b"symbols")]
    for member in members:
        if len(member) > 15:
            entries.append((f"/{len(names)}", b"object"))
            names += member.encode() + b"\0"
        else:
            entries.append((member + "/", b"object"))
    entries.insert(1, ("//", names))
    with open(path, "wb") as archive:
        archive.write(b"!<arch>\n")
        for name, data in entries:
            archive.write(f"{name:<16}{0:<12}{0:<6}{0:<6}{0:<8}{len(data):<10}`\n".encode())
            archive.write(data + b"\n" * (len(data) % 2))


class AngleLicenseTests(unittest.TestCase):
    def test_archive_members_reads_short_and_long_names(self):
        members = ["obj/third_party/zlib/zlib/adler32.obj", "obj\\xxhash\\xxhash.obj", "short.obj"]
        with tempfile.TemporaryDirectory(prefix="hostforge-licenses-test-") as temporary:
            path = os.path.join(temporary, "libANGLE_static.lib")
            _archive(path, members)
            self.assertEqual(members, angle.AngleConan._archive_members(path))

    def test_archive_members_rejects_other_files(self):
        with tempfile.TemporaryDirectory(prefix="hostforge-licenses-test-") as temporary:
            path = os.path.join(temporary, "libANGLE_static.lib")
            Path(path).write_bytes(b"MZ not an archive")
            with self.assertRaises(ConanException):
                angle.AngleConan._archive_members(path)

    def test_linked_licenses_maps_members_to_components(self):
        licenses = angle.AngleConan._linked_licenses([
            "obj/libANGLE/Context.obj",
            "obj/third_party/zlib/zlib/adler32.obj",
            "obj\\xxhash\\xxhash.obj",
            "obj/src/third_party/volk/volk/volk.obj",
        ])
        self.assertEqual({"angle", "chromium", "zlib", "xxhash", "volk"}, licenses.keys())
        self.assertEqual("src/common/third_party/xxhash/LICENSE", licenses["xxhash"])

    def test_linked_licenses_rejects_unmapped_third_party_objects(self):
        with self.assertRaisesRegex(ConanException, "obj/third_party/newlib/src"):
            angle.AngleConan._linked_licenses(["obj/third_party/newlib/src/new.obj"])


class HostLibsDebugInfoTests(unittest.TestCase):
    LAUNCHER = "C:/tools/sccache.exe"
    # Blocks of a CMake Ninja build of the hosts. CMake spells the debug format /Zi (.NET 10, add_compile_options)
    # or -Zi (.NET 11, the debug format setting); ml64 has no /Z7, and the link flags are not compile flags.
    BLOCKS = {
        "header": "# CMAKE generated file: DO NOT EDIT!\ninclude CMakeFiles\\rules.ninja",
        "c": "build hostmisc\\CMakeFiles\\hostmisc.dir\\trace.c.obj: C_COMPILER__hostmisc_unscanned_Release "
             "C$:\\runtime\\src\\native\\corehost\\hostmisc\\trace.c\n"
             "  FLAGS = /DWIN32 /O1 /Zi /Zl /guard:cf\n"
             f"  LAUNCHER = {LAUNCHER}\n"
             "  OBJECT_DIR = hostmisc\\CMakeFiles\\hostmisc.dir",
        "cxx": "build hostpolicy\\CMakeFiles\\libhostpolicy.dir\\deps_entry.cpp.obj: "
               "CXX_COMPILER__libhostpolicy_unscanned_Release C$:\\runtime\\src\\native\\corehost\\deps_entry.cpp\n"
               "  FLAGS = /DWIN32 /EHsc /O1 -Zi /Zl /guard:cf\n"
               f"  LAUNCHER = {LAUNCHER}\n"
               "  OBJECT_DIR = hostpolicy\\CMakeFiles\\libhostpolicy.dir",
        "pch-creator": "build vm\\CMakeFiles\\cee_wks.dir\\common.cpp.obj: CXX_COMPILER__cee_wks_unscanned_Release "
                       "C$:\\runtime\\src\\coreclr\\vm\\common.cpp\n"
                       "  FLAGS = /DWIN32 /EHsc /O1 /Zi /Zl /Yccommon.h /Fpvm\\CMakeFiles\\cee_wks.dir\\common.pch\n"
                       f"  LAUNCHER = {LAUNCHER}\n"
                       "  OBJECT_DIR = vm\\CMakeFiles\\cee_wks.dir",
        "pch-consumer": "build vm\\CMakeFiles\\cee_wks.dir\\class.cpp.obj: CXX_COMPILER__cee_wks_unscanned_Release "
                        "C$:\\runtime\\src\\coreclr\\vm\\class.cpp\n"
                        "  FLAGS = /DWIN32 /EHsc /O1 -Zi /Zl /Yucommon.h /Fpvm\\CMakeFiles\\cee_wks.dir\\common.pch\n"
                        f"  LAUNCHER = {LAUNCHER}\n"
                        "  OBJECT_DIR = vm\\CMakeFiles\\cee_wks.dir",
        "assembler": "build vm\\CMakeFiles\\cee_wks.dir\\amd64\\thunks.asm.obj: ASM_MASM_COMPILER__cee_wks_Release "
                     "C$:\\runtime\\src\\coreclr\\vm\\amd64\\thunks.asm\n"
                     "  FLAGS = /Zi /ZH:SHA_256\n"
                     "  OBJECT_DIR = vm\\CMakeFiles\\cee_wks.dir",
        "link": "build corehost\\apphost.exe: CXX_EXECUTABLE_LINKER__apphost_Release hostmisc\\trace.c.obj\n"
                "  FLAGS = /DWIN32 /EHsc /O1 /Zi /Zl\n"
                "  LINK_FLAGS = /DEBUG /guard:cf\n"
                f"  LAUNCHER = {LAUNCHER}\n"
                "  TARGET_PDB = corehost\\apphost.pdb",
    }
    COMPILES = ("c", "cxx", "pch-creator", "pch-consumer")
    PCH = ("pch-creator", "pch-consumer")

    def rewrite(self, cache):
        with tempfile.TemporaryDirectory(prefix="hostforge-recipes-test-") as temporary:
            ninja = Path(temporary) / "build.ninja"
            ninja.write_text("\n\n".join(self.BLOCKS.values()) + "\n", encoding="utf-8")
            with mock.patch.dict(os.environ):
                os.environ.pop("HOSTFORGE_COMPILER_CACHE", None)
                if cache:
                    os.environ["HOSTFORGE_COMPILER_CACHE"] = self.LAUNCHER
                hostlibs.HostLibsConan._msvc_rewrite_compiles(Path(temporary))
            blocks = ninja.read_text(encoding="utf-8").removesuffix("\n").split("\n\n")
        self.assertEqual(len(self.BLOCKS), len(blocks))
        return dict(zip(self.BLOCKS, blocks))

    @staticmethod
    def variable(block, name):
        return re.search(rf"^  {name} =(.*)$", block, re.M).group(1).strip()

    def test_every_compile_embeds_debug_information(self):
        for cache in (False, True):
            blocks = self.rewrite(cache)
            for name in self.COMPILES:
                with self.subTest(cache=cache, block=name):
                    flags = self.variable(blocks[name], "FLAGS")
                    self.assertNotRegex(flags, r"(?:^| )[/-]Zi(?: |$)")
                    self.assertRegex(flags, r"(?:^| )[/-]Z7(?: |$)")

    def test_cached_build_compiles_precompiled_headers_directly(self):
        blocks = self.rewrite(cache=True)
        for name in self.COMPILES:
            with self.subTest(block=name):
                flags = self.variable(blocks[name], "FLAGS")
                if name in self.PCH:
                    self.assertEqual("", self.variable(blocks[name], "LAUNCHER"))
                    self.assertIn(" /Zl", flags)
                else:
                    self.assertEqual(self.LAUNCHER, self.variable(blocks[name], "LAUNCHER"))
                    self.assertIn(" -Zl", flags)
                    self.assertNotIn(" /Zl", flags)

    def test_uncached_build_keeps_the_launcher_and_zl(self):
        blocks = self.rewrite(cache=False)
        for name in self.COMPILES:
            with self.subTest(block=name):
                self.assertEqual(self.LAUNCHER, self.variable(blocks[name], "LAUNCHER"))
                self.assertIn(" /Zl", self.variable(blocks[name], "FLAGS"))
                self.assertNotIn(" -Zl", self.variable(blocks[name], "FLAGS"))

    def test_other_blocks_are_unchanged(self):
        for cache in (False, True):
            blocks = self.rewrite(cache)
            for name in self.BLOCKS.keys() - self.COMPILES:
                with self.subTest(cache=cache, block=name):
                    self.assertEqual(self.BLOCKS[name], blocks[name])


class SkiaSharpLicenseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hostforge-licenses-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.recipe = skiasharp.SkiaSharpConan()
        self.recipe.build_folder = str(self.root)

    def objects(self, *paths):
        for path in paths:
            file = self.root / "out" / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"")

    def test_externals_follow_the_object_directories(self):
        self.objects(
            "skia/obj/src/core/skia.SkCanvas.obj",
            "skia/obj/third_party/externals/libpng/png.png.obj",
            "skia/obj/third_party/externals/zlib/contrib/optimizations/zlib.inflate.obj",
            "skia/obj/third_party/vulkanmemoryallocator/vma.GrVulkanMemoryAllocator.o",
            "skia/obj/third_party/externals/piex/src/piex.piex.ninja",
            "harfbuzz/obj/x64/Release/libHarfBuzzSharp/harfbuzz.obj",
        )
        self.assertEqual({"harfbuzz", "libpng", "zlib", "vulkanmemoryallocator"}, self.recipe._compiled_externals())

    def test_harfbuzz_is_always_an_external(self):
        self.objects("skia/obj/src/core/skia.SkCanvas.o")
        self.assertEqual({"harfbuzz"}, self.recipe._compiled_externals())

    def test_unmapped_third_party_objects_fail(self):
        self.objects("skia/obj/src/third_party/newlib/new.o")
        with self.assertRaisesRegex(ConanException, "src/third_party/newlib"):
            self.recipe._compiled_externals()

    def test_externals_without_licenses_fail(self):
        self.objects("skia/obj/third_party/externals/newlib/new.o")
        with self.assertRaisesRegex(ConanException, "third_party/externals/newlib"):
            self.recipe._compiled_externals()


class SkiaSharpDeployTests(unittest.TestCase):
    def test_redeploy_replaces_the_payload_directory(self):
        with tempfile.TemporaryDirectory(prefix="hostforge-licenses-test-") as temporary:
            root = Path(temporary)
            for name in ("skia.lib", "manifest.json", "licenses/skia/LICENSE"):
                (root / "package/lib" / name).parent.mkdir(parents=True, exist_ok=True)
                (root / "package/lib" / name).write_text(name, encoding="utf-8")
            stale = root / "artifacts/1.2.3/win-x64/licenses/LICENSE"
            stale.parent.mkdir(parents=True)
            stale.write_text("earlier layout", encoding="utf-8")
            package = SimpleNamespace(ref=SimpleNamespace(name="skiasharp", version="1.2.3"),
                                      settings=SimpleNamespace(os="Windows", arch="x86_64"),
                                      package_folder=str(root / "package"))
            graph = SimpleNamespace(root=SimpleNamespace(conanfile=SimpleNamespace(dependencies={"skiasharp": package})))

            skiasharp_deploy.deploy(graph, str(root / "artifacts"))

            target = root / "artifacts/1.2.3/win-x64"
            self.assertEqual(["licenses/skia/LICENSE", "manifest.json", "skia.lib"],
                             sorted(file.relative_to(target).as_posix() for file in target.rglob("*") if file.is_file()))


if __name__ == "__main__":
    unittest.main()
