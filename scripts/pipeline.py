#!/usr/bin/env python3
import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path


def error(text):
    print(f"\n! {text}\n")
    sys.exit(1)


def header(text):
    print(f"\n* {text}\n")


def vprint(text):
    if args.verbose > 0:
        print(text)


# OS detection
os_name = platform.system().lower()
is_windows = os_name not in ("linux", "darwin")

if not sys.version_info >= (3, 10):
    error("Requires Python 3.10+")

# Global vars
args: argparse.Namespace


###################
# Helper functions
###################


def execv(cmds, cwd: Path | None = None, env=None):
    if args.verbose > 0:
        print(" ".join(str(x) for x in cmds))
    proc = subprocess.run(cmds, cwd=cwd, env=env, shell=is_windows)
    if proc.returncode != 0:
        error(f"Command failed ({proc.returncode}): {' '.join(str(x) for x in cmds)}")
    return proc


def python():
    return sys.executable


###############
# Pipeline
###############

SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parent
BUILD_HOSTLIBS_SCRIPT = SCRIPT_ROOT / "build-hostlibs.py"
BUILD_ANGLE_SCRIPT = SCRIPT_ROOT / "build-angle.py"
BUILD_SKIA_HARFBUZZ_SCRIPT = SCRIPT_ROOT / "build-skia-harfbuzz.py"
AVALONIA_APPHOST_LINK_PROJ = (
    REPO_ROOT / "src" / "package-avalonia-apphost" / "AvaloniaAppHost.Link.proj"
)
AVALONIA_APPHOST_CSPROJ = (
    REPO_ROOT / "src" / "package-avalonia-apphost" / "AvaloniaAppHost.csproj"
)
APPHOST_STATIC_CSPROJ = (
    REPO_ROOT / "src" / "package-apphost-static" / "AppHostStatic.csproj"
)
SKIASHARP_STATIC_CSPROJ = (
    REPO_ROOT / "src" / "package-skiasharp-static" / "SkiaSharp.Static.csproj"
)
MATRIX_TEST_CSPROJ = (
    REPO_ROOT
    / "tests"
    / "HostForge.StaticAppHost.Tests"
    / "HostForge.StaticAppHost.Tests.csproj"
)
AVALONIA_TEST_CSPROJ = (
    REPO_ROOT
    / "tests"
    / "HostForge.AvaloniaAppHost.Tests"
    / "HostForge.AvaloniaAppHost.Tests.csproj"
)


def dotnet_verbosity() -> str:
    return "normal" if args.verbose > 0 else "minimal"


def build_hostlibs():
    header("Build host libs")
    verbose = ["-v"] if args.verbose > 0 else []
    if is_windows:
        builds = [
            ("x64", False),
            ("arm64", False),
            ("x64", True),
            ("arm64", True),
        ]
    else:
        builds = [("x64", False)]

    for arch, no_pgo in builds:
        extra = ["--no-pgo"] if no_pgo else []
        execv([python(), BUILD_HOSTLIBS_SCRIPT, *verbose, "all", "-a", arch, *extra])


def build_skia_harfbuzz():
    header("Build SkiaSharp and HarfBuzzSharp")
    verbose = ["-v"] if args.verbose > 0 else []
    execv([python(), BUILD_SKIA_HARFBUZZ_SCRIPT, *verbose, "-a", "x64"])
    execv([python(), BUILD_SKIA_HARFBUZZ_SCRIPT, *verbose, "-a", "arm64"])


def build_angle():
    header("Build ANGLE static libraries")
    verbose = ["-v"] if args.verbose > 0 else []
    execv([python(), BUILD_ANGLE_SCRIPT, *verbose, "-a", "x64"])
    execv(
        [
            python(),
            BUILD_ANGLE_SCRIPT,
            *verbose,
            "-a",
            "arm64",
            "--skip-sync-deps",
        ]
    )


def run_matrix_test():
    header("Run matrix test")
    env = None
    if args.skip_exe_run or args.no_clean:
        env = dict(os.environ)
        if args.skip_exe_run:
            env["HOSTFORGE_MATRIX_SKIP_EXE_RUN"] = "true"
        if args.no_clean:
            env["HOSTFORGE_MATRIX_NO_CLEAN"] = "true"

    cmd = ["dotnet", "test", "--project", MATRIX_TEST_CSPROJ, "-c", "Release"]
    cmd.extend([f"-v:{dotnet_verbosity()}"])
    execv(cmd, env=env)


def run_avalonia_test():
    header("Run avalonia apphost test")
    env = dict(os.environ)
    if args.sysroot:
        env["ROOTFS_DIR"] = args.sysroot
    cmd = ["dotnet", "test", "--project", AVALONIA_TEST_CSPROJ, "-c", "Release"]
    cmd.extend([f"-v:{dotnet_verbosity()}"])
    execv(cmd, env=env)


