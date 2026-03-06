#!/usr/bin/env python3
import argparse
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def color_print(code, text):
    if no_color:
        print(text)
    else:
        text = text.replace("\n", f"\033[0m\n{code}")
        print(f"{code}{text}\033[0m")


def error(text):
    color_print("\033[41;39m", f"\n! {text}\n")
    sys.exit(1)


def header(text):
    color_print("\033[44;39m", f"\n* {text}\n")


def vprint(text):
    if args.verbose > 0:
        print(text)


# OS detection
os_name = platform.system().lower()
is_windows = os_name not in ("linux", "darwin")

no_color = False
if is_windows:
    try:
        import colorama

        colorama.init()
    except ImportError:
        no_color = True

if not sys.version_info >= (3, 10):
    error("Requires Python 3.10+")

# Global vars
args: argparse.Namespace


###################
# Helper functions
###################


def rm_on_error(func, path, _):
    try:
        Path(path).chmod(stat.S_IWRITE)
        Path(path).unlink()
    except FileNotFoundError:
        pass


def rm_rf(path: Path):
    if not path.exists():
        return
    vprint(f"rm -rf {path}")
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=rm_on_error)
    else:
        shutil.rmtree(path, onerror=rm_on_error)


def cp(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    vprint(f"cp {source} -> {target}")


def execv(cmd: str, cwd: Path | None = None, env=None):
    out = None if args.verbose > 0 else subprocess.DEVNULL
    if args.verbose > 0:
        print(cmd)
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", cmd],
        cwd=cwd,
        env=env,
        stdout=out,
        stderr=out,
        shell=False,
    )
    if proc.returncode != 0:
        error(f"Command failed ({proc.returncode}): {cmd}")
    return proc


################
# Build HostLib
################

SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parent
RUNTIME_ROOT = REPO_ROOT / "repo" / "runtime"
HOSTLIBS_ROOT = REPO_ROOT / "artifacts" / "hostlibs"
TMP_ROOT = REPO_ROOT / "artifacts" / "tmp"
SINGLEFILEHOST_DEF = (
    RUNTIME_ROOT / "src" / "native" / "corehost" / "apphost" / "static" / "singlefilehost.def"
)


def resolve_arch() -> str:
    return getattr(args, "sub_arch", None) or args.arch


def hostlibs_dir(arch: str) -> Path:
    return HOSTLIBS_ROOT / f"win-{arch}"


def run_in_vs_env(cmd: str, cwd: Path, arch: str):
    full_cmd = (
        f'call "{RUNTIME_ROOT}\\eng\\native\\init-vs-env.cmd" {arch} && '
        f'cd /d "{cwd}" && '
        f"{cmd}"
    )
    execv(full_cmd)


def partition_intermediates(items: list[str]):
    objs = [x for x in items if x.endswith((".obj", ".res"))]
    libs = [x for x in items if x not in objs]
    user_libs = [x for x in libs if "\\" in x or "/" in x]
    win32_libs = [x for x in libs if x not in user_libs]
    return objs, user_libs, win32_libs


def bundle_intermediates(name: str, build_root: Path, rsp_path: Path, output: Path, arch: str):
    if not rsp_path.exists():
        error(f"RSP not found: {rsp_path}")

    intermediates = rsp_path.read_text(encoding="utf-8").split()
    objs, libs, win32_libs = partition_intermediates(intermediates)

    vprint("OBJ")
    vprint("\n".join(objs))
    vprint("\nLIB")
    vprint("\n".join(libs))
    vprint("\nWIN32")
    vprint("\n".join(win32_libs))

    header("Creating Win32 library directives")
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    build_tmp = TMP_ROOT / f"{name}_win32_directives_lib"
    rm_rf(build_tmp)
    build_tmp.mkdir(parents=True, exist_ok=True)

    directives_cpp = build_tmp / "win32_directives.cpp"
    directives_lib = build_tmp / f"lib{name}_directives.lib"
    with directives_cpp.open("w", encoding="utf-8") as f:
        for lib in win32_libs:
            f.write(f'#pragma comment(lib, "{lib}")\n')

    run_in_vs_env(
        'cl.exe /nologo /c win32_directives.cpp && '
        f'lib.exe /nologo win32_directives.obj /out:"{directives_lib.name}"',
        build_tmp,
        arch,
    )

    if args.verbose > 0:
        run_in_vs_env(
            f'dumpbin.exe /nologo /directives "{directives_lib.name}"',
            build_tmp,
            arch,
        )

    header("Archive intermediates")
    obj_lib = build_root / f"lib{name}_obj.lib"
    lib_lib = build_root / f"lib{name}_lib.lib"

    if not objs:
        error(f"No object/resource files found for {name}")

    quoted_objs = " ".join(f'"{obj}"' for obj in objs)
    run_in_vs_env(f'lib.exe /nologo {quoted_objs} /out:"{obj_lib.name}"', build_root, arch)

    if libs:
        quoted_libs = " ".join(f'"{lib}"' for lib in libs)
        run_in_vs_env(f'lib.exe /nologo {quoted_libs} /out:"{lib_lib.name}"', build_root, arch)
    else:
        run_in_vs_env(f'lib.exe /nologo /out:"{lib_lib.name}"', build_root, arch)

    output.mkdir(parents=True, exist_ok=True)
    cp(obj_lib, output / obj_lib.name)
    cp(lib_lib, output / lib_lib.name)
    cp(directives_lib, output / directives_lib.name)
    rm_rf(build_tmp)


