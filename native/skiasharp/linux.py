import io
import json
import os
import sys
from pathlib import Path

from conan.errors import ConanException
from conan.tools.build import build_jobs
from conan.tools.files import replace_in_file, save


class LinuxBuild:
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
            f'ninja -C "{self._skia_build}" -j {build_jobs(self)} SkiaSharp',
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
            f'ninja -C "{self._harfbuzz_build}" -j {build_jobs(self)} HarfBuzzSharp',
            cwd=str(self._skia_root),
            env="conanbuild",
        )


    def _linux_skia_args(self):
        asmflags, cflags, ldflags = self._linux_toolchain_flags()
        cflags.extend(
            ['"-DSKIA_C_DLL"', '"-DHAVE_SYSCALL_GETRANDOM"', '"-DXML_DEV_URANDOM"']
        )
        cflags.append('"-mretpoline"' if self._target_arch == "x64" else '"-mharden-sls=all"')
        if self.options.libc == "musl" and not self._modern:
            cflags.append('"-D__WORDSIZE=64"')
        if self._modern:
            cflags.extend(['"-DSK_AVOID_SLOW_RASTER_PIPELINE_BLURS"', '"-DSK_ENABLE_LEGACY_SHADERCONTEXT"'])
        return self._gn_text(
            [
                'target_os = "linux"',
                f'target_cpu = "{self._target_arch}"',
                "skia_enable_ganesh = true",
                "skia_use_harfbuzz = false",
                "skia_use_icu = false",
                *self._version_args(),
                "skia_use_fontconfig = true",
                *(["skia_use_dng_sdk = false"] if self._modern else []),
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
                *(["skia_use_partition_alloc = false"] if self._modern else []),
                f"extra_asmflags = {self._format_gn_list(asmflags)}",
                f"extra_cflags = {self._format_gn_list(cflags)}",
                f"extra_ldflags = {self._format_gn_list(ldflags)}",
                *self._compiler_args(),
            ]
        )


    def _linux_toolchain_flags(self):
        sysroot = os.environ.get("ROOTFS_DIR")
        cpu = "x86_64" if self._target_arch == "x64" else "aarch64"
        toolchain_target = f"{cpu}-{'alpine-linux-musl' if self.options.libc == 'musl' else 'linux-gnu'}"

        initial = []
        if sysroot:
            initial.append(f'"--sysroot={sysroot}"')
            initial.append(f'"--gcc-toolchain={sysroot}/usr"')
        if toolchain_target:
            initial.append(f'"--target={toolchain_target}"')

        binary = []
        includes = []
        libraries = []
        if self._modern:
            if not sysroot or not (Path(sysroot) / "usr/include/c++/v1").is_dir():
                raise ConanException("SkiaSharp 4 requires ROOTFS_DIR with target libc++ headers and archive")
            includes.extend(['"-stdlib=libc++"', f'"-isystem{sysroot}/usr/include/c++/v1"'])

        asmflags = [*initial, *binary, *includes]
        # These x64/arm64 targets use clang's integrated assembler.
        cflags = [*initial, *binary, *includes]
        ldflags = [*initial, *binary, *libraries]
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
