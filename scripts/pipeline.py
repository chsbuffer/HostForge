#!/usr/bin/env python3
import argparse
import platform
import shutil
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


def execv(cmds: list[str], cwd: Path | None = None, env=None):
    if args.verbose > 0:
        print(" ".join(str(x) for x in cmds))
    proc = subprocess.run(cmds, cwd=cwd, env=env, shell=is_windows)
    if proc.returncode != 0:
        error(f"Command failed ({proc.returncode}): {' '.join(str(x) for x in cmds)}")
    return proc


def resolve_python():
    if shutil.which("py"):
        return ["py", "-3"]
    if shutil.which("python"):
        return ["python"]
    error("Python not found. Install Python 3.10+.")


###############
# Pipeline
###############

SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parent
BUILD_HOSTLIBS_SCRIPT = SCRIPT_ROOT / "build-hostlibs.py"
STATIC_APPHOST_CSPROJ = REPO_ROOT / "src" / "package-static-apphost" / "StaticAppHost.csproj"
AVALONIA_APPHOST_CSPROJ = REPO_ROOT / "src" / "package-avalonia-apphost" / "AvaloniaAppHost.csproj"
MATRIX_TEST_CSPROJ = REPO_ROOT / "tests" / "HostForge.StaticAppHost.Tests" / "HostForge.StaticAppHost.Tests.csproj"


def runtime_rid(arch: str) -> str:
    return f"win-{arch}"


def dotnet_verbosity() -> str:
    return "normal" if args.verbose > 0 else "minimal"


def build_hostlibs():
    header("Build host libs")
    py = resolve_python()
    cmd = [*py, str(BUILD_HOSTLIBS_SCRIPT)]
    if args.verbose > 0:
        cmd.append("-v")
    cmd.extend(["all", "-a", args.arch])
    execv(cmd, cwd=REPO_ROOT)


def pack_static_apphost():
    header("Pack static apphost nuget")
    rid = runtime_rid(args.arch)
    package_assets_dir = REPO_ROOT / "artifacts" / "hostlibs" / rid
    execv(
        [
            "dotnet",
            "pack",
            str(STATIC_APPHOST_CSPROJ),
            "-c",
            args.configuration,
            f"-v:{dotnet_verbosity()}",
            f"/p:PackageAssetsDir={package_assets_dir}",
        ],
        cwd=REPO_ROOT,
    )


def pack_avalonia_apphost():
    if args.arch != "x64":
        error("Avalonia apphost package currently supports x64 only.")

    header("Pack avalonia apphost nuget")
    rid = runtime_rid(args.arch)
    package_assets_dir = REPO_ROOT / "artifacts" / "hostlibs" / rid
    execv(
        [
            "dotnet",
            "pack",
            str(AVALONIA_APPHOST_CSPROJ),
            "-c",
            args.configuration,
            f"-v:{dotnet_verbosity()}",
            f"/p:PackageAssetsDir={package_assets_dir}",
        ],
        cwd=REPO_ROOT,
    )


def run_matrix_test():
    if args.arch != "x64":
        error("Matrix test currently only supports x64 package. Use --skip-matrix-test for non-x64.")

    header("Run matrix test")
    cmd = [
        "dotnet",
        "test",
        "--project",
        str(MATRIX_TEST_CSPROJ),
        "-c",
        args.configuration,
        f"-v:{dotnet_verbosity()}",
    ]
    if args.skip_exe_run:
        cmd.extend(["-e", "HOSTFORGE_MATRIX_SKIP_EXE_RUN=true"])
    if args.no_clean:
        cmd.extend(["-e", "HOSTFORGE_MATRIX_NO_CLEAN=true"])
    execv(cmd, cwd=REPO_ROOT)


def run_all():
    if not args.skip_host_lib_build:
        build_hostlibs()
    if not args.skip_pack:
        pack_static_apphost()
    if not args.skip_matrix_test:
        run_matrix_test()
    print("\nPipeline completed.")


##########################
# Parse arguments
##########################


def add_common_options(parser):
    parser.add_argument(
        "-a",
        "--arch",
        choices=["x64", "x86", "arm64"],
        default="x64",
        help="target architecture",
    )
    parser.add_argument(
        "-c",
        "--configuration",
        default="Release",
        help="build configuration (default: Release)",
    )


def add_all_options(parser):
    parser.add_argument("--skip-host-lib-build", action="store_true", help="skip HostLib build")
    parser.add_argument("--skip-pack", action="store_true", help="skip package step")
    parser.add_argument("--skip-matrix-test", action="store_true", help="skip matrix test")
    parser.add_argument("--skip-exe-run", action="store_true", help="skip exe run in matrix test")
    parser.add_argument("--no-clean", action="store_true", help="keep consumer bin/obj in matrix test")


def parse_args():
    parser = argparse.ArgumentParser(description="HostForge pipeline script")
    parser.set_defaults(func=run_all)
    parser.add_argument("-v", "--verbose", action="count", default=0, help="verbose output")
    add_common_options(parser)
    add_all_options(parser)

    subparsers = parser.add_subparsers(dest="command")

    all_parser = subparsers.add_parser("all", help="run full pipeline")
    all_parser.add_argument("-v", "--verbose", action="count", default=0, help="verbose output")
    add_common_options(all_parser)
    add_all_options(all_parser)
    all_parser.set_defaults(func=run_all)

    hostlibs_parser = subparsers.add_parser("hostlibs", help="build hostlibs only")
    hostlibs_parser.add_argument("-v", "--verbose", action="count", default=0, help="verbose output")
    hostlibs_parser.add_argument(
        "-a",
        "--arch",
        choices=["x64", "x86", "arm64"],
        default="x64",
        help="target architecture",
    )
    hostlibs_parser.set_defaults(func=build_hostlibs)

    pack_static_parser = subparsers.add_parser("pack-static", help="pack static apphost only")
    pack_static_parser.add_argument("-v", "--verbose", action="count", default=0, help="verbose output")
    add_common_options(pack_static_parser)
    pack_static_parser.set_defaults(func=pack_static_apphost)

    pack_avalonia_parser = subparsers.add_parser("pack-avalonia", help="pack avalonia apphost only")
    pack_avalonia_parser.add_argument("-v", "--verbose", action="count", default=0, help="verbose output")
    add_common_options(pack_avalonia_parser)
    pack_avalonia_parser.set_defaults(func=pack_avalonia_apphost)

    matrix_parser = subparsers.add_parser("matrix", help="run matrix test only")
    matrix_parser.add_argument("-v", "--verbose", action="count", default=0, help="verbose output")
    add_common_options(matrix_parser)
    matrix_parser.add_argument("--skip-exe-run", action="store_true", help="skip exe run in matrix test")
    matrix_parser.add_argument("--no-clean", action="store_true", help="keep consumer bin/obj in matrix test")
    matrix_parser.set_defaults(func=run_matrix_test)

    return parser.parse_args()


def main():
    global args
    args = parse_args()
    args.func()


if __name__ == "__main__":
    main()
