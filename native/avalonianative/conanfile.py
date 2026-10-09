import hashlib
import io
import json
import os
import shlex
from pathlib import Path

from conan import ConanFile
from conan.errors import ConanException, ConanInvalidConfiguration
from conan.tools.build import build_jobs
from conan.tools.files import copy, get, save


class AvaloniaNativeConan(ConanFile):
    name = "avalonianative"
    package_type = "static-library"
    license = "MIT"
    homepage = "https://github.com/AvaloniaUI/Avalonia"
    description = "AvaloniaNative static library for macOS NativeAOT"
    settings = "os", "arch", "compiler", "build_type"

    def set_version(self):
        self.version = self.version or "12.1.3"

    def validate(self):
        if str(self.version) not in self.conan_data["sources"]:
            raise ConanInvalidConfiguration(f"Unsupported Avalonia version: {self.version}")
        if self.settings.os != "Macos" or str(self.settings.arch) not in ("x86_64", "armv8"):
            raise ConanInvalidConfiguration("AvaloniaNative requires macOS x64 or arm64")
        if self.settings.build_type != "Release":
            raise ConanInvalidConfiguration("The NuGet payload requires a Release build")

    def source(self):
        source = self.conan_data["sources"][str(self.version)]
        get(self, source["url"], filename=f"avalonia-{self.version}.tar.gz", sha256=source["sha256"], strip_root=True)
        # Avalonia 12.1.3's native target has no submodule dependencies.

    @property
    def _apple_arch(self):
        return "arm64" if self.settings.arch == "armv8" else "x86_64"

    def build(self):
        root = Path(self.build_folder)
        native = root / "native/Avalonia.Native"
        project = native / "Avalonia.Native.macOS.proj"
        self.run(
            f"dotnet build {shlex.quote(str(project))} -t:GenerateMicroComItems "
            "-p:BuildAvaloniaNativeXcodeProject=false -c Release",
            cwd=self.build_folder,
        )
        if not (native / "inc/avalonia-native.h").is_file():
            raise ConanException("MicroCom did not generate avalonia-native.h")

        settings = [
            "ONLY_ACTIVE_ARCH=NO", "CODE_SIGNING_ALLOWED=NO", "MACH_O_TYPE=staticlib",
            "EXECUTABLE_PREFIX=lib", "EXECUTABLE_EXTENSION=a", "PRODUCT_NAME=AvaloniaNative",
            "CLANG_ENABLE_MODULES=YES", "GCC_GENERATE_DEBUGGING_SYMBOLS=NO",
            "DEBUG_INFORMATION_FORMAT=", "LLVM_LTO=NO", "MACOSX_DEPLOYMENT_TARGET=12.0",
            f"ARCHS={self._apple_arch}", f"CONFIGURATION_BUILD_DIR={root / 'out'}",
            f"OBJROOT={root / 'obj'}", f"HEADER_SEARCH_PATHS={native / 'inc'}",
        ]
        if wrapper := os.environ.get("HOSTFORGE_COMPILER_CACHE"):
            settings += [f"C_COMPILER_LAUNCHER={wrapper}", "COMPILER_INDEX_STORE_ENABLE=NO",
                         "CLANG_MODULES_BUILD_SESSION_FILE="]
            # ccache hashes the session file's mtime. Use normal module header
            # validation so an Xcode build timestamp cannot invalidate objects.
            os.environ["CCACHE_BASEDIR"] = self.build_folder
        command = [
            "xcodebuild", "-project", str(native / "src/OSX/Avalonia.Native.OSX.xcodeproj"),
            "-target", "Avalonia.Native.OSX", "-configuration", "Release", "-sdk", "macosx",
        ] + settings
        self.run(shlex.join(command + ["-showBuildSettings"]))
        self.run(shlex.join(command + ["-jobs", str(min(build_jobs(self), 4))]))
        archive = root / "out/libAvaloniaNative.a"
        if not archive.is_file():
            raise ConanException("Xcode did not produce libAvaloniaNative.a")
        if self._capture(["xcrun", "lipo", "-archs", str(archive)]) != self._apple_arch:
            raise ConanException("Archive must contain exactly the requested architecture")
        symbols = self._capture(["xcrun", "nm", "-gU", str(archive)])
        if "_CreateAvaloniaNative" not in symbols:
            raise ConanException("Archive is missing CreateAvaloniaNative")
        manifest = {
            "upstreamVersion": str(self.version),
            "sourceCommit": self.conan_data["sources"][str(self.version)]["commit"],
            "rid": "osx-arm64" if self.settings.arch == "armv8" else "osx-x64",
            "deploymentTarget": "12.0", "xcode": self._capture(["xcodebuild", "-version"]),
            "sdk": self._capture(["xcrun", "--show-sdk-version"]),
            "buildSettings": settings[:12],
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        save(self, str(root / "out/manifest.json"), json.dumps(manifest, indent=2) + "\n")

    def _capture(self, arguments):
        output = io.StringIO()
        self.run(shlex.join(arguments), stdout=output)
        return output.getvalue().strip()

    def package(self):
        for name in ("libAvaloniaNative.a", "manifest.json"):
            copy(self, name, src=str(Path(self.build_folder) / "out"), dst=str(Path(self.package_folder) / "lib"))
        copy(self, "licence.md", src=self.source_folder, dst=str(Path(self.package_folder) / "licenses"))

    def package_info(self):
        self.cpp_info.includedirs = []
        self.cpp_info.bindirs = []
        self.cpp_info.libs = ["AvaloniaNative"]
        self.cpp_info.system_libs = ["c++", "objc"]
        self.cpp_info.frameworks = [
            "AppKit", "Carbon", "CoreFoundation", "CoreGraphics", "CoreVideo", "Foundation", "IOSurface",
            "Metal", "OpenGL", "QuartzCore", "UniformTypeIdentifiers",
        ]
