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
SINGLEFILEHOST_DEF = (
    RUNTIME_ROOT
    / "src"
    / "native"
    / "corehost"
    / "apphost"
    / "static"
    / "singlefilehost.def"
)
def get_arch() -> str:
    return getattr(args, "sub_arch", None) or args.arch


def get_rid() -> str:
    _os = "win" if is_windows else "linux"
    return f"{_os}-{get_arch()}"


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


def parse_link_rule_from_ninja(build_root: Path, name: str) -> tuple[list[str], str]:
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

    build_ninja = build_root if build_root.suffix == ".ninja" else build_root / "build.ninja"
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
            output.replace("\\", "/")
            for output in head.removeprefix("build ").split()
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
        for j in range(i + 1, min(i + 16, len(lines))):
            if lines[j].startswith("  LINK_LIBRARIES = "):
                link_libs = lines[j].split("=", 1)[1].strip().split()
            elif lines[j].startswith("  LINK_FLAGS = "):
                link_flags = lines[j].split("=", 1)[1].strip()

        if not objs:
            error(f"No object files parsed from ninja for {name}")
        if not link_libs:
            error(f"No LINK_LIBRARIES parsed from ninja for {name}")
        if not link_flags:
            error(f"No LINK_FLAGS parsed from ninja for {name}")

        return [*objs, *link_libs], link_flags

    error(
        f"Target rule not found in {build_ninja}: {', '.join(sorted(target_outputs))}"
    )


def parse_link_inputs_from_ninja(build_root: Path, name: str) -> list[str]:
    inputs, _ = parse_link_rule_from_ninja(build_root, name)
    return inputs


def bundle_intermediates(name: str, build_root: Path, output: Path, arch: str):
    output.mkdir(parents=True, exist_ok=True)
    intermediates, link_flags = parse_link_rule_from_ninja(build_root, name)
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
    write_link_flags_file(output, name, link_flags)

    vprint(f"copied files: {copied}")
    if missing:
        vprint("missing tokens")
        vprint("\n".join(missing))


def bundle_target(name: str, build_root: Path, output: Path, arch: str):
    bundle_intermediates(name, build_root, output, arch)


def build_singlefilehost():
    arch = get_arch()
    header("Configure runtime")
    build_args = f"clr.runtime -ninja -c release -arch {arch}"
    if is_windows:
        execv(f"call {RUNTIME_ROOT}\\build.cmd {build_args}")
    else:
        execv(f"{RUNTIME_ROOT}/build.sh {build_args}")

    build_root = (
        RUNTIME_ROOT / "artifacts" / "obj" / "coreclr" / f"windows.{arch}.Release"
    )
    output = HOSTLIBS_ROOT / get_rid()
    bundle_target("singlefilehost", build_root, output, arch)
    if is_windows:
        cp(SINGLEFILEHOST_DEF, output / SINGLEFILEHOST_DEF.name)


def build_apphost():
    arch = get_arch()
    header("Configure corehost")
    build_args = f"host.native -ninja -c release -arch {arch}"
    if is_windows:
        execv(f"call {RUNTIME_ROOT}\\build.cmd {build_args}")
        build_root = (
            RUNTIME_ROOT / "artifacts" / "obj" / f"{get_rid()}.Release" / "corehost"
        )
    else:
        execv(f"{RUNTIME_ROOT}/build.sh {build_args}")
        build_root = RUNTIME_ROOT / "artifacts" / "obj" / f"{get_rid()}.Release"

    output = HOSTLIBS_ROOT / get_rid()
    bundle_target("apphost", build_root, output, arch)


def build_all():
    build_apphost()
    build_singlefilehost()


##################
# Parse arguments
##################


def parse_args():
    parser = argparse.ArgumentParser(description=".NET App Host LIB build script")
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    parser.add_argument(
        "-a",
        "--arch",
        choices=["x86", "x64", "arm64"],
        default="x64",
        help="target architecture for the default command",
    )
    parser.set_defaults(func=build_all)

    subparsers = parser.add_subparsers(dest="command")

    apphost_parser = subparsers.add_parser(
        "apphost", help="build and bundle apphost intermediates"
    )
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

    all_parser = subparsers.add_parser(
        "all", help="build apphost + singlefilehost host libs"
    )
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
