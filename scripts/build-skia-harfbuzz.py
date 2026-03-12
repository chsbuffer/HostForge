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

args: argparse.Namespace


def execv(cmds, cwd: Path | None = None, env=None):
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
    full_cmd = f"call {INIT_VS_ENV_CMD} {arch} && cd /d {cwd} && {cmd}"
    exec_cmd(full_cmd, env=env)


def cp(source: Path, target: Path):
    vprint(f"cp {source} -> {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parent

VC_COMPILER_VER = os.environ.get("VC_COMPILER_VER", "14.5")
VC_TOOLSET_VER = os.environ.get("VC_TOOLSET_VER", "v145")
WINDOWS_SDK_VER = os.environ.get("WINDOWS_SDK_VER", "10.0.26100.0")

DEFAULT_VERSION = "2.88.9"

SKIA_ROOT: Path
SKIA_BUILD_DIR: Path
HARFBUZZ_BUILD_DIR: Path

INIT_VS_ENV_CMD = SCRIPT_ROOT / "init-vs-env.cmd"

HARFBUZZ_SLN_DIR: Path
HARFBUZZ_PROJECT_FILE: Path

OUTDIR: Path

SKIA_OUTPUT_LIBS = [
    "SkiaSharp",
    "skia",
    "skottie",
    "sksg",
    "skshaper",
    "skresources",
]

SKIA_OUTPUT_LIBS_WIN = [f"{x}.lib" for x in SKIA_OUTPUT_LIBS]
SKIA_OUTPUT_LIBS_LINUX = [f"lib{x}.a" for x in SKIA_OUTPUT_LIBS]

HARFBUZZ_PLATFORM = {
    "x64": "x64",
    "arm64": "ARM64",
}

GN_CPU = {
    "x64": "x64",
    "arm64": "arm64",
}


def target_rid() -> str:
    prefix = "win" if args.os == "windows" else "linux"
    return f"{prefix}-{args.arch}"


def harfbuzz_output_lib() -> Path:
    platform_name = HARFBUZZ_PLATFORM[args.arch]
    if args.os == "windows":
        return (
            HARFBUZZ_SLN_DIR
            / "bin"
            / platform_name
            / "Release"
            / "libHarfBuzzSharp.lib"
        )
    else:
        return HARFBUZZ_BUILD_DIR / "libHarfBuzzSharp.a"


def compiler_args_gn() -> list[str]:
    pairs = [
        ("cc", os.environ.get("CC")),
        ("cxx", os.environ.get("CXX")),
        ("ar", os.environ.get("AR")),
    ]
    return [f'{name} = "{value}"' for name, value in pairs if value]


def gn() -> str | Path:
    # if args.os == "windows":
    #     return SKIA_ROOT / "bin" / "gn.exe"
    return SKIA_ROOT / "bin" / "gn"


def write_windows_args_gn(target: Path):
    cpu = GN_CPU[args.arch]
    extra_cflags = [
        '"-DSKIA_C_DLL"',
        '"/MT"',
        '"/EHsc"',
        '"/Z7"',
    ]
    extra_ldflags = [
        '"/DEBUG:FULL"',
        '"/DEBUGTYPE:CV,FIXUP"',
    ]
    lines = [
        'target_os = "win"',
        f'target_cpu = "{cpu}"',
        "skia_enable_fontmgr_win_gdi = false",
        "skia_use_dng_sdk = true",
        "skia_use_icu = false",
        "skia_use_piex = true",
        "skia_use_sfntly = false",
        "skia_use_system_expat = false",
        "skia_use_system_libjpeg_turbo = false",
        "skia_use_system_libpng = false",
        "skia_use_system_libwebp = false",
        "skia_use_system_zlib = false",
        "skia_enable_skottie = true",
        "is_static_skiasharp = true",
        "skia_use_vulkan = true",
        'clang_win = "C:/Program Files/LLVM"',
        f'win_vcvars_version = "{VC_COMPILER_VER}"',
        "skia_enable_tools = false",
        "is_official_build = true",
    ]

    if args.version != "2.88.9":
        lines.insert(4, "skia_use_harfbuzz = false")
        extra_cflags.append('"/guard:cf"')
        extra_ldflags.append('"/guard:cf"')

    extra_cflags.extend(
        [
            '"-D_HAS_AUTO_PTR_ETC=1"',
            '"-D_SILENCE_ALL_CXX17_DEPRECATION_WARNINGS=1"',
        ]
    )
    if args.arch == "x64":
        extra_cflags.append('"/arch:AVX2"')

    lines.extend(
        [
            f"extra_cflags = [ {', '.join(extra_cflags)} ]",
            f"extra_ldflags = [ {', '.join(extra_ldflags)} ]",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_linux_skia_args_gn(target: Path):
    cpu = GN_CPU[args.arch]
    lines = [
        'target_os = "linux"',
        f'target_cpu = "{cpu}"',
        "is_official_build = true",
        "skia_enable_tools = false",
        "is_static_skiasharp = true",
        "skia_use_icu = false",
        "skia_use_piex = true",
        "skia_use_sfntly = false",
        "skia_use_system_expat = false",
        "skia_use_system_freetype2 = false",
        "skia_use_system_libjpeg_turbo = false",
        "skia_use_system_libpng = false",
        "skia_use_system_libwebp = false",
        "skia_use_system_zlib = false",
        "skia_enable_skottie = true",
        "skia_use_vulkan = true",
        "skia_use_harfbuzz = false",
        'extra_cflags = [ "-DSKIA_C_DLL", "-DHAVE_SYSCALL_GETRANDOM", "-DXML_DEV_URANDOM" ]',
        "extra_ldflags = []",
    ]
    if args.version != "2.88.9":
        lines.append("skia_enable_ganesh = true")
    lines.extend(compiler_args_gn())
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_linux_harfbuzz_args_gn(target: Path):
    cpu = GN_CPU[args.arch]
    lines = [
        'target_os = "linux"',
        f'target_cpu = "{cpu}"',
        "is_official_build = true",
        "is_static_skiasharp = true",
        "skia_enable_tools = false",
        "visibility_hidden = false",
        "extra_asmflags = []",
        "extra_cflags = []",
        "extra_ldflags = []",
    ]
    lines.extend(compiler_args_gn())
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_skia_windows():
    header("Build SkiaSharp")
    SKIA_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    write_windows_args_gn(SKIA_BUILD_DIR / "args.gn")

    if not args.skip_sync_deps:
        execv([sys.executable, SKIA_ROOT / "tools/git-sync-deps"], cwd=SKIA_ROOT)

    execv([gn(), "gen", SKIA_BUILD_DIR], cwd=SKIA_ROOT)
    execv(["ninja", "-C", SKIA_BUILD_DIR, "skia", "SkiaSharp"])


def build_skia_linux():
    header("Build SkiaSharp")
    SKIA_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    write_linux_skia_args_gn(SKIA_BUILD_DIR / "args.gn")

    if not args.skip_sync_deps:
        execv([sys.executable, SKIA_ROOT / "tools/git-sync-deps"], cwd=SKIA_ROOT)

    execv([gn(), "gen", SKIA_BUILD_DIR], cwd=SKIA_ROOT)
    execv(["ninja", "-C", SKIA_BUILD_DIR, "SkiaSharp"], cwd=SKIA_ROOT)


def build_harfbuzz_windows():
    header("Build HarfBuzzSharp")
    HARFBUZZ_SLN_DIR.mkdir(parents=True, exist_ok=True)

    harfbuzz_project_file_in = SCRIPT_ROOT / "libHarfBuzzSharp.vcxproj.in"
    project_file = HARFBUZZ_SLN_DIR / "libHarfBuzzSharp.vcxproj"
    platform_name = HARFBUZZ_PLATFORM[args.arch]
    copy_template(
        harfbuzz_project_file_in,
        project_file,
        {
            "VC_TOOLSET_VER": VC_TOOLSET_VER,
            "WINDOWS_SDK_VER": WINDOWS_SDK_VER,
            "SKIA_ROOT": str(SKIA_ROOT),
        },
        encoding="utf-8-sig",
    )
    run_in_vs_env(
        f"msbuild {project_file} -m /p:Configuration=Release /p:Platform={platform_name}",
        cwd=HARFBUZZ_SLN_DIR,
        arch=args.arch,
    )


def build_harfbuzz_linux():
    header("Build HarfBuzzSharp")
    HARFBUZZ_BUILD_DIR.mkdir(parents=True, exist_ok=True)
    write_linux_harfbuzz_args_gn(HARFBUZZ_BUILD_DIR / "args.gn")
    execv([gn(), "gen", HARFBUZZ_BUILD_DIR], cwd=SKIA_ROOT)
    execv(["ninja", "-C", HARFBUZZ_BUILD_DIR, "HarfBuzzSharp"], cwd=SKIA_ROOT)


def copy_outputs():
    header("Copy output libraries")
    OUTDIR.mkdir(parents=True, exist_ok=True)

    for lib_name in (
        SKIA_OUTPUT_LIBS_WIN if args.os == "windows" else SKIA_OUTPUT_LIBS_LINUX
    ):
        cp(SKIA_BUILD_DIR / lib_name, OUTDIR / lib_name)

    harfbuzz_lib = harfbuzz_output_lib()
    cp(harfbuzz_lib, OUTDIR / harfbuzz_lib.name)


def check_os():
    host_os = "windows" if is_windows else os_name
    if host_os != args.os:
        error("cross-compile is unsupported.")


def build_all():
    check_os()
    if args.os == "windows":
        build_skia_windows()
        build_harfbuzz_windows()
    else:
        build_skia_linux()
        build_harfbuzz_linux()
    copy_outputs()
    print("\nDone.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build SkiaSharp + HarfBuzz libs for HostForge.",
        epilog="""Environment Variables:
    VC_COMPILER_VER:\t(default: 14.5)
    VC_TOOLSET_VER :\t(default: v145)
    WINDOWS_SDK_VER:\t(default: 10.0.26100.0)
    CC/CXX/AR      :\t(optional Linux toolchain overrides)""",
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
        "--os",
        choices=["windows", "linux"],
        default="windows" if is_windows else "linux",
        help="target os",
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
    global SKIA_ROOT, SKIA_BUILD_DIR, HARFBUZZ_BUILD_DIR, OUTDIR, HARFBUZZ_SLN_DIR

    SKIA_ROOT = REPO_ROOT / "repo" / f"skia-{args.version}"
    if args.os == "windows":
        SKIA_BUILD_DIR = SKIA_ROOT / "out" / "windows" / args.arch
        HARFBUZZ_BUILD_DIR = SKIA_ROOT / "out" / "windows" / args.arch
    else:
        SKIA_BUILD_DIR = SKIA_ROOT / "out" / "linux" / args.arch / "skiasharp"
        HARFBUZZ_BUILD_DIR = SKIA_ROOT / "out" / "linux" / args.arch / "harfbuzz"

    HARFBUZZ_SLN_DIR = REPO_ROOT / "repo" / f"HarfBuzzSharp-{args.version}"
    OUTDIR = REPO_ROOT / "artifacts" / "skiasharp" / args.version / target_rid()

    if not SKIA_ROOT.exists():
        error("source code not found; checkout-deps first.")

    args.func()


if __name__ == "__main__":
    main()