def build_target(build_root: Path, target: str, arch: str):
    header(f"Building {target}.exe")
    run_in_vs_env(
        f'call "%CMakePath%" --build "{build_root}" --target {target} --config Release -- -d keeprsp',
        build_root,
        arch,
    )


def bundle_target(name: str, build_root: Path, output: Path, arch: str):
    rsp_path = build_root / "CMakeFiles" / f"{name}.rsp"
    bundle_intermediates(name, build_root, rsp_path, output, arch)


def build_singlefilehost():
    arch = resolve_arch()
    header("Configure runtime")
    execv(
        f'call "{RUNTIME_ROOT}\\src\\coreclr\\build-runtime.cmd" '
        f"-{arch} -release -os windows -component runtime -ninja -configureonly "
        '-cmakeargs "-DCMAKE_NINJA_FORCE_RESPONSE_FILE=ON"',
        cwd=RUNTIME_ROOT,
    )

    build_root = RUNTIME_ROOT / "artifacts" / "obj" / "coreclr" / f"windows.{arch}.Release"
    output = hostlibs_dir(arch)
    build_target(build_root, "singlefilehost", arch)
    bundle_target("singlefilehost", build_root, output, arch)
    cp(SINGLEFILEHOST_DEF, output / SINGLEFILEHOST_DEF.name)


def build_apphost():
    arch = resolve_arch()
    target_rid = f"win-{arch}"

    header("Configure corehost")
    execv(
        "powershell -NoProfile -ExecutionPolicy ByPass "
        f'-File "{RUNTIME_ROOT}\\eng\\common\\msbuild.ps1" '
        f'"{RUNTIME_ROOT}\\src\\native\\corehost\\corehost.proj" '
        "/t:BuildCoreHostWindows "
        "/p:ConfigureOnly=true "
        "/p:Ninja=true "
        f"/p:Configuration=Release /p:RuntimeConfiguration=Release /p:TargetOS=windows "
        f"/p:TargetArchitecture={arch} /p:TargetRid={target_rid} "
        f'/p:RepoRoot="{RUNTIME_ROOT}\\"',
        cwd=RUNTIME_ROOT,
    )

    build_root = RUNTIME_ROOT / "artifacts" / "obj" / f"win-{arch}.Release" / "corehost"
    output = hostlibs_dir(arch)
    build_target(build_root, "apphost", arch)
    bundle_target("apphost", build_root, output, arch)


def build_all():
    build_apphost()
    build_singlefilehost()


##################
# Parse arguments
##################


def parse_args():
    parser = argparse.ArgumentParser(description=".NET App Host LIB build script")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="verbose output")
    parser.add_argument(
        "-a",
        "--arch",
        choices=["x86", "x64", "arm64"],
        default="x64",
        help="target architecture for the default command",
    )
    parser.set_defaults(func=build_all)

    subparsers = parser.add_subparsers(dest="command")

    apphost_parser = subparsers.add_parser("apphost", help="build and bundle apphost intermediates")
    apphost_parser.add_argument(
        "-a",
        "--arch",
        dest="sub_arch",
        choices=["x86", "x64", "arm64"],
        default=None,
        help="target architecture",
    )
    apphost_parser.set_defaults(func=build_apphost)

    singlefilehost_parser = subparsers.add_parser(
        "singlefilehost", help="build and bundle singlefilehost intermediates"
    )
    singlefilehost_parser.add_argument(
        "-a",
        "--arch",
        dest="sub_arch",
        choices=["x86", "x64", "arm64"],
        default=None,
        help="target architecture",
    )
    singlefilehost_parser.set_defaults(func=build_singlefilehost)

    all_parser = subparsers.add_parser("all", help="build apphost + singlefilehost host libs")
    all_parser.add_argument(
        "-a",
        "--arch",
        dest="sub_arch",
        choices=["x86", "x64", "arm64"],
        default=None,
        help="target architecture",
    )
    all_parser.set_defaults(func=build_all)

    return parser.parse_args()


def main():
    global args
    args = parse_args()
    args.func()


if __name__ == "__main__":
    main()
