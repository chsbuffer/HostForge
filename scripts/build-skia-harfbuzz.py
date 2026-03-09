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

SKIASHARP_VERSION = "2.88.9"
SKIA_ROOT = REPO_ROOT / "repo" / "skia"
DEPOT_TOOLS_ROOT = REPO_ROOT / "repo" / "depot_tools"
SKIA_OUT_ROOT = SKIA_ROOT / "out" / "windows"

PATCH_ARGS_GN = REPO_ROOT / "repo" / "patch" / "args.gn"

HARFBUZZ_PROJECT_DIR = REPO_ROOT / "repo" / "HarfBuzzSharp"
HARFBUZZ_PROJECT_FILE_IN = HARFBUZZ_PROJECT_DIR / "libHarfBuzzSharp.vcxproj.in"
HARFBUZZ_PROJECT_FILE = HARFBUZZ_PROJECT_DIR / "libHarfBuzzSharp.vcxproj"
INIT_VS_ENV_CMD = SCRIPT_ROOT / "init-vs-env.cmd"

SKIA_OUTPUT_LIBS = [
    "SkiaSharp.lib",
    "skia.lib",
    "skottie.lib",
    "sksg.lib",
    "skshaper.lib",
]

HARFBUZZ_PLATFORM = {
    "x64": "x64",
    "arm64": "ARM64",
}

GN_CPU = {
    "x64": "x64",
    "arm64": "arm64",
}


def target_arch() -> str:
    return args.arch


def target_rid() -> str:
    return f"win-{target_arch()}"


def resolve_output_dir() -> Path:
    if args.output_dir:
        return Path(args.output_dir).resolve()
    return REPO_ROOT / "artifacts" / f"skiasharp-{SKIASHARP_VERSION}" / target_rid()


def skia_out_dir() -> Path:
    return SKIA_OUT_ROOT / target_arch()


def harfbuzz_output_lib() -> Path:
    platform_name = HARFBUZZ_PLATFORM[target_arch()]
    return (
        HARFBUZZ_PROJECT_DIR
        / "bin"
        / platform_name
        / "Release"
        / "libHarfBuzzSharp.lib"
    )


def write_args_gn(target: Path):
    cpu = GN_CPU[target_arch()]
    lines = PATCH_ARGS_GN.read_text(encoding="utf-8").splitlines()
    output = []
    for line in lines:
        if line.strip().startswith("target_cpu = "):
            output.append(f'target_cpu = "{cpu}"')
            continue
        if line.strip().startswith("win_vcvars_version = "):
            output.append(f'win_vcvars_version = "{VC_COMPILER_VER}"')
            continue
        if target_arch() != "x64" and '"/arch:AVX2"' in line:
            continue
        output.append(line)
    target.write_text("\n".join(output) + "\n", encoding="utf-8")


def build_skia():
    header("Build SkiaSharp")
    out_dir = skia_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    write_args_gn(out_dir / "args.gn")

    if not args.skip_sync_deps:
        execv([sys.executable, SKIA_ROOT / "tools/git-sync-deps"], cwd=SKIA_ROOT)

    execv([SKIA_ROOT / "bin" / "gn.exe", "gen", out_dir], cwd=SKIA_ROOT)
    execv([DEPOT_TOOLS_ROOT / "ninja.exe", "-C", out_dir, "skia", "SkiaSharp"])


def build_harfbuzz():
    header("Build HarfBuzzSharp")
    platform_name = HARFBUZZ_PLATFORM[target_arch()]
    copy_template(
        HARFBUZZ_PROJECT_FILE_IN,
        HARFBUZZ_PROJECT_FILE,
        {
            "VC_TOOLSET_VER": VC_TOOLSET_VER,
            "WINDOWS_SDK_VER": WINDOWS_SDK_VER,
        },
        encoding="utf-8-sig",
    )
    run_in_vs_env(
        f"msbuild {HARFBUZZ_PROJECT_FILE} -m /p:Configuration=Release /p:Platform={platform_name}",
        cwd=HARFBUZZ_PROJECT_DIR,
        arch=target_arch(),
    )


def copy_outputs(output_dir: Path):
    header("Copy output libraries")
    output_dir.mkdir(parents=True, exist_ok=True)

    out_dir = skia_out_dir()

    for lib_name in SKIA_OUTPUT_LIBS:
        source = out_dir / lib_name
        target = output_dir / lib_name
        cp(source, target)

    harfbuzz_lib = harfbuzz_output_lib()
    target = output_dir / harfbuzz_lib.name
    cp(harfbuzz_lib, target)


def build_all():
    if os.name != "nt":
        error("This script is intended for Windows only")

    output_dir = resolve_output_dir()

    build_skia()
    build_harfbuzz()
    copy_outputs(output_dir)
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
        "-o",
        "--output-dir",
        default=None,
        help="Directory to copy built libs (default: artifacts/skiasharp-2.88.9/win-<arch>)",
    )
    parser.add_argument(
        "--skip-sync-deps", action="store_true", help="skip running tools/git-sync-deps"
    )
    return parser.parse_args()


def main():
    global args
    args = parse_args()
    args.func()


if __name__ == "__main__":
    main()
