import io
import json
import os
import re
import sys
from pathlib import Path

from conan.errors import ConanException
from conan.tools.build import build_jobs
from conan.tools.files import replace_in_file, save


class WindowsBuild:
    def _build_windows(self):
        if not self._modern:
            # Conan's VCVars already selected this architecture/SDK. m119 runs
            # vcvarsall again for every object; m153 already leaves this empty.
            toolchain = self._skia_root / "gn/toolchain/BUILD.gn"
            content = toolchain.read_text(encoding="utf-8")
            content, count = re.subn(
                r'^  if \(target_cpu == "x64"\) \{\n    _target = "amd64".*?^  env_setup = "cmd /c [^\n]*',
                '  env_setup = ""', content, flags=re.M | re.S)
            if count != 1:
                raise ConanException("Unexpected m119 Windows environment setup")
            toolchain.write_text(content, encoding="utf-8")
        if not self._modern and '#include <string>' not in (self._skia_root / "src/gpu/ganesh/d3d/GrD3DUtil.h").read_text():
            # m119 relied on an STL transitive include removed by recent MSVC.
            replace_in_file(self, str(self._skia_root / "src/gpu/ganesh/d3d/GrD3DUtil.h"),
                            '#include "include/core/SkImage.h"',
                            '#include <string>\n#include "include/core/SkImage.h"', strict=False)
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
            f'ninja -C "{self._skia_build}" -j {build_jobs(self)} skia SkiaSharp',
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

        if self._modern:
            template = (Path(self.source_folder) / "libHarfBuzzSharp.vcxproj").read_text(encoding="utf-8-sig")
            template = template.replace("..\\..\\..\\externals\\skia", str(self._skia_root))
            template = template.replace("DynamicLibrary", "StaticLibrary")
            template = template.replace("<WholeProgramOptimization>true", "<WholeProgramOptimization>false")
            template = template.replace("<ClCompile>", "<ClCompile><AdditionalOptions>/bigobj %(AdditionalOptions)</AdditionalOptions>")
            template = template.replace("v142", values["VC_TOOLSET_VER"])
            template = template.replace("10.0.19041.0", values["WINDOWS_SDK_VER"])
            wrapper = (Path(self.source_folder) / "harfbuzz-subset-msvc.cc").read_text()
            wrapper = wrapper.replace("../../../externals/skia", self._skia_root.as_posix())
            save(self, str(self._harfbuzz_build / "harfbuzz-subset-msvc.cc"), wrapper)
        template = template.replace("$(SolutionDir)", "$(ProjectDir)")
        # A NuGet consumer cannot access the build machine's compiler PDB (/Zi).
        # Embed CodeView types in the archive's objects (/Z7) for portable linking.
        template = template.replace("</ClCompile>", "<DebugInformationFormat>OldStyle</DebugInformationFormat></ClCompile>")
        project = self._harfbuzz_build / "libHarfBuzzSharp.vcxproj"
        if wrapper := os.environ.get("HOSTFORGE_COMPILER_CACHE"):
            launcher = self._harfbuzz_build / "cached-cl.cmd"
            launcher.write_text(f'@"{wrapper}" cl.exe %*\n', newline="\r\n")
            template = template.replace('<Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets"',
                '<PropertyGroup><CLToolExe>cached-cl.cmd</CLToolExe>'
                '<CLToolPath>$(ProjectDir)</CLToolPath><TrackFileAccess>false</TrackFileAccess>'
                '</PropertyGroup><Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets"')
        project.write_text(template, encoding="utf-8-sig")
        platform = "x64" if self.settings.arch == "x86_64" else "ARM64"
        self.run(
            f'msbuild "{project}" -m /p:Configuration=Release /p:Platform={platform}',
            cwd=str(self._harfbuzz_build),
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
        if self._modern:
            cflags.extend(['"-DSK_AVOID_SLOW_RASTER_PIPELINE_BLURS"', '"-DSK_ENABLE_LEGACY_SHADERCONTEXT"'])
        ldflags = ['"/DEBUG:FULL"', '"/DEBUGTYPE:CV,FIXUP"', '"/guard:cf"']
        return self._gn_text(
            [
                'target_os = "win"',
                f'target_cpu = "{self._target_arch}"',
                "skia_enable_fontmgr_win_gdi = false",
                *([] if self._modern else ["skia_use_dng_sdk = true"]),
                "skia_use_harfbuzz = false",
                "skia_use_icu = false",
                *self._version_args(),
                "skia_use_system_expat = false",
                "skia_use_system_libjpeg_turbo = false",
                "skia_use_system_libpng = false",
                "skia_use_system_libwebp = false",
                "skia_use_system_zlib = false",
                "skia_enable_skottie = true",
                "skia_use_vulkan = true",
                "skia_use_direct3d = true",
                *(["skia_use_system_freetype2 = false", "skia_use_freetype = false", "skia_enable_fontmgr_custom_empty = false", "skia_enable_fontmgr_win = true"] if self._modern else []),
                f'clang_win = {json.dumps(os.environ.get("LLVM_HOME", "C:/Program Files/LLVM").replace(chr(92), "/"))}',
                'win_vcvars_version = "14.5"',
                "skia_enable_tools = false",
                "is_official_build = true",
                "is_static_skiasharp = true",
                f"extra_cflags = [ {', '.join(cflags)} ]",
                f"extra_ldflags = [ {', '.join(ldflags)} ]",
            ]
        )
