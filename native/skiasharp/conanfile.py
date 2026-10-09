import hashlib
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path

from conan import ConanFile
from conan.errors import ConanException, ConanInvalidConfiguration
from conan.tools.env import Environment
from conan.tools.files import copy, download, get, save
from conan.tools.microsoft import VCVars
from conan.tools.system.package_manager import Apt

from windows import WindowsBuild
from linux import LinuxBuild
from macos import MacosBuild

VERSIONS = json.loads((Path(__file__).parent / "versions.json").read_text())

class SkiaSharpConan(WindowsBuild, LinuxBuild, MacosBuild, ConanFile):
    name = "skiasharp"
    package_type = "static-library"
    license = "MIT"
    homepage = "https://github.com/mono/skia"
    description = "SkiaSharp and HarfBuzzSharp static libraries"

    settings = "os", "arch", "compiler", "build_type"
    options = {"libc": ["glibc", "musl"]}
    default_options = {"libc": "glibc"}
    # Build beside an isolated source copy: GN then emits stable relative paths
    # instead of Conan's revision-dependent ../skias<hash>/s paths.
    # https://github.com/google/skia/blob/chrome/m150/tools/git-sync-deps
    exports_sources = "git-sync-deps"
    exports = "windows.py", "linux.py", "macos.py", "versions.json"

    def set_version(self):
        self.version = self.version or "3.119.4"

    def config_options(self):
        if self.settings.os != "Linux":
            self.options.rm_safe("libc")

    def validate(self):
        if str(self.version) not in VERSIONS:
            raise ConanInvalidConfiguration(f"Unsupported SkiaSharp version: {self.version}")
        if str(self.settings.os) not in ("Windows", "Linux", "Macos"):
            raise ConanInvalidConfiguration("This recipe supports Windows, Linux and macOS")
        if str(self.settings.arch) not in self._arch:
            raise ConanInvalidConfiguration("Only x64 and arm64 are supported")

    @property
    def _modern(self):
        return str(self.version).startswith("4.")

    _arch = {
        "x86_64": "x64",
        "armv8": "arm64",
    }
    _skia_libraries = (
        "SkiaSharp",
        "skia",
        "skottie",
        "sksg",
        "skshaper",
        "skresources",
    )

    @property
    def _skia_root(self):
        return Path(self.source_folder) / "skia"

    @property
    def _skia_build(self):
        return Path(self.build_folder) / "out/skia"

    @property
    def _harfbuzz_build(self):
        return Path(self.build_folder) / "out/harfbuzz"

    @property
    def _target_arch(self):
        return self._arch[str(self.settings.arch)]

    def export_sources(self):
        copy(
            self,
            "libHarfBuzzSharp.vcxproj.in",
            src=str(Path(self.recipe_folder).parent.parent / "scripts"),
            dst=self.export_sources_folder,
        )

    def system_requirements(self):
        if self.settings.os == "Linux" and shutil.which("apt-get"):
            Apt(self).install(["clang", "ninja-build"], update=True)

    def source(self):
        get(
            self,
            f"https://github.com/mono/skia/archive/{VERSIONS[str(self.version)][0]}.tar.gz",
            destination=str(self._skia_root),
            strip_root=True,
            keep_permissions=True,
        )
        copy(
            self,
            "git-sync-deps",
            src=self.export_sources_folder,
            dst=str(self._skia_root / "tools"),
            overwrite_equal=True,
        )

        os.environ["GIT_SYNC_DEPS_SKIP_EMSDK"] = "1"
        for attempt in range(3):
            result = self.run(
                f'"{sys.executable}" tools/git-sync-deps',
                cwd=str(self._skia_root),
                ignore_errors=True,
            )
            if result == 0:
                break
            if attempt < 2:
                # Already checked-out commits are reused by git-sync-deps.
                self.output.warning("Dependency download failed; retrying source synchronization")
                time.sleep(3 * (attempt + 1))
        else:
            raise ConanException("git-sync-deps failed")
        if self._modern:
            upstream = f"https://raw.githubusercontent.com/mono/SkiaSharp/v{self.version}/native/windows/libHarfBuzzSharp"
            for name in ("libHarfBuzzSharp.vcxproj", "harfbuzz-subset-msvc.cc"):
                download(self, f"{upstream}/{name}", str(Path(self.source_folder) / name))

    def generate(self):
        env = Environment()
        env.define("SCCACHE_BASEDIRS", self.build_folder)
        env.define("SCCACHE_DIRECT", "false")
        env.vars(self).save_script("build_env")
        if self.settings.os == "Windows":
            VCVars(self).generate()

    def build(self):
        self._skia_build.mkdir(parents=True, exist_ok=True)
        self._harfbuzz_build.mkdir(parents=True, exist_ok=True)
        if self.settings.os == "Windows":
            self._build_windows()
        elif self.settings.os == "Macos":
            self._build_macos()
        else:
            self._build_linux()
        versions = io.StringIO()
        if self.settings.os == "Windows":
            clang = Path(os.environ.get("LLVM_HOME", "C:/Program Files/LLVM")) / "bin/clang-cl.exe"
            self.run(f'"{clang}" --version', stdout=versions, env="conanbuild")
            self.run("cl", stdout=versions, stderr=versions, ignore_errors=True, env="conanbuild")
        else:
            self.run("clang --version", stdout=versions, env="conanbuild")
            if self.settings.os == "Macos":
                self.run("xcrun --sdk macosx --show-sdk-version", stdout=versions)
        save(self, str(Path(self.build_folder) / "toolchain.txt"), versions.getvalue())

    def package(self):
        extension = ".lib" if self.settings.os == "Windows" else ".a"
        prefix = "" if self.settings.os == "Windows" else "lib"
        for library in self._skia_libraries:
            name = f"{prefix}{library}{extension}"
            copy(
                self,
                name,
                src=str(self._skia_build),
                dst=str(Path(self.package_folder) / "lib"),
                keep_path=False,
            )

        if self.settings.os == "Windows":
            platform = "x64" if self.settings.arch == "x86_64" else "ARM64"
            source = self._harfbuzz_build / "bin" / platform / "Release"
            name = "libHarfBuzzSharp.lib"
        else:
            source = self._harfbuzz_build
            name = "libHarfBuzzSharp.a"
        copy(
            self,
            name,
            src=str(source),
            dst=str(Path(self.package_folder) / "lib"),
            keep_path=False,
        )

        output = Path(self.package_folder) / "lib"
        for name in (f"{prefix}SkiaSharp{extension}", f"libHarfBuzzSharp{extension}"):
            if not (output / name).is_file():
                raise ConanException(f"Required archive was not produced: {name}")
        if self._modern and self.settings.os == "Linux":
            root = Path(os.environ["ROOTFS_DIR"])
            candidates = list((root / "usr/lib").glob("**/libc++.a")) + list((root / "usr/lib64").glob("libc++.a"))
            if len(candidates) != 1:
                raise ConanException(f"Expected one target libc++.a, found: {candidates}")
            copy(self, "libc++.a", src=str(candidates[0].parent), dst=str(output / "cxx"))
        copy(self, "args.gn", src=str(self._skia_build), dst=str(output))
        copy(self, "args.gn", src=str(self._harfbuzz_build), dst=str(output / "harfbuzz"))
        if self.settings.os == "Macos":
            copy(self, "gn-*.json", src=str(self._skia_build), dst=str(output))
            copy(self, "gn-*.json", src=str(self._harfbuzz_build), dst=str(output / "harfbuzz"))
        copy(self, "toolchain.txt", src=self.build_folder, dst=str(output), keep_path=False)
        copy(self, "LICENSE", src=str(self._skia_root), dst=str(output / "licenses"), keep_path=True)
        copy(self, "COPYING", src=str(self._skia_root / "third_party/externals/harfbuzz"), dst=str(output / "licenses/harfbuzz"))
        hashes = {}
        for file in sorted(output.glob("**/*")):
            if file.is_file():
                with file.open("rb") as stream:
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                hashes[file.relative_to(output).as_posix()] = digest.hexdigest()
        manifest = {"skiaSharp": str(self.version), "harfBuzzSharp": VERSIONS[str(self.version)][1],
                    "sourceCommit": VERSIONS[str(self.version)][0], "arch": self._target_arch,
                    "compiler": str(self.settings.compiler), "compilerVersion": str(self.settings.compiler.version), "sha256": hashes}
        if self.settings.os == "Linux":
            manifest["libc"] = str(self.options.libc)
        save(self, str(output / "manifest.json"), json.dumps(manifest, indent=2) + "\n")

    def package_info(self):
        self.cpp_info.includedirs = []
        self.cpp_info.bindirs = []
        prefix = "" if self.settings.os == "Windows" else "lib"
        self.cpp_info.libs = [
            f"{prefix}{library}" for library in self._skia_libraries
        ] + ["libHarfBuzzSharp"]

    def _version_args(self):
        if self._modern:
            return ["skia_use_partition_alloc = false", "skia_enable_graphite = true"]
        return ["skia_use_piex = true", "skia_use_sfntly = false"]

    @staticmethod
    def _format_gn_list(values):
        return f"[ {', '.join(values)} ]" if values else "[]"

    @staticmethod
    def _gn_text(lines):
        if wrapper := os.environ.get("HOSTFORGE_COMPILER_CACHE"):
            lines = [*lines, f"cc_wrapper = {json.dumps(wrapper.replace(chr(92), '/'))}"]
        return "\n".join(lines) + "\n"
