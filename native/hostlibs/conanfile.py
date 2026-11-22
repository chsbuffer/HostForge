import json
import os
import re
import shutil
from pathlib import Path

from conan import ConanFile
from conan.tools.build import build_jobs
from conan.errors import ConanException, ConanInvalidConfiguration
from conan.tools.env import Environment
from conan.tools.files import rmdir, save
from conan.tools.microsoft import VCVars
from conan.tools.scm import Git

# Shipped runtimes are built from the VMR, which can carry fixes (including
# security fixes) that dotnet/runtime tags do not contain yet. Each entry is the
# 1xx SDK band tag that built the runtime; installed runtimes record the same
# commit in shared/Microsoft.NETCore.App/<version>/.version.
VERSIONS = json.loads((Path(__file__).parent / "versions.json").read_text())
VMR_URL = "https://github.com/dotnet/dotnet.git"

class HostLibsConan(ConanFile):
    name = "hostlibs"
    package_type = "static-library"
    license = "MIT"
    homepage = "https://github.com/dotnet/dotnet"
    description = ".NET apphost and single-file host static link inputs"

    settings = "os", "arch", "compiler", "build_type"
    options = {"pgo": [True, False], "libc": ["glibc", "musl"]}
    default_options = {"pgo": True, "libc": "glibc"}
    exports = "versions.json"

    _arch = {
        "x86_64": "x64",
        "armv8": "arm64",
    }

    def set_version(self):
        # No default here: Taskfile.yml always passes --version, defaulting to the version scripts/repo_versions.py reads.
        if str(self.version) not in VERSIONS:
            raise ConanException(f"Unsupported .NET runtime version: {self.version}; pass --version with one of "
                                 f"{', '.join(VERSIONS)} (native/hostlibs/versions.json)")

    def config_options(self):
        if self.settings.os != "Linux":
            self.options.rm_safe("libc")
        if self.settings.os != "Windows":
            # build.sh runs in the pinned upstream build container, which selects
            # its own LLVM; the container digest identifies it in the cache key.
            self.settings.rm_safe("compiler")
            # Linux hosts are built like the official Linux runtime, with its PGO
            # data; the no-PGO flavor exists for the Windows AppHost.Static package.
            self.options.rm_safe("pgo")

    def validate(self):
        if str(self.version) not in VERSIONS:
            raise ConanInvalidConfiguration(f"Unsupported .NET runtime version: {self.version}")

    @property
    def _source_commit(self):
        return VERSIONS[str(self.version)]["commit"]

    @property
    def _runtime_root(self):
        return Path(self.source_folder) / "runtime"

    @property
    def _target_arch(self):
        return self._arch[str(self.settings.arch)]

    @property
    def _musl(self):
        return self.settings.os == "Linux" and self.options.libc == "musl"

    @property
    def _rid(self):
        prefix = "win" if self.settings.os == "Windows" else "linux-musl" if self._musl else "linux"
        return f"{prefix}-{self._target_arch}"

    def source(self):
        source = VERSIONS[str(self.version)]
        # Fetch only src/runtime from the VMR. LF endings keep sources (and
        # compiler-cache inputs) identical across hosts, as with an archive.
        Git(self, folder=self.source_folder).run(
            "clone --quiet --depth 1 --filter=blob:none --sparse "
            "--config core.autocrlf=false --config core.eol=lf --config core.longpaths=true "
            f"--branch {source['tag']} {VMR_URL} vmr"
        )
        vmr = Git(self, folder=os.path.join(self.source_folder, "vmr"))
        commit = vmr.run("rev-parse HEAD")
        if commit != source["commit"]:
            raise ConanException(f"VMR tag {source['tag']} resolved to {commit}, expected {source['commit']}")
        vmr.run("sparse-checkout set src/runtime")
        shutil.move(os.path.join(self.source_folder, "vmr", "src", "runtime"), str(self._runtime_root))
        rmdir(self, os.path.join(self.source_folder, "vmr"))

    def generate(self):
        env = Environment()
        if wrapper := os.environ.get("HOSTFORGE_COMPILER_CACHE"):
            env.define("CMAKE_C_COMPILER_LAUNCHER", wrapper.replace("\\", "/"))
            env.define("CMAKE_CXX_COMPILER_LAUNCHER", wrapper.replace("\\", "/"))
            env.define("SCCACHE_BASEDIRS", self.build_folder)
            env.define("SCCACHE_DIRECT", "false")
        if self.settings.os == "Windows":
            # dotnet-install.ps1 prefers .tar.gz for .NET 11+ and runs the first
            # tar on PATH, which may be Git's GNU tar ("Cannot connect to C:").
            env.define("DOTNET_INSTALL_SKIP_TAR", "1")
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

        # The runtime's notices cover the third-party code of its native components, which the hosts link.
        licenses = Path(self.package_folder) / "licenses"
        licenses.mkdir()
        for name in ("LICENSE.TXT", "THIRD-PARTY-NOTICES.TXT"):
            shutil.copy2(self._runtime_root / name, licenses / name)

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
            self.run(
                f'"{self._runtime_root / "build.sh"}" host.native -ninja '
                f"-c release -arch {self._target_arch}{self._unix_target()} -p:ConfigureOnly=true "
                f"{self._runtime_properties('-p:')}",
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
            self.run(
                f'"{self._runtime_root / "build.sh"}" clr.runtime -ninja '
                f"-c release -arch {self._target_arch}{self._unix_target()} -p:ConfigureOnly=true "
                f"{self._runtime_properties('-p:')}",
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
        # .NET 11 rewrote apphost in C, so its link rule is C_EXECUTABLE_LINKER.
        marker = re.compile(rf": (?:C|CXX)_EXECUTABLE_LINKER__{re.escape(name)}_")
        for line in text.splitlines():
            if line.startswith("build ") and marker.search(line):
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
               f"{self._runtime_properties('/p:')} {extra_args}{self._cmake_args()}")
        if pre_ninja:
            cmd += f" && {pre_ninja}"
        self.run(cmd, cwd=root, env="conanbuild")
        self._msvc_rewrite_compiles(build_root)
        target = self._link_inputs_target(build_root, target)
        cmd = ""
        if self._target_arch == "arm64":
            cmd = f'call "{root}\\eng\\native\\init-vs-env.cmd" arm64 && '
        cmd += f'ninja -C "{build_root}" -j {build_jobs(self)} {target}'
        self.run(cmd, cwd=root, env="conanbuild")

    @staticmethod
    def _msvc_rewrite_compiles(build_root):
        # The packaged objects are relinked outside this build tree, where the
        # compiler PDBs that /Zi writes (named by /Fd) do not exist: the linker
        # reports LNK4099 and links the objects without debug information.
        # Embed CodeView in every object instead (/Z7; CMake spells the format
        # /Zi or -Zi). A /Yc object then holds the types of its precompiled
        # header, and the /Yu objects reference it, which also links it in.
        cache = bool(os.environ.get("HOSTFORGE_COMPILER_CACHE"))
        ninja = build_root / "build.ninja"
        blocks = ninja.read_text(encoding="utf-8").split("\n\n")
        for index, block in enumerate(blocks):
            if not re.search(r"^build .*: (?:C|CXX)_COMPILER__", block, re.M):
                continue
            # sccache cannot cache MSVC PCH creators or consumers: compile them
            # directly and keep their PCH. Through sccache, spell /Zl as -Zl,
            # which sccache otherwise parses as an input path.
            cached = cache and not re.search(r"^  FLAGS = .* /Y[cu]", block, re.M)

            def flags(match):
                text = match.group().replace(" /Zi", " /Z7").replace(" -Zi", " -Z7")
                return text.replace(" /Zl", " -Zl") if cached else text

            block = re.sub(r"^  FLAGS =.*$", flags, block, flags=re.M)
            if cache and not cached:
                block = re.sub(r"^  LAUNCHER =.*$", "  LAUNCHER = ", block, flags=re.M)
            blocks[index] = block
        ninja.write_text("\n\n".join(blocks), encoding="utf-8")

    def _runtime_properties(self, prefix):
        # Rebuilding a fixed release must not fail on advisories published later
        # for build-time MSBuild task dependencies; they never enter the hosts.
        return f"{prefix}SourceRevisionId={self._source_commit} {prefix}NuGetAudit=false"

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

    def _unix_target(self):
        # build.sh derives the musl RID, CMake defines and PGO data from --os, as in the official builds.
        cross = " --cross" if self._sysroot() else ""
        return f"{cross} --os linux-musl" if self._musl else cross

    def _bundle(self, name: str, build_root: Path):
        tokens, link_flags, flags = self._parse_link_rule(build_root, name)
        copied: set[Path] = set()
        package = Path(self.package_folder)
        response = []
        for token in tokens:
            path = self._token_input_path(token)
            source = self._resolve_token(build_root, path)
            if source is not None:
                relative = self._token_output_path(path)
                target = package / relative
                if target not in copied:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    copied.add(target)
                if self._is_absolute(path):
                    # A library outside the build tree, such as the libpthread.a stub that CMake finds in the
                    # musl rootfs. The link runs in the package directory, so name the packaged copy.
                    token = token[:len(token) - len(path)] + relative.as_posix()
            response.append(token)

        save(self, str(package / f"{name}.rsp"), "\n".join(response) + "\n")
        save(
            self,
            str(package / f"{name}.linkflags"),
            f"{flags}\n{link_flags}\n",
        )

    @staticmethod
    def _token_input_path(token: str):
        # CMake's WHOLE_ARCHIVE link feature emits the library inside the MSVC
        # option (.NET 11 links runtimeinfo.lib this way). The response file
        # keeps the option; the package needs the library it names.
        option = "/WHOLEARCHIVE:"
        return token[len(option):] if token.upper().startswith(option) else token

    @staticmethod
    def _is_absolute(token: str):
        normalized = token.replace("\\", "/")
        return normalized.startswith("/") or (len(normalized) >= 3 and normalized[1:3] == ":/")

    @classmethod
    def _resolve_token(cls, build_root: Path, token: str):
        normalized = token.replace("\\", "/")
        candidate = Path(normalized) if cls._is_absolute(token) else build_root / normalized
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
                # Explicit inputs end at the implicit (|), order-only (||) or
                # validation (|@) separator; .NET 11's C apphost has only ||.
                if token.startswith("|"):
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
