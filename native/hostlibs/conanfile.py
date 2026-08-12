import os
import shutil
from pathlib import Path

from conan import ConanFile
from conan.errors import ConanException
from conan.tools.env import Environment
from conan.tools.files import mkdir, save
from conan.tools.microsoft import VCVars
from conan.tools.scm import Git
from conan.tools.system.package_manager import Apt

VERSION = "10.0.11"

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
    _source_roots = (
        ".config",
        "docs/design/datacontracts/data",
        "eng",
        "src/coreclr",
        "src/libraries/Common",
        "src/libraries/Microsoft.NETCore.Platforms",
        "src/libraries/System.Private.CoreLib",
        "src/libraries/System.Runtime.InteropServices",
        "src/native",
        "src/tasks",
    )

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
        runtime = self._runtime_root
        mkdir(self, str(runtime))
        git = Git(self, folder=str(runtime))
        git.run("init")
        git.run('remote add origin "https://github.com/dotnet/runtime"')
        git.run(f"fetch --depth 1 --filter=blob:none origin v{self.version}")
        git.run("sparse-checkout init --cone")
        git.run(f"sparse-checkout set {' '.join(self._source_roots)}")
        git.run("checkout --detach FETCH_HEAD")

    def generate(self):
        env = Environment()
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
                f"-c release -arch {self._target_arch}{cross}",
                cwd=str(self._runtime_root),
                env="conanbuild",
            )

    def _build_singlefilehost(self):
        root = self._singlefilehost_build_root()
        if self.settings.os == "Windows":
            extra = "/p:ConfigureOnly=true"
            if not self.options.pgo:
                extra += " /p:CMakeArgs=-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF"
                extra += " /p:NoPgoOptimize=true"
            self._msvc_configure_build("clr.runtime", "singlefilehost", root,
                                       extra_args=extra)
        else:
            cross = " --cross" if self._sysroot() else ""
            self.run(
                f'"{self._runtime_root / "build.sh"}" clr.runtime -ninja '
                f"-c release -arch {self._target_arch}{cross}",
                cwd=str(self._runtime_root),
                env="conanbuild",
            )

    def _msvc_configure_build(self, subset, target, build_root, extra_args="", pre_ninja=""):
        root = str(self._runtime_root)
        cmd = f'"{root}\\build.cmd" {subset} -ninja -c release -arch {self._target_arch} {extra_args}'
        if pre_ninja:
            cmd += f" && {pre_ninja}"
        if self._target_arch == "arm64":
            cmd += f' && call "{root}\\eng\\native\\init-vs-env.cmd" arm64'
        cmd += f' && ninja -C "{build_root}" {target}'
        self.run(cmd, cwd=root, env="conanbuild")

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
