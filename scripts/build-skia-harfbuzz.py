#!/usr/bin/env python3
import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Never


def color_print(code, text):
    if no_color:
        print(text)
    else:
        text = text.replace("\n", f"\033[0m\n{code}")
        print(f"{code}{text}\033[0m")


def error(text) -> Never:
    color_print("\033[41;39m", f"\n! {text}\n")
    sys.exit(1)


def header(text):
    color_print("\033[44;39m", f"\n* {text}\n")


def vprint(text):
    if args.verbose > 0:
        print(text)


def copy_template(in_path, out, vals: dict[str, str], encoding="utf-8"):
    out_path = Path(out)
    template = Path(in_path).read_text(encoding)
    if not template:
        error(f"input path {in_path} not exists.")

    for k, v in vals.items():
        template = template.replace(f"$${k}$$", v)

    if out_path.exists() and template == Path(out_path).read_text(encoding):
        return

    Path(out_path).write_text(template, encoding)


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
    proc = subprocess.run(cmds, cwd=cwd, shell=is_windows)
    if proc.returncode != 0:
        error(f"Command failed ({proc.returncode}): {' '.join(str(x) for x in cmds)}")
    return proc


def exec_cmd(cmd: str, cwd: Path | None = None, env=None):
    if args.verbose > 0:
        print(cmd)
    proc = subprocess.run(["cmd.exe", "/d", "/c", cmd], cwd=cwd, shell=False)
    if proc.returncode != 0:
        error(f"Command failed ({proc.returncode}): {cmd}")
    return proc


def run_in_vs_env(cmd: str, cwd: Path, arch: str, env=None):
    full_cmd = f"call {INIT_VS_ENV_CMD} {arch} && cd /d {cwd} && {cmd}"
    exec_cmd(full_cmd)


