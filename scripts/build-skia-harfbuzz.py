#!/usr/bin/env python3
import argparse
import os
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


def exec_cmd(cmd: str, cwd: Path | None = None, env=None):
    if args.verbose > 0:
        print(cmd)
    proc = subprocess.run(["cmd.exe", "/d", "/c", cmd], cwd=cwd, env=env, shell=False)
    if proc.returncode != 0:
        error(f"Command failed ({proc.returncode}): {cmd}")
    return proc


def run_in_vs_env(cmd: str, cwd: Path, arch: str, env=None):
    full_cmd = (
        f'call "{INIT_VS_ENV_CMD}" {arch} && '
        f'cd /d "{cwd}" && '
        f"{cmd}"
    )
    exec_cmd(full_cmd, env=env)


def resolve_python() -> list[str]:
    if shutil.which("py"):
        return ["py", "-3"]
    if shutil.which("python"):
        return ["python"]
    error("Python not found in PATH")


###############
# Build logic
###############

SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parent

SKIASHARP_VERSION = "2.88.9"
SKIASHARP_ROOT = REPO_ROOT / "repo" / "SkiaSharp"
SKIA_ROOT = SKIASHARP_ROOT / "externals" / "skia"
SKIA_OUT_DIR = SKIA_ROOT / "out" / "windows" / "x64"

PATCH_ARGS_GN = REPO_ROOT / "repo" / "patch" / "args.gn"
PATCH_HARFBUZZ_VCXPROJ = REPO_ROOT / "repo" / "patch" / "libHarfBuzzSharp.vcxproj"

HARFBUZZ_PROJECT_DIR = SKIASHARP_ROOT / "native" / "windows" / "libHarfBuzzSharp"
HARFBUZZ_PROJECT_FILE = HARFBUZZ_PROJECT_DIR / "libHarfBuzzSharp.vcxproj"
HARFBUZZ_OUTPUT_LIB = HARFBUZZ_PROJECT_DIR / "bin" / "x64" / "Release" / "libHarfBuzzSharp.lib"
INIT_VS_ENV_CMD = SCRIPT_ROOT / "init-vs-env.cmd"

DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / f"skiasharp-{SKIASHARP_VERSION}" / "win-x64"
SKIA_OUTPUT_LIBS = ["SkiaSharp.lib", "skia.lib", "skottie.lib", "sksg.lib", "skshaper.lib"]


def ensure_local_depot_tools_in_path(env):
    depot_tools = SKIASHARP_ROOT / "externals" / "depot_tools"
    if not depot_tools.exists():
        return
    env["PATH"] = f"{SKIA_ROOT / 'bin'}{os.pathsep}{depot_tools}{os.pathsep}{env.get('PATH', '')}"


def build_skia(env, python_cmd):
    header("Build SkiaSharp")
    SKIA_OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PATCH_ARGS_GN, SKIA_OUT_DIR / "args.gn")

    if not args.skip_sync_deps:
        execv([*python_cmd, "tools/git-sync-deps"], cwd=SKIA_ROOT, env=env)

    execv(["gn.exe", "gen", str(SKIA_OUT_DIR)], cwd=SKIA_ROOT, env=env)
    execv(["ninja.exe", "-C", str(SKIA_OUT_DIR), "skia", "SkiaSharp"], cwd=SKIA_ROOT, env=env)


def build_harfbuzz(env):
    header("Build HarfBuzzSharp")
    shutil.copy2(PATCH_HARFBUZZ_VCXPROJ, HARFBUZZ_PROJECT_FILE)
    run_in_vs_env(
        f'msbuild "{HARFBUZZ_PROJECT_FILE}" -m /p:Configuration=Release;Platform=x64',
        cwd=HARFBUZZ_PROJECT_DIR,
        arch="x64",
        env=env,
    )


def copy_outputs(output_dir: Path):
    header("Copy output libraries")
    output_dir.mkdir(parents=True, exist_ok=True)

    for lib_name in SKIA_OUTPUT_LIBS:
        source = SKIA_OUT_DIR / "windows" / "x64" / lib_name
        target = output_dir / lib_name
        shutil.copy2(source, target)
        vprint(f"cp {source} -> {target}")

    target = output_dir / HARFBUZZ_OUTPUT_LIB.name
    shutil.copy2(HARFBUZZ_OUTPUT_LIB, target)
    vprint(f"cp {HARFBUZZ_OUTPUT_LIB} -> {target}")


def build_all():
    if os.name != "nt":
        error("This script is intended for Windows only")

    env = os.environ.copy()
    ensure_local_depot_tools_in_path(env)
    python_cmd = resolve_python()
    output_dir = Path(args.output_dir).resolve()

    build_skia(env, python_cmd)
    build_harfbuzz(env)
    copy_outputs(output_dir)
    print("\nDone.")


def parse_args():
    parser = argparse.ArgumentParser(description="Build SkiaSharp + HarfBuzz static libs for HostForge.")
    parser.set_defaults(func=build_all)
    parser.add_argument("-v", "--verbose", action="count", default=0, help="verbose output")
    parser.add_argument(
        "-o",
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory to copy built libs (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument("--skip-sync-deps", action="store_true", help="skip running tools/git-sync-deps")
    return parser.parse_args()


def main():
    global args
    args = parse_args()
    args.func()


if __name__ == "__main__":
    main()
