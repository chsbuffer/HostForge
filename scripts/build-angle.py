#!/usr/bin/env python3
import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def error(value):
    print(f"\n! {value}\n")
    sys.exit(1)


def header(value):
    print(f"\n* {value}\n")


def vprint(value):
    if args.verbose:
        print(value)


is_windows = platform.system().lower() == "windows"

if sys.version_info < (3, 10):
    error("Requires Python 3.10+")


SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parent
DEPS_PATCH_FILE = SCRIPT_ROOT / "angle-deps.patch"
STATIC_PATCH_FILE = SCRIPT_ROOT / "angle-static.patch"
GN_CPU = {"x64": "x64", "arm64": "arm64"}
STATIC_LIBRARIES = ("libANGLE_static.lib", "libGLESv2_static.lib")


def load_angle_dep() -> tuple[str, str]:
    deps = {}
    with (REPO_ROOT / "DEPS").open(encoding="utf-8") as handle:
        exec(handle.read(), deps)
    entry = deps["angle"]
    return entry["version"], entry["angle"]


def execv(command, cwd: Path | None = None, env=None, capture=False):
    vprint(" ".join(str(part) for part in command))
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        shell=is_windows,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=capture,
    )
    if process.returncode != 0:
        detail = ""
        if capture:
            detail = f"\n{process.stdout}{process.stderr}".rstrip()
        error(
            f"Command failed ({process.returncode}): "
            f"{' '.join(str(part) for part in command)}{detail}"
        )
    return process


def require_tool(name: str):
    if not shutil.which(name):
        error(f"{name} was not found on PATH. Install depot_tools first.")


def apply_patch(
    patch_file: Path,
    source_file: Path,
    marker: str,
    marker_means_unapplied: bool,
    description: str,
):
    marker_present = marker in source_file.read_text(encoding="utf-8")
    if marker_present != marker_means_unapplied:
        return

    header(description)
    execv(["git", "apply", "--check", patch_file], cwd=angle_root)
    execv(["git", "apply", patch_file], cwd=angle_root)


def apply_build_patches():
    apply_patch(
        DEPS_PATCH_FILE,
        angle_root / "DEPS",
        "'third_party/catapult':",
        True,
        "Prune unused ANGLE checkout dependencies",
    )
    apply_patch(
        STATIC_PATCH_FILE,
        angle_root / "BUILD.gn",
        'angle_static_library("libANGLE_static")',
        False,
        "Add ANGLE complete static-library targets",
    )


def sync_dependencies():
    header("Bootstrap and sync ANGLE dependencies")
    env = dict(os.environ)
    env["DEPOT_TOOLS_WIN_TOOLCHAIN"] = "0"
    execv([sys.executable, angle_root / "scripts" / "bootstrap.py"], cwd=angle_root, env=env)
    execv(["gclient", "sync", "-f", "-D", "-R"], cwd=angle_root, env=env)


def write_args_gn():
    build_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "is_debug=false",
        "is_component_build=false",
        "is_clang=false",
        "angle_is_msvc=true",
        "symbol_level=0",
        "use_custom_libcxx=false",
        "use_lld=false",
        "use_thin_lto=false",
        f'target_cpu="{GN_CPU[args.arch]}"',
        "dcheck_always_on=false",
        "angle_enable_vulkan=false",
        "angle_enable_gl=false",
        "angle_enable_null=false",
        "angle_assert_always_on=false",
        "angle_enable_d3d9=false",
        "angle_enable_metal=false",
        "angle_enable_gl_desktop_backend=false",
        "angle_enable_wgpu=false",
        "angle_enable_swiftshader=false",
        "angle_build_tests=false",
        "build_angle_deqp_tests=false",
    ]
    (build_dir / "args.gn").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_static_libraries():
    header(f"Build ANGLE static libraries ({args.arch})")
    write_args_gn()
    execv(["gn", "gen", build_dir], cwd=angle_root)
    execv(
        ["autoninja", "-C", build_dir, "libANGLE_static", "libGLESv2_static"],
        cwd=angle_root,
    )


def copy_outputs():
    header("Collect ANGLE link inputs")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    object_dir = build_dir / "obj"
    for library_name in STATIC_LIBRARIES:
        source = object_dir / library_name
        if not source.exists():
            error(f"Expected static library was not produced: {source}")
        target = output_dir / library_name
        vprint(f"cp {source} -> {target}")
        shutil.copy2(source, target)

    source_def = angle_root / "src" / "libGLESv2" / "libGLESv2_autogen.def"
    target_def = output_dir / "av_libglesv2.def"
    definition_lines = source_def.read_text(encoding="utf-8").splitlines()
    target_def.write_text(
        "\n".join(
            line
            for line in definition_lines
            if not line.strip().upper().startswith("LIBRARY ")
        )
        + "\n",
        encoding="utf-8",
    )
    print("Collected av_libglesv2 export definition.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build complete ANGLE static libraries for HostForge."
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("-a", "--arch", choices=GN_CPU, default="x64")
    parser.add_argument(
        "--skip-sync-deps",
        action="store_true",
        help="skip ANGLE bootstrap and gclient sync",
    )
    return parser.parse_args()


def main():
    global args, angle_root, build_dir, output_dir
    args = parse_args()
    if not is_windows:
        error("ANGLE Windows static libraries must be built on Windows.")
    require_tool("gn")
    require_tool("autoninja")
    os.environ["DEPOT_TOOLS_WIN_TOOLCHAIN"] = "0"

    version, expected_commit = load_angle_dep()
    angle_root = REPO_ROOT / "repo" / f"angle-{version}"
    build_dir = angle_root / "out" / "windows" / args.arch
    output_dir = REPO_ROOT / "artifacts" / "angle" / version / f"win-{args.arch}"
    if not angle_root.exists():
        error("ANGLE source code not found; run checkout-deps.py angle first.")
    actual_commit = execv(
        ["git", "rev-parse", "HEAD"], cwd=angle_root, capture=True
    ).stdout.strip()
    if actual_commit != expected_commit:
        error(f"ANGLE checkout is {actual_commit}; expected {expected_commit}.")

    apply_build_patches()
    if not args.skip_sync_deps:
        sync_dependencies()
    build_static_libraries()
    copy_outputs()
    print("\nDone.")


if __name__ == "__main__":
    main()