def cp(source: Path, target: Path):
    vprint(f"cp {source} -> {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


###############
# Build logic
###############

SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parent

VC_COMPILER_VER = os.environ.get("VC_COMPILER_VER", "14.5")
VC_TOOLSET_VER = os.environ.get("VC_TOOLSET_VER", "v145")
WINDOWS_SDK_VER = os.environ.get("WINDOWS_SDK_VER", "10.0.26100.0")

DEFAULT_VERSION = "2.88.9"

SKIA_ROOT: Path
SKIA_BUILD_DIR: Path

INIT_VS_ENV_CMD = SCRIPT_ROOT / "init-vs-env.cmd"

HARFBUZZ_SLN_DIR: Path
HARFBUZZ_PROJECT_FILE: Path

OUTDIR: Path

SKIA_OUTPUT_LIBS = [
    "SkiaSharp.lib",
    "skia.lib",
    "skottie.lib",
    "sksg.lib",
    "skshaper.lib",
    "skresources.lib",
]

HARFBUZZ_PLATFORM = {
    "x64": "x64",
    "arm64": "ARM64",
}

GN_CPU = {
    "x64": "x64",
    "arm64": "arm64",
}


def target_rid() -> str:
    return f"win-{args.arch}"


def harfbuzz_output_lib() -> Path:
    platform_name = HARFBUZZ_PLATFORM[args.arch]
    return HARFBUZZ_SLN_DIR / "bin" / platform_name / "Release" / "libHarfBuzzSharp.lib"


def write_args_gn(target: Path):
    cpu = GN_CPU[args.arch]
    ARGS_GN_IN = SCRIPT_ROOT / f"args.{args.version}.gn"
    if not ARGS_GN_IN.exists():
        error(f"{ARGS_GN_IN} not found.")

    copy_template(
        ARGS_GN_IN,
        target,
        {
            "SKIA_ARCH": f"{cpu}",
            "VC_COMPILER_VER": VC_COMPILER_VER,
            "ADDITIONAL_CFLAGS": '"/arch:AVX2",' if args.arch == "x64" else "",
            "ADDITIONAL_LDFLAGS": "",
        },
    )


def build_skia():
    header("Build SkiaSharp")
    SKIA_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    write_args_gn(SKIA_BUILD_DIR / "args.gn")

    if not args.skip_sync_deps:
        execv([sys.executable, SKIA_ROOT / "tools/git-sync-deps"], cwd=SKIA_ROOT)

    execv([SKIA_ROOT / "bin" / "gn.exe", "gen", SKIA_BUILD_DIR], cwd=SKIA_ROOT)
    execv(["ninja", "-C", SKIA_BUILD_DIR, "skia", "SkiaSharp"])


def build_harfbuzz():
    header("Build HarfBuzzSharp")

    HARFBUZZ_SLN_DIR.mkdir(parents=True, exist_ok=True)

    HARFBUZZ_PROJECT_FILE_IN = SCRIPT_ROOT / "libHarfBuzzSharp.vcxproj.in"
    HARFBUZZ_PROJECT_FILE = HARFBUZZ_SLN_DIR / "libHarfBuzzSharp.vcxproj"
    platform_name = HARFBUZZ_PLATFORM[args.arch]
    copy_template(
        HARFBUZZ_PROJECT_FILE_IN,
        HARFBUZZ_PROJECT_FILE,
        {
            "VC_TOOLSET_VER": VC_TOOLSET_VER,
            "WINDOWS_SDK_VER": WINDOWS_SDK_VER,
            "SKIA_ROOT": str(SKIA_ROOT),
        },
        encoding="utf-8-sig",
    )
    run_in_vs_env(
        f"msbuild {HARFBUZZ_PROJECT_FILE} -m /p:Configuration=Release /p:Platform={platform_name}",
        cwd=HARFBUZZ_SLN_DIR,
        arch=args.arch,
    )


def copy_outputs():
    header("Copy output libraries")
    OUTDIR.mkdir(parents=True, exist_ok=True)

    for lib_name in SKIA_OUTPUT_LIBS:
        source = SKIA_BUILD_DIR / lib_name
        target = OUTDIR / lib_name
        cp(source, target)

    harfbuzz_lib = harfbuzz_output_lib()
    target = OUTDIR / harfbuzz_lib.name
    cp(harfbuzz_lib, target)


def build_all():
    if os.name != "nt":
        error("This script is intended for Windows only")

    build_skia()
    build_harfbuzz()
    copy_outputs()
    print("\nDone.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build SkiaSharp + HarfBuzz static libs for HostForge.",
        epilog="""Environment Variables:
    VC_COMPILER_VER:\t(default: 14.5)
    VC_TOOLSET_VER :\t(default: v145)
    WINDOWS_SDK_VER:\t(default: 10.0.26100.0)""",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.set_defaults(func=build_all)
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    parser.add_argument(
        "-a",
        "--arch",
        choices=["x64", "arm64"],
        default="x64",
        help="target architecture",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"SkiaSharp version key in DEPS (default: {DEFAULT_VERSION})",
    )
    parser.add_argument(
        "--skip-sync-deps", action="store_true", help="skip running tools/git-sync-deps"
    )
    return parser.parse_args()


def main():
    global args
    args = parse_args()
    global SKIA_ROOT, SKIA_BUILD_DIR, OUTDIR, HARFBUZZ_SLN_DIR

    SKIA_ROOT = REPO_ROOT / "repo" / f"skia-{args.version}"
    SKIA_BUILD_DIR = SKIA_ROOT / "out" / "windows" / args.arch

    HARFBUZZ_SLN_DIR = REPO_ROOT / "repo" / f"HarfBuzzSharp-{args.version}"

    OUTDIR = REPO_ROOT / "artifacts" / "skiasharp" / args.version / target_rid()

    if not SKIA_ROOT.exists():
        error("source code not found; checkout-deps first.")

    args.func()


if __name__ == "__main__":
    main()
