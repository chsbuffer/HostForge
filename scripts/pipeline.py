#!/usr/bin/env python3
import argparse
import os
import platform
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
BUILD_SKIA_HARFBUZZ_SCRIPT = SCRIPT_ROOT / "build-skia-harfbuzz.py"
AVALONIA_APPHOST_LINK_PROJ = (
    REPO_ROOT / "src" / "package-avalonia-apphost" / "AvaloniaAppHost.Link.proj"
)
AVALONIA_APPHOST_CSPROJ = (
    REPO_ROOT / "src" / "package-avalonia-apphost" / "AvaloniaAppHost.csproj"
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
    execv([python(), BUILD_HOSTLIBS_SCRIPT, *verbose, "all", "-a", "x64"])
    execv([python(), BUILD_HOSTLIBS_SCRIPT, *verbose, "all", "-a", "arm64"])


def build_skia_harfbuzz():
    header("Build SkiaSharp and HarfBuzzSharp")
    verbose = ["-v"] if args.verbose > 0 else []
    execv([python(), BUILD_SKIA_HARFBUZZ_SCRIPT, *verbose, "-a", "x64"])
    execv([python(), BUILD_SKIA_HARFBUZZ_SCRIPT, *verbose, "-a", "arm64"])


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
    env["TargetAvaloniaVersion"] = args.target
    cmd = ["dotnet", "test", "--project", AVALONIA_TEST_CSPROJ, "-c", "Release"]
    cmd.extend([f"-v:{dotnet_verbosity()}"])
    execv(cmd, env=env)


def link_avalonia_apphost():
    header("Link avalonia apphost templates")
    target = [f"-p:TargetAvaloniaVersion={args.target}"]
    os_arg = [f"-p:AvaloniaAppHostTarget={args.os}"]
    execv(
        [
            "dotnet",
            "msbuild",
            AVALONIA_APPHOST_LINK_PROJ,
            "/t:LinkAvaloniaHosts",
            *target,
            *os_arg,
            f"-v:{dotnet_verbosity()}",
        ]
    )


def pack_avalonia_apphost():
    header("Pack avalonia apphost nuget")
    target = [f"-p:TargetAvaloniaVersion={args.target}"]
    mode = [f"-p:AvaloniaAppHostPackageMode={args.mode}"]
    execv(
        ["dotnet", "pack", AVALONIA_APPHOST_CSPROJ, *target, *mode, f"-v:{dotnet_verbosity()}"]
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
        "--target",
        choices=["11.0", "12.0"],
        default="11.0",
        help="Target Avalonia version. This decides skiasharp version and changes output package version",
    )
    avalonia_test_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    avalonia_test_parser.set_defaults(func=run_avalonia_test)

    link_avalonia_parser = subparsers.add_parser(
        "link-avalonia", help="link avalonia apphost templates only"
    )
    link_avalonia_parser.add_argument(
        "--target",
        choices=["11.0", "12.0"],
        default="11.0",
        help="Target Avalonia version. This decides skiasharp version and changes output package version",
    )
    link_avalonia_parser.add_argument(
        "--os",
        choices=["windows", "linux"],
        required=True,
        help="target OS for generated apphost templates",
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

    pack_avalonia_parser = subparsers.add_parser(
        "pack-avalonia", help="pack avalonia apphost only"
    )
    pack_avalonia_parser.add_argument(
        "--target",
        choices=["11.0", "12.0"],
        default="11.0",
        help="Target Avalonia version. This decides skiasharp version and changes output package version",
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

    return parser.parse_args()


def main():
    global args
    args = parse_args()
    args.func()


if __name__ == "__main__":
    main()
