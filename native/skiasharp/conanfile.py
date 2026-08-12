import os
import sys
from pathlib import Path

from conan import ConanFile
from conan.errors import ConanException
from conan.tools.env import Environment
from conan.tools.files import copy, mkdir, save
from conan.tools.microsoft import VCVars
from conan.tools.scm import Git
from conan.tools.system.package_manager import Apt


class SkiaSharpConan(ConanFile):
    name = "skiasharp"
    version = "3.119.4"
    package_type = "static-library"
    license = "MIT"
    homepage = "https://github.com/mono/skia"
    description = "SkiaSharp and HarfBuzzSharp static libraries"

    settings = "os", "arch", "compiler", "build_type"
    no_copy_source = True

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
        return Path(self.build_folder) / "skia"

    @property
    def _harfbuzz_build(self):
        return Path(self.build_folder) / "harfbuzz"

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
        if self.settings.os == "Linux":
            Apt(self).install(["clang", "ninja-build"], update=True)

    def source(self):
        source = self.conan_data["sources"]
        mkdir(self, str(self._skia_root))
        git = Git(self, folder=str(self._skia_root))
        git.run("init")
        git.run(f'remote add origin "{source["url"]}"')
        git.run(f"fetch --depth 1 origin {source['commit']}")
        git.run("checkout --detach FETCH_HEAD")

        for attempt in range(1, 4):
            result = self.run(
                f'"{sys.executable}" tools/git-sync-deps',
                cwd=str(self._skia_root),
                ignore_errors=True,
            )
            if result == 0:
                break
            self.output.warning(f"git-sync-deps failed on attempt {attempt} of 3")
        else:
            raise ConanException("git-sync-deps failed after 3 attempts")

    def generate(self):
        Environment().vars(self).save_script("build_env")
        VCVars(self).generate()

    def build(self):
        self._skia_build.mkdir(parents=True, exist_ok=True)
        self._harfbuzz_build.mkdir(parents=True, exist_ok=True)
        if self.settings.os == "Windows":
            self._build_windows()
        else:
            self._build_linux()

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

    def package_info(self):
        self.cpp_info.includedirs = []
        self.cpp_info.bindirs = []
        prefix = "" if self.settings.os == "Windows" else "lib"
        self.cpp_info.libs = [
            f"{prefix}{library}" for library in self._skia_libraries
        ] + ["libHarfBuzzSharp"]

    def _build_windows(self):
        save(self, str(self._skia_build / "args.gn"), self._windows_skia_args())
        gn = self._skia_root / "bin" / "gn.exe"
        self.run(
            f'"{gn}" gen "{self._skia_build}" '
            f'--root="{self._skia_root}" '
            f'--script-executable="{sys.executable}" --nocolor',
            cwd=str(self._skia_root),
            env="conanbuild",
        )
        self.run(
            f'ninja -C "{self._skia_build}" skia SkiaSharp',
            env="conanbuild",
        )

        template = (Path(self.source_folder) / "libHarfBuzzSharp.vcxproj.in").read_text(
            encoding="utf-8-sig"
        )
        values = {
            "VC_TOOLSET_VER": str(
                self.settings.get_safe("compiler.runtime_version") or "v145"
            ),
            "WINDOWS_SDK_VER": str(
                self.conf.get("tools.microsoft:winsdk_version", default="10.0.26100.0")
            ),
            "SKIA_ROOT": str(self._skia_root),
        }
        for key, value in values.items():
            template = template.replace(f"$${key}$$", value)

        project = self._harfbuzz_build / "libHarfBuzzSharp.vcxproj"
        project.write_text(template, encoding="utf-8-sig")
        platform = "x64" if self.settings.arch == "x86_64" else "ARM64"
        self.run(
            f'msbuild "{project}" -m /p:Configuration=Release /p:Platform={platform}',
            cwd=str(self._harfbuzz_build),
            env="conanbuild",
        )

    def _build_linux(self):
        save(self, str(self._skia_build / "args.gn"), self._linux_skia_args())
        gn = self._skia_root / "bin" / "gn"
        self.run(
            f'"{gn}" gen "{self._skia_build}" '
            f'--root="{self._skia_root}" '
            f'--script-executable="{sys.executable}" --nocolor',
            cwd=str(self._skia_root),
            env="conanbuild",
        )
        self.run(
            f'ninja -C "{self._skia_build}" SkiaSharp',
            cwd=str(self._skia_root),
            env="conanbuild",
        )

        save(
            self,
            str(self._harfbuzz_build / "args.gn"),
            self._linux_harfbuzz_args(),
        )
        self.run(
            f'"{gn}" gen "{self._harfbuzz_build}" '
            f'--root="{self._skia_root}" '
            f'--script-executable="{sys.executable}" --nocolor',
            cwd=str(self._skia_root),
            env="conanbuild",
        )
        self.run(
            f'ninja -C "{self._harfbuzz_build}" HarfBuzzSharp',
            cwd=str(self._skia_root),
            env="conanbuild",
        )

    def _windows_skia_args(self):
        cflags = [
            '"-DSKIA_C_DLL"',
            '"/MT"',
            '"/EHsc"',
            '"/Z7"',
            '"/guard:cf"',
            '"-D_HAS_AUTO_PTR_ETC=1"',
            '"-D_SILENCE_ALL_CXX17_DEPRECATION_WARNINGS=1"',
        ]
        if self.settings.arch == "x86_64":
            cflags.append('"/arch:AVX2"')
        ldflags = ['"/DEBUG:FULL"', '"/DEBUGTYPE:CV,FIXUP"', '"/guard:cf"']
        return self._gn_text(
            [
                'target_os = "win"',
                f'target_cpu = "{self._target_arch}"',
                "skia_enable_fontmgr_win_gdi = false",
                "skia_use_dng_sdk = true",
                "skia_use_harfbuzz = false",
                "skia_use_icu = false",
                "skia_use_piex = true",
                "skia_use_sfntly = false",
                "skia_use_system_expat = false",
                "skia_use_system_libjpeg_turbo = false",
                "skia_use_system_libpng = false",
                "skia_use_system_libwebp = false",
                "skia_use_system_zlib = false",
                "skia_enable_skottie = true",
                "skia_use_vulkan = true",
                'clang_win = "C:/Program Files/LLVM"',
                'win_vcvars_version = "14.5"',
                "skia_enable_tools = false",
                "is_official_build = true",
                "is_static_skiasharp = true",
                f"extra_cflags = [ {', '.join(cflags)} ]",
                f"extra_ldflags = [ {', '.join(ldflags)} ]",
            ]
        )

    def _linux_skia_args(self):
        asmflags, cflags, ldflags = self._linux_toolchain_flags()
        cflags.extend(
            ['"-DSKIA_C_DLL"', '"-DHAVE_SYSCALL_GETRANDOM"', '"-DXML_DEV_URANDOM"']
        )
        return self._gn_text(
            [
                'target_os = "linux"',
                f'target_cpu = "{self._target_arch}"',
                "skia_enable_ganesh = true",
                "skia_use_harfbuzz = false",
                "skia_use_icu = false",
                "skia_use_piex = true",
                "skia_use_sfntly = false",
                "skia_use_system_expat = false",
                "skia_use_system_freetype2 = false",
                "skia_use_system_libjpeg_turbo = false",
                "skia_use_system_libpng = false",
                "skia_use_system_libwebp = false",
                "skia_use_system_zlib = false",
                "skia_enable_skottie = true",
                "skia_use_vulkan = true",
                "skia_enable_tools = false",
                "is_official_build = true",
                "is_static_skiasharp = true",
                f"extra_asmflags = {self._format_gn_list(asmflags)}",
                f"extra_cflags = {self._format_gn_list(cflags)}",
                f"extra_ldflags = {self._format_gn_list(ldflags)}",
                *self._compiler_args(),
            ]
        )

    def _linux_harfbuzz_args(self):
        asmflags, cflags, ldflags = self._linux_toolchain_flags()
        return self._gn_text(
            [
                'target_os = "linux"',
                f'target_cpu = "{self._target_arch}"',
                "is_official_build = true",
                "is_static_skiasharp = true",
                "skia_enable_tools = false",
                "visibility_hidden = false",
                f"extra_asmflags = {self._format_gn_list(asmflags)}",
                f"extra_cflags = {self._format_gn_list(cflags)}",
                f"extra_ldflags = {self._format_gn_list(ldflags)}",
                *self._compiler_args(),
            ]
        )

    def _linux_toolchain_flags(self):
        build_variant = os.environ.get("BUILD_VARIANT")
        sysroot = os.environ.get("ROOTFS_DIR")
        toolchain_arch = os.environ.get("TOOLCHAIN_ARCH")
        toolchain_target = os.environ.get("TOOLCHAIN_ARCH_TARGET")
        if not sysroot and build_variant in ("alpine", "alpinenodeps"):
            sysroot = "/alpine"

        initial = []
        if sysroot:
            initial.append(f'"--sysroot={sysroot}"')
        if toolchain_target:
            initial.append(f'"--target={toolchain_target}"')

        binary = []
        includes = []
        libraries = []
        if toolchain_arch:
            root = f"/usr/{toolchain_arch}"
            binary.append(f'"-B{root}/bin/"')
            libraries.append(f'"-L{root}/lib/"')
            includes.extend(
                [
                    f'"-I{root}/include"',
                    f'"-I{root}/include/c++/current"',
                    f'"-I{root}/include/c++/current/{toolchain_arch}"',
                ]
            )

        asmflags = [*initial, *binary, *includes]
        if asmflags:
            asmflags.insert(len(initial), '"-no-integrated-as"')
        cflags = [*initial, *binary, *includes]
        ldflags = [*initial, *binary, *libraries]
        if build_variant in ("alpine", "alpinenodeps"):
            ldflags.append('"-fuse-ld=lld"')
        return asmflags, cflags, ldflags

    @staticmethod
    def _compiler_args():
        return [
            f'{name} = "{value}"'
            for name, value in (
                ("cc", os.environ.get("CC")),
                ("cxx", os.environ.get("CXX")),
                ("ar", os.environ.get("AR")),
            )
            if value
        ]

    @staticmethod
    def _format_gn_list(values):
        return f"[ {', '.join(values)} ]" if values else "[]"

    @staticmethod
    def _gn_text(lines):
        return "\n".join(lines) + "\n"


