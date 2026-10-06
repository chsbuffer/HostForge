import os
import sys

from conan import ConanFile
from conan.tools.env import Environment
from conan.tools.files import (
    apply_conandata_patches,
    copy,
    get,
    load,
    save,
)
from conan.tools.microsoft import VCVars


class AngleConan(ConanFile):
    name = "angle"
    version = "2.1.27548.20260419"
    package_type = "static-library"
    license = "BSD-3-Clause"
    homepage = "https://github.com/google/angle"
    description = "ANGLE complete static libraries"

    settings = "os", "arch", "compiler", "build_type"
    exports_sources = "patches/*"
    no_copy_source = True

    _gn_cpu = {
        "x86_64": "x64",
        "armv8": "arm64",
    }
    _libraries = (
        "libANGLE_static.lib",
        "libGLESv2_static.lib",
    )

    @property
    def _angle_root(self):
        return os.path.join(self.source_folder, "angle")

    def source(self):
        source = self.conan_data["sources"]
        get(
            self,
            f"{source['url']}/archive/refs/heads/{source['commit']}.tar.gz",
            destination=self._angle_root,
            strip_root=True,
        )
        apply_conandata_patches(self)

    def generate(self):
        self._environment().vars(self).save_script("angle")
        VCVars(self).generate()

    def build(self):
        env = self._environment()
        with env.vars(self).apply():
            self.run(
                f'"{sys.executable}" scripts/bootstrap.py',
                cwd=self._angle_root,
            )
            self.run(
                "gclient sync --no-history --shallow",
                cwd=self._angle_root,
            )

        gn_args = [
            "is_debug=false",
            "is_component_build=false",
            "is_clang=false",
            "angle_is_msvc=true",
            "symbol_level=0",
            "use_custom_libcxx=false",
            "use_lld=false",
            "use_thin_lto=false",
            f'target_cpu="{self._gn_cpu[str(self.settings.arch)]}"',
            "dcheck_always_on=false",
            "angle_enable_vulkan=false",
            "angle_enable_gl=false",
            "angle_enable_null=false",
            "angle_assert_always_on=false",
            "angle_enable_d3d9=false",
            "angle_enable_metal=false",
            "angle_enable_gl_desktop_backend=false",
            "angle_enable_wgpu=false",
            "angle_enable_swiftshader=false",
            "angle_build_tests=false",
            "build_angle_deqp_tests=false",
        ]
        save(
            self, os.path.join(self.build_folder, "args.gn"), "\n".join(gn_args) + "\n"
        )

        gn = os.path.join(self._angle_root, "buildtools", "win", "gn.exe")
        ninja = os.path.join(self._angle_root, "third_party", "ninja", "ninja.exe")
        self.run(
            f'"{gn}" gen "{self.build_folder}" --root="{self._angle_root}" --nocolor',
            env="conanbuild",
        )
        self.run(
            f'"{ninja}" -C "{self.build_folder}" libANGLE_static libGLESv2_static',
            env="conanbuild",
        )

    def package(self):
        object_dir = os.path.join(self.build_folder, "obj")
        for library in self._libraries:
            copy(
                self,
                library,
                src=object_dir,
                dst=os.path.join(self.package_folder, "lib"),
                keep_path=False,
            )

        definition = load(
            self,
            os.path.join(
                self._angle_root,
                "src",
                "libGLESv2",
                "libGLESv2_autogen.def",
            ),
        )
        exports = "\n".join(
            line
            for line in definition.splitlines()
            if not line.strip().upper().startswith("LIBRARY ")
        )
        save(
            self,
            os.path.join(self.package_folder, "res", "av_libglesv2.def"),
            exports + "\n",
        )

    def package_info(self):
        self.cpp_info.includedirs = []
        self.cpp_info.bindirs = []
        self.cpp_info.libs = ["libGLESv2_static", "libANGLE_static"]
        self.cpp_info.system_libs = [
            "advapi32",
            "d3d11",
            "dxgi",
            "dxguid",
            "gdi32",
            "setupapi",
            "synchronization",
            "user32",
        ]

    @staticmethod
    def _environment():
        env = Environment()
        env.define("DEPOT_TOOLS_WIN_TOOLCHAIN", "0")
        return env
