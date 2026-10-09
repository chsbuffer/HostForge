import os
import re
import shutil
from pathlib import Path

from conan import ConanFile
from conan.tools.build import build_jobs
from conan.errors import ConanException
from conan.tools.env import Environment
from conan.tools.files import get, save
from conan.tools.microsoft import VCVars
from conan.tools.system.package_manager import Apt

VERSION = "10.0.12"
SOURCE_COMMIT = "4271d88e0aebf3d04f188f1334c2220d80555ef6"  # dotnet/runtime v10.0.12

class HostLibsConan(ConanFile):
    name = "hostlibs"
    version = VERSION
    package_type = "static-library"
    license = "MIT"
    homepage = "https://github.com/dotnet/runtime"
    description = ".NET apphost and single-file host static link inputs"

    settings = "os", "arch", "compiler", "build_type"
    options = {"pgo": [True, False]}
    default_options = {"pgo": True}

    _arch = {
        "x86_64": "x64",
        "armv8": "arm64",
    }

    @property
    def _runtime_root(self):
        return Path(self.source_folder) / "runtime"

    @property
    def _target_arch(self):
        return self._arch[str(self.settings.arch)]

    @property
    def _rid(self):
        prefix = "win" if self.settings.os == "Windows" else "linux"
        return f"{prefix}-{self._target_arch}"

    def system_requirements(self):
        if self.settings.os == "Linux":
            Apt(self).install(
                [
                    "build-essential",
                    "gettext",
                    "locales",
                    "cmake",
                    "llvm",
                    "clang",
                    "lld",
                    "lldb",
                    "liblldb-dev",
                    "libunwind8-dev",
                    "libicu-dev",
                    "liblttng-ust-dev",
                    "libssl-dev",
                    "libkrb5-dev",
                    "pigz",
                    "cpio",
                ],
                update=True,
            )

    def source(self):
        get(
            self,
            f"https://github.com/dotnet/runtime/archive/{SOURCE_COMMIT}.tar.gz",
            destination=str(self._runtime_root),
            strip_root=True,
            keep_permissions=True,
        )

    def generate(self):
        env = Environment()
        if wrapper := os.environ.get("HOSTFORGE_COMPILER_CACHE"):
            env.define("CMAKE_C_COMPILER_LAUNCHER", wrapper.replace("\\", "/"))
            env.define("CMAKE_CXX_COMPILER_LAUNCHER", wrapper.replace("\\", "/"))
            env.define("SCCACHE_BASEDIRS", self.build_folder)
            env.define("SCCACHE_DIRECT", "false")
        env.vars(self).save_script("build_env")
        VCVars(self).generate()

    def build(self):
        self._build_apphost()
        self._build_singlefilehost()

    def package(self):
        self._bundle("apphost", self._apphost_build_root())
        self._bundle("singlefilehost", self._singlefilehost_build_root())

        if self.settings.os == "Windows":
            source = (
                self._runtime_root
                / "src"
                / "native"
                / "corehost"
                / "apphost"
                / "static"
                / "singlefilehost.def"
            )
            shutil.copy2(source, Path(self.package_folder) / source.name)
        else:
            source = (
                self._singlefilehost_build_root()
                / "Corehost.Static"
                / "singlefilehost.exports"
            )
            shutil.copy2(source, Path(self.package_folder) / source.name)

    def package_info(self):
        self.cpp_info.includedirs = []
        self.cpp_info.libdirs = []
        self.cpp_info.bindirs = []

    def _build_apphost(self):
        root = self._apphost_build_root()
        if self.settings.os == "Windows":
            pre = ""
            if not self.options.pgo:
                src = self._runtime_root / "src" / "native" / "corehost"
                pre = f'cmake -S "{src}" -B "{root}" -DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF'
            self._msvc_configure_build("host.native", "apphost", root,
                                       extra_args="/p:ConfigureOnly=true", pre_ninja=pre)
        else:
            cross = " --cross" if self._sysroot() else ""
            self.run(
                f'"{self._runtime_root / "build.sh"}" host.native -ninja '
                f"-c release -arch {self._target_arch}{cross} -p:ConfigureOnly=true "
                f"-p:SourceRevisionId={SOURCE_COMMIT}",
                cwd=str(self._runtime_root),
                env="conanbuild",
            )
            self._disable_clang_pch_cache(root)
            target = self._link_inputs_target(root, "apphost")
            self.run(f'ninja -C "{root}" -j {build_jobs(self)} {target}', env="conanbuild")

    def _build_singlefilehost(self):
        root = self._singlefilehost_build_root()
        if self.settings.os == "Windows":
            extra = "/p:ConfigureOnly=true"
            if not self.options.pgo:
                extra += " /p:NoPgoOptimize=true"
            self._msvc_configure_build("clr.runtime", "singlefilehost", root,
                                       extra_args=extra)
        else:
            cross = " --cross" if self._sysroot() else ""
            self.run(
                f'"{self._runtime_root / "build.sh"}" clr.runtime -ninja '
                f"-c release -arch {self._target_arch}{cross} -p:ConfigureOnly=true "
                f"-p:SourceRevisionId={SOURCE_COMMIT}",
                cwd=str(self._runtime_root),
                env="conanbuild",
            )
            self._disable_clang_pch_cache(root)
            target = self._link_inputs_target(root, "singlefilehost")
            self.run(f'ninja -C "{root}" -j {build_jobs(self)} {target}', env="conanbuild")

    @staticmethod
    def _link_inputs_target(build_root, name):
        # The package contains link inputs, not Runtime's final executable.
        # Keep every explicit, implicit and order-only dependency of that link,
        # including generated export lists, without performing an unused link.
        ninja = build_root / "build.ninja"
        text = ninja.read_text(encoding="utf-8")
        marker = f": CXX_EXECUTABLE_LINKER__{name}_"
        for line in text.splitlines():
            if line.startswith("build ") and marker in line:
                dependencies = line.split(": ", 1)[1].split(" ", 1)[1]
                target = f"hostforge-{name}-inputs"
                ninja.write_text(text + f"\nbuild {target}: phony {dependencies}\n", encoding="utf-8")
                return target
        raise ConanException(f"Ninja link inputs not found for {name}")

    def _disable_clang_pch_cache(self, build_root):
        if not os.environ.get("HOSTFORGE_COMPILER_CACHE"):
            return
        # Clang PCH files embed absolute header paths. Recreate them in each
        # Conan directory; their consumers can still reuse cached objects.
        ninja = build_root / "build.ninja"
        blocks = ninja.read_text(encoding="utf-8").split("\n\n")
        for index, block in enumerate(blocks):
            if re.search(r"^  FLAGS = .* -emit-pch(?: |$)", block, re.M):
                blocks[index], count = re.subn(r"^  LAUNCHER =.*$", "  LAUNCHER = ", block, flags=re.M)
                if count != 1:
                    raise ConanException("Unexpected CMake launcher rule for Clang PCH")
        ninja.write_text("\n\n".join(blocks), encoding="utf-8")

    def _msvc_configure_build(self, subset, target, build_root, extra_args="", pre_ninja=""):
        root = str(self._runtime_root)
        cmd = (f'"{root}\\build.cmd" {subset} -ninja -c release -arch {self._target_arch} '
               f'/p:SourceRevisionId={SOURCE_COMMIT} {extra_args}{self._cmake_args()}')
        if pre_ninja:
            cmd += f" && {pre_ninja}"
        self.run(cmd, cwd=root, env="conanbuild")
        if os.environ.get("HOSTFORGE_COMPILER_CACHE"):
            # MSVC PCH consumers cannot be cached by sccache. Preserve their
            # PCH acceleration and shared PDB instead of expanding headers and
            # debug types into every object. Cache only independent objects.
            ninja = build_root / "build.ninja"
            blocks = ninja.read_text(encoding="utf-8").split("\n\n")
            for index, block in enumerate(blocks):
                if re.search(r"^  FLAGS = .* /Y[cu]", block, re.M):
                    blocks[index] = re.sub(r"^  LAUNCHER =.*$", "  LAUNCHER = ", block, flags=re.M)
                elif re.search(r"^build .*: (?:C|CXX)_COMPILER__", block, re.M):
                    # Use per-object CodeView and the equivalent dash spelling
                    # of /Zl, which sccache otherwise parses as an input path.
                    blocks[index] = re.sub(r"^  FLAGS =.*$", lambda match:
                                          match.group().replace(" /Zi", " /Z7").replace(" /Zl", " -Zl"),
                                          block, flags=re.M)
            ninja.write_text("\n\n".join(blocks), encoding="utf-8")
        target = self._link_inputs_target(build_root, target)
        cmd = ""
        if self._target_arch == "arm64":
            cmd = f'call "{root}\\eng\\native\\init-vs-env.cmd" arm64 && '
        cmd += f'ninja -C "{build_root}" -j {build_jobs(self)} {target}'
        self.run(cmd, cwd=root, env="conanbuild")

    def _cmake_args(self):
        args = []
        if self.settings.os == "Windows" and not self.options.pgo:
            args.append("-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF")
        prefix = "/" if self.settings.os == "Windows" else "-"
        return f' {prefix}p:CMakeArgs="{" ".join(args)}"' if args else ""

    def _apphost_build_root(self):
        artifacts = self._runtime_root / "artifacts" / "obj"
        if self.settings.os == "Windows":
            return artifacts / f"{self._rid}.Release" / "corehost"
        return artifacts / f"{self._rid}.Release"

    def _singlefilehost_build_root(self):
        os_name = "windows" if self.settings.os == "Windows" else "linux"
        return (
            self._runtime_root
            / "artifacts"
            / "obj"
            / "coreclr"
            / f"{os_name}.{self._target_arch}.Release"
        )

    def _sysroot(self):
        return os.environ.get("ROOTFS_DIR")

    def _bundle(self, name: str, build_root: Path):
        tokens, link_flags, flags = self._parse_link_rule(build_root, name)
        copied: set[Path] = set()
        package = Path(self.package_folder)
        for token in tokens:
            source = self._resolve_token(build_root, token)
            if source is None:
                continue
            target = package / self._token_output_path(token)
            if target in copied:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.add(target)

        save(self, str(package / f"{name}.rsp"), "\n".join(tokens) + "\n")
        save(
            self,
            str(package / f"{name}.linkflags"),
            f"{flags}\n{link_flags}\n",
        )

    @staticmethod
    def _resolve_token(build_root: Path, token: str):
        normalized = token.replace("\\", "/")
        absolute = normalized.startswith("/") or (
            len(normalized) >= 3 and normalized[1:3] == ":/"
        )
        candidate = Path(normalized) if absolute else build_root / normalized
        return candidate if candidate.exists() else None

    @staticmethod
    def _token_output_path(token: str):
        normalized = token.replace("\\", "/")
        if len(normalized) >= 2 and normalized[1] == ":":
            normalized = f"{normalized[0]}{normalized[2:]}"
        parts = [part for part in normalized.lstrip("/").split("/") if part != "."]
        if not parts:
            raise ConanException(f"Cannot derive package path from token: {token}")
        return Path(*parts)

    @staticmethod
    def _parse_link_rule(build_root: Path, name: str):
        target_outputs = {
            "apphost": {"apphost/standalone/apphost", "apphost/standalone/apphost.exe"},
            "singlefilehost": {
                "Corehost.Static/singlefilehost",
                "Corehost.Static/singlefilehost.exe",
            },
        }[name]
        build_ninja = build_root / "build.ninja"
        lines = build_ninja.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not line.startswith("build ") or ": " not in line:
                continue
            head, tail = line.split(": ", 1)
            outputs = {
                output.replace("\\", "/")
                for output in head.removeprefix("build ").split()
            }
            if outputs.isdisjoint(target_outputs):
                continue

            rule_tokens = tail.split()
            objects = []
            for token in rule_tokens[1:]:
                if token == "|":
                    break
                objects.append(token)

            values = {"LINK_LIBRARIES": "", "LINK_FLAGS": "", "FLAGS": ""}
            for following in lines[index + 1 : index + 16]:
                for key in values:
                    prefix = f"  {key} = "
                    if following.startswith(prefix):
                        values[key] = following.removeprefix(prefix).strip()

            libraries = values["LINK_LIBRARIES"].split()
            if not objects or not libraries or not values["LINK_FLAGS"]:
                raise ConanException(f"Malformed Ninja link rule for {name}")
            return [*objects, *libraries], values["LINK_FLAGS"], values["FLAGS"]

        raise ConanException(f"Ninja target rule not found for {name}: {build_ninja}")
