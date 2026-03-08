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
    vprint(f"cp {source} -> {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def execv(cmd: str, cwd: Path | None = None, env=None):
    out = None if args.verbose > 0 else subprocess.DEVNULL
    if args.verbose > 0:
        print(cmd)
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", cmd] if is_windows else ["bash", "-c", cmd],
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


def get_rid() -> str:
    os = "win" if args.os == "windows" else args.os
    return f"{os}-{args.arch}"


def run_in_vs_env(cmd: str, cwd: Path, arch: str):
    full_cmd = (
        f"call {RUNTIME_ROOT}\\eng\\native\\init-vs-env.cmd {arch} && "
        f"cd /d {cwd} && "
        f"{cmd}"
    )
    execv(full_cmd)


def resolve_token_source(build_root: Path, token: str) -> Path | None:
    normalized = token.replace("\\", "/")

    if normalized.startswith("/") or (
        len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/"
    ):
        candidate = Path(normalized)
    else:
        candidate = build_root / Path(normalized)

    return candidate if candidate.exists() else None


def get_token_output_path(token: str) -> Path:
    normalized = token.replace("\\", "/")

    if len(normalized) >= 2 and normalized[1] == ":":
        normalized = f"{normalized[0]}{normalized[2:]}"

    normalized = normalized.lstrip("/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts:
        error(f"Cannot derive output path from token: {token}")

    return Path(*parts)


def write_rsp_file(output: Path, name: str, tokens: list[str]):
    rsp_path = output / f"{name}.rsp"
    rsp_path.write_text("\n".join(tokens) + "\n", encoding="utf-8")
    vprint(f"write {rsp_path}")


def write_link_flags_file(output: Path, name: str, link_flags: str):
    link_flags_path = output / f"{name}.linkflags"
    link_flags_path.write_text(link_flags + "\n", encoding="utf-8")
    vprint(f"write {link_flags_path}")


def parse_link_rule_from_ninja(
    build_root: Path, name: str
) -> tuple[list[str], str, str]:
    target_outputs = {
        "apphost": {
            "apphost/standalone/apphost",
            "apphost/standalone/apphost.exe",
        },
        "singlefilehost": {
            "Corehost.Static/singlefilehost",
            "Corehost.Static/singlefilehost.exe",
        },
    }.get(name)

    if not target_outputs:
        error(f"Unsupported target for ninja parsing: {name}")

    build_ninja = build_root / "build.ninja"

    if not build_ninja.exists():
        error(f"build.ninja not found: {build_ninja}")

    lines = build_ninja.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("build "):
            continue

        head, sep, tail = line.partition(": ")
        if not sep:
            continue

        outputs = {
            output.replace("\\", "/") for output in head.removeprefix("build ").split()
        }
        if outputs.isdisjoint(target_outputs):
            continue
        tokens = tail.split()
        if len(tokens) < 2:
            error(f"Malformed ninja build rule: {line}")

        objs = []
        for token in tokens[1:]:
            if token == "|":
                break
            objs.append(token)

        link_libs = []
        link_flags = ""
        flags = ""
        for j in range(i + 1, min(i + 16, len(lines))):
            if lines[j].startswith("  LINK_LIBRARIES = "):
                link_libs = lines[j].split("=", 1)[1].strip().split()
            elif lines[j].startswith("  LINK_FLAGS = "):
                link_flags = lines[j].split("=", 1)[1].strip()
            elif lines[j].startswith("  FLAGS = "):
                flags = lines[j].split("=", 1)[1].strip()

        if not objs:
            error(f"No object files parsed from ninja for {name}")
        if not link_libs:
            error(f"No LINK_LIBRARIES parsed from ninja for {name}")
        if not link_flags:
            error(f"No LINK_FLAGS parsed from ninja for {name}")

        return [*objs, *link_libs], link_flags, flags

    error(
        f"Target rule not found in {build_ninja}: {', '.join(sorted(target_outputs))}"
    )


def bundle_intermediates(name: str, build_root: Path, output: Path, arch: str):
    output.mkdir(parents=True, exist_ok=True)
    intermediates, link_flags, flags = parse_link_rule_from_ninja(build_root, name)
    copied = 0
    missing = []
    copied_outputs: set[Path] = set()

    header("Copy link inputs")
    for token in intermediates:
        source = resolve_token_source(build_root, token)
        if source is None:
            missing.append(token)
            continue

        target = output / get_token_output_path(token)
        if target in copied_outputs:
            continue

        cp(source, target)
        copied_outputs.add(target)
        copied += 1

    write_rsp_file(output, name, intermediates)
    write_link_flags_file(output, name, f"{flags}\n{link_flags}")

    vprint(f"copied files: {copied}")
    if missing:
        vprint("missing tokens")
        vprint("\n".join(missing))


def bundle_target(name: str, build_root: Path, output: Path, arch: str):
    bundle_intermediates(name, build_root, output, arch)


def check_os():
    host_os = platform.system().lower()
    target_os = args.os

    if host_os != target_os:
        error("cross-compile is unsupported.")


def build_singlefilehost():
    os = args.os
    arch = args.arch

    if not args.skip_build:
        header("Build runtime")
        check_os()
        build_args = f"clr.runtime -ninja -c release -arch {arch}"
        if is_windows:
            execv(f"call {RUNTIME_ROOT}\\build.cmd {build_args}")
        else:
            execv(f"{RUNTIME_ROOT}/build.sh {build_args}")

    build_root = RUNTIME_ROOT / "artifacts" / "obj" / "coreclr" / f"{os}.{arch}.Release"

    header("Archive singlefilehost")
    output = HOSTLIBS_ROOT / get_rid()
    bundle_target("singlefilehost", build_root, output, arch)
    match args.os:
        case "windows":
            SINGLEFILEHOST_DEF = (
                RUNTIME_ROOT
                / "src"
                / "native"
                / "corehost"
                / "apphost"
                / "static"
                / "singlefilehost.def"
            )
            cp(SINGLEFILEHOST_DEF, output / SINGLEFILEHOST_DEF.name)
        case "linux":
            cp(
                build_root / "Corehost.Static" / "singlefilehost.exports",
                output / "singlefilehost.exports",
            )


def build_apphost():
    os = args.os
    arch = args.arch

    if not args.skip_build:
        header("Build corehost")
        check_os()
        build_args = f"host.native -ninja -c release -arch {arch}"
        if is_windows:
            execv(f"call {RUNTIME_ROOT}\\build.cmd {build_args}")
        else:
            execv(f"{RUNTIME_ROOT}/build.sh {build_args}")

    if os == "windows":
        build_root = (
            RUNTIME_ROOT / "artifacts" / "obj" / f"{get_rid()}.Release" / "corehost"
        )
    else:
        build_root = RUNTIME_ROOT / "artifacts" / "obj" / f"{get_rid()}.Release"

    header("Archive corehost")
    output = HOSTLIBS_ROOT / get_rid()
    bundle_target("apphost", build_root, output, arch)


def build_all():
    build_apphost()
    build_singlefilehost()


##################
# Parse arguments
##################


def parse_args():
    parser = argparse.ArgumentParser(
        description=".NET App Host LIB build and archive script"
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    parser.add_argument(
        "target",
        nargs="?",
        choices=["apphost", "singlefilehost", "all"],
        default="all",
        help="build and archive target",
    )
    parser.add_argument(
        "-a",
        "--arch",
        choices=["x86", "x64", "arm64"],
        default="x64",
        help="target architecture",
    )
    parser.add_argument(
        "--os",
        choices=["windows", "linux"],
        default="windows" if is_windows else "linux",
        help="target os",
    )
    parser.add_argument("--skip-build", action="store_true", help="skip build")

    return parser.parse_args()


def main():
    global args
    args = parse_args()

    if args.target == "apphost":
        build_apphost()
    elif args.target == "singlefilehost":
        build_singlefilehost()
    else:
        build_all()


if __name__ == "__main__":
    main()
