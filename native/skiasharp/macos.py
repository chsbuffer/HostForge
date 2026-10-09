import io
import json
import os
import sys
from pathlib import Path

from conan.errors import ConanException
from conan.tools.build import build_jobs
from conan.tools.files import replace_in_file, save


class MacosBuild:
    def _build_macos(self):
        sdk = io.StringIO()
        self.run("xcrun --sdk macosx --show-sdk-path", stdout=sdk)
        gn = self._skia_root / "bin" / "gn"
        for output, target, args in (
            (self._skia_build, "SkiaSharp", self._macos_skia_args()),
            (self._harfbuzz_build, "HarfBuzzSharp", self._macos_harfbuzz_args()),
        ):
            # GN's SDK discovery uses xcodebuild. xcrun also supports standalone CLT.
            args += f"xcode_sysroot = {json.dumps(sdk.getvalue().strip())}\n"
            save(self, str(output / "args.gn"), args)
            self.run(
                f'"{gn}" gen "{output}" --root="{self._skia_root}" '
                f'--script-executable="{sys.executable}" --fail-on-unused-args --nocolor',
                cwd=str(self._skia_root), env="conanbuild",
            )
            for name, command in (
                ("gn-args.json", f'args "{output}" --list --json'),
                ("gn-target.json", f'desc "{output}" //:{target} --format=json'),
            ):
                evidence = io.StringIO()
                self.run(f'"{gn}" {command}', cwd=str(self._skia_root), stdout=evidence)
                save(self, str(output / name), evidence.getvalue())
            self.run(
                f'ninja -C "{output}" -j {min(build_jobs(self), 4)} {target}',
                cwd=str(self._skia_root), env="conanbuild",
            )


    def _macos_common_args(self):
        return [
            'target_os = "mac"',
            f'target_cpu = "{self._target_arch}"',
            f'min_macos_version = "{"10.13" if self._target_arch == "x64" else "11.0"}"',
            "is_official_build = true",
            "is_debug = false",
            "is_static_skiasharp = true",
            "skia_enable_tools = false",
        ]


    def _macos_skia_args(self):
        # native/macos/build.cake in the corresponding mono/SkiaSharp tag.
        cflags = ['"-DSKIA_C_DLL"', '"-DHAVE_ARC4RANDOM_BUF"', '"-stdlib=libc++"']
        if self._modern:
            cflags.extend(['"-DSK_AVOID_SLOW_RASTER_PIPELINE_BLURS"', '"-DSK_ENABLE_LEGACY_SHADERCONTEXT"'])
        return self._gn_text([
            *self._macos_common_args(),
            "skia_use_harfbuzz = false",
            "skia_use_icu = false",
            "skia_use_metal = true",
            "skia_use_gl = true",
            *self._version_args(),
            *(["skia_use_dng_sdk = false", "skia_use_piex = false"] if self._modern else []),
            "skia_use_system_expat = false",
            "skia_use_system_libjpeg_turbo = false",
            "skia_use_system_libpng = false",
            "skia_use_system_libwebp = false",
            "skia_use_system_zlib = false",
            "skia_enable_skottie = true",
            f"extra_cflags = {self._format_gn_list(cflags)}",
            'extra_ldflags = [ "-stdlib=libc++" ]',
        ])


    def _macos_harfbuzz_args(self):
        # Same harfbuzz-subset.cc and feature defines as the upstream Xcode project.
        return self._gn_text([
            *self._macos_common_args(),
            *([] if self._modern else ["visibility_hidden = false"]),
            *(["skia_use_partition_alloc = false"] if self._modern else []),
            'extra_cflags = [ "-stdlib=libc++" ]',
            'extra_ldflags = [ "-stdlib=libc++" ]',
        ])