def link_avalonia_apphost():
    header("Link avalonia apphost templates")
    os_arg = [f"-p:AvaloniaAppHostTarget={args.os}"]
    hostlibs_flavor = ["-p:HostLibsFlavor=default"]
    sysroot = [f"-p:Sysroot={args.sysroot}"] if args.sysroot else []
    execv(
        [
            "dotnet",
            "msbuild",
            AVALONIA_APPHOST_LINK_PROJ,
            "/t:LinkAvaloniaHosts",
            *os_arg,
            *hostlibs_flavor,
            *sysroot,
            f"-v:{dotnet_verbosity()}",
        ]
    )


def pack_avalonia_apphost():
    header("Pack avalonia apphost nuget")
    mode = [f"-p:AvaloniaAppHostPackageMode={args.mode}"]
    hostlibs_flavor = ["-p:HostLibsFlavor=default"]
    execv(
        [
            "dotnet",
            "pack",
            AVALONIA_APPHOST_CSPROJ,
            *mode,
            *hostlibs_flavor,
            f"-v:{dotnet_verbosity()}",
        ]
    )


def pack_static_apphost():
    header("Pack static apphost nuget")
    rid = [f"-p:StaticAppHostRid={args.rid}"]
    hostlibs_flavor = ["-p:HostLibsFlavor=no-pgo"]
    execv(
        [
            "dotnet",
            "pack",
            APPHOST_STATIC_CSPROJ,
            *rid,
            *hostlibs_flavor,
            f"-v:{dotnet_verbosity()}",
        ]
    )


def pack_skiasharp_static():
    header("Pack skiasharp static nuget")
    rid = [f"-p:AvaloniaHostRid={args.rid}"]
    execv(
        [
            "dotnet",
            "pack",
            SKIASHARP_STATIC_CSPROJ,
            *rid,
            f"-v:{dotnet_verbosity()}",
        ]
    )


##########################
# Parse arguments
##########################


def add_matrix_options(parser):
    parser.add_argument(
        "--skip-exe-run", action="store_true", help="skip exe run in matrix test"
    )
    parser.add_argument(
        "--no-clean", action="store_true", help="keep consumer bin/obj in matrix test"
    )


def parse_args():
    parser = argparse.ArgumentParser(description="HostForge pipeline script")
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    hostlibs_parser = subparsers.add_parser("hostlibs", help="build hostlibs only")
    hostlibs_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    hostlibs_parser.set_defaults(func=build_hostlibs)

    matrix_parser = subparsers.add_parser(
        "matrix", help="run apphost linking build integration matrix test only"
    )
    add_matrix_options(matrix_parser)
    matrix_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    matrix_parser.set_defaults(func=run_matrix_test)

    avalonia_test_parser = subparsers.add_parser(
        "avalonia-test", help="run avalonia apphost integration test only"
    )
    avalonia_test_parser.add_argument(
        "--sysroot",
        help="optional sysroot path exposed to tests as ROOTFS_DIR",
    )
    avalonia_test_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    avalonia_test_parser.set_defaults(func=run_avalonia_test)

    link_avalonia_parser = subparsers.add_parser(
        "link-avalonia", help="link avalonia apphost templates only"
    )
    link_avalonia_parser.add_argument(
        "--os",
        choices=["windows", "linux"],
        required=True,
        help="target OS for generated apphost templates",
    )
    link_avalonia_parser.add_argument(
        "--sysroot",
        help="optional sysroot path passed to Avalonia apphost link as -p:Sysroot",
    )
    link_avalonia_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    link_avalonia_parser.set_defaults(func=link_avalonia_apphost)

    skia_parser = subparsers.add_parser("skia", help="build SkiaSharp only")
    skia_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    skia_parser.set_defaults(func=build_skia_harfbuzz)

    angle_parser = subparsers.add_parser("angle", help="build ANGLE static libraries")
    angle_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    angle_parser.set_defaults(func=build_angle)

    pack_avalonia_parser = subparsers.add_parser(
        "pack-avalonia", help="pack avalonia apphost only"
    )
    pack_avalonia_parser.add_argument(
        "--mode",
        choices=["windows", "linux", "all"],
        required=True,
        help="package only one OS or aggregate all prelinked outputs",
    )
    pack_avalonia_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    pack_avalonia_parser.set_defaults(func=pack_avalonia_apphost)

    pack_static_parser = subparsers.add_parser(
        "pack-static-apphost", help="pack static apphost only"
    )
    pack_static_parser.add_argument(
        "--rid",
        choices=["win-x64"],
        default="win-x64",
        help="StaticAppHost package RID",
    )
    pack_static_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    pack_static_parser.set_defaults(func=pack_static_apphost)

    pack_skiasharp_parser = subparsers.add_parser(
        "pack-skia-static", help="pack skiasharp static only"
    )
    pack_skiasharp_parser.add_argument(
        "--rid",
        choices=["win-x64"],
        default="win-x64",
        help="SkiaSharp static package RID",
    )
    pack_skiasharp_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    pack_skiasharp_parser.set_defaults(func=pack_skiasharp_static)

    return parser.parse_args()


def main():
    global args
    args = parse_args()
    args.func()


if __name__ == "__main__":
    main()
