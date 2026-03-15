#!/usr/bin/env python3
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_WINDOWS_CLANG = Path(r"C:\Program Files\LLVM\bin\clang.exe")


class CacheKeyError(Exception):
    pass


def load_deps(deps_file: Path) -> dict:
    deps = {}
    with deps_file.open(encoding="utf-8") as handle:
        exec(handle.read(), deps)
    return deps


def vctools_version() -> str:
    return os.environ.get("VCToolsVersion", "unknown")


def resolve_clang_exe(os_name: str, clang_path: str | None) -> Path:
    if clang_path:
        return Path(clang_path)
    if os_name != "windows":
        clang = shutil.which("clang")
        if clang:
            return Path(clang)
    return DEFAULT_WINDOWS_CLANG


def resolve_gcc_exe(os_name: str, gcc_path: str | None) -> Path:
    if gcc_path:
        return Path(gcc_path)
    if os_name != "windows":
        gcc = shutil.which("gcc")
        if gcc:
            return Path(gcc)
    return Path("gcc")


def tool_version(exe: Path) -> str:
    resolved = exe
    if not resolved.exists():
        from_path = shutil.which(str(exe))
        if from_path:
            resolved = Path(from_path)
        else:
            raise CacheKeyError(f"tool not found: {exe}")
    proc = subprocess.run(
        [str(resolved), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    if proc.returncode != 0:
        raise CacheKeyError(f"Command failed ({proc.returncode}): {resolved} --version")
    output = (proc.stdout or proc.stderr).strip()
    if not output:
        raise CacheKeyError(f"Failed to read tool version: {resolved}")
    return output.splitlines()[0]


def hostlibs_cache_key(
    deps_file: Path,
    os_name: str,
    arch: str,
    runtime_version: str,
    hostlibs_flavor: str = "default",
    clang_path: str | None = None,
) -> tuple[str, list[str]]:
    deps = load_deps(deps_file)
    runtime_versions = deps.get("runtime", {})
    if runtime_version not in runtime_versions:
        raise CacheKeyError(f"Unknown runtime version: {runtime_version}")

    runtime_commit = runtime_versions[runtime_version]["runtime"]
    inputs = [
        f"os={os_name}",
        f"arch={arch}",
        f"runtime_version={runtime_version}",
        f"hostlibs_flavor={hostlibs_flavor}",
        f"runtime_commit={runtime_commit}",
    ]
    if os_name == "windows":
        inputs.append(f"vctoolsversion={vctools_version()}")
    else:
        clang_exe = resolve_clang_exe(os_name, clang_path)
        inputs.append(f"clang={tool_version(clang_exe)}")
    digest = hashlib.sha256("\n".join(inputs).encode("utf-8")).hexdigest()
    return (
        f"hostlibs-{os_name}-{arch}-{runtime_version}-{hostlibs_flavor}-{digest[:16]}",
        inputs,
    )


def skiasharp_cache_key(
    deps_file: Path,
    os_name: str,
    arch: str,
    skiasharp_version: str,
    clang_path: str | None = None,
    gcc_path: str | None = None,
) -> tuple[str, list[str]]:
    deps = load_deps(deps_file)
    skia_versions = deps.get("skiasharp", {})
    if skiasharp_version not in skia_versions:
        raise CacheKeyError(f"Unknown skiasharp version: {skiasharp_version}")

    entry = skia_versions[skiasharp_version]
    skia_commit = entry["skia"]
    inputs = [
        f"os={os_name}",
        f"arch={arch}",
        f"skiasharp_version={skiasharp_version}",
        f"skia_commit={skia_commit}",
    ]
    if os_name == "windows":
        clang_exe = resolve_clang_exe(os_name, clang_path)
        inputs.extend(
            [
                f"vctoolsversion={vctools_version()}",
                f"clang={tool_version(clang_exe)}",
            ]
        )
    else:
        if clang_path:
            clang_exe = resolve_clang_exe(os_name, clang_path)
            inputs.append(f"clang={tool_version(clang_exe)}")
        else:
            gcc_exe = resolve_gcc_exe(os_name, gcc_path)
            inputs.append(f"gcc={tool_version(gcc_exe)}")
    digest = hashlib.sha256("\n".join(inputs).encode("utf-8")).hexdigest()
    return f"skiasharp-{os_name}-{skiasharp_version}-{arch}-{digest[:16]}", inputs


def parse_args():
    parser = argparse.ArgumentParser(description="Compute build cache keys")
    parser.add_argument("--kind", choices=["hostlibs", "skiasharp"], required=True)
    parser.add_argument("--arch", choices=["x64", "arm64"], required=True)
    parser.add_argument("--os", choices=["windows", "linux"], default="windows")
    parser.add_argument("--runtime-version", default=None)
    parser.add_argument("--skiasharp-version", default=None)
    parser.add_argument(
        "--hostlibs-flavor",
        choices=["default", "no-pgo"],
        default="default",
        help="hostlibs optimization flavor for hostlibs cache keys",
    )
    parser.add_argument(
        "--clang",
        default=None,
        help="clang executable path (defaults to system clang on linux)",
    )
    parser.add_argument(
        "--gcc",
        default=None,
        help="gcc executable path (defaults to system gcc on linux)",
    )
    parser.add_argument(
        "--print-inputs",
        action="store_true",
        help="print cache key inputs instead of the hashed key",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    deps_file = Path(__file__).resolve().parent.parent / "DEPS"
    try:
        if args.kind == "hostlibs":
            if not args.runtime_version:
                raise CacheKeyError("--runtime-version is required for hostlibs")
            key, inputs = hostlibs_cache_key(
                deps_file=deps_file,
                os_name=args.os,
                arch=args.arch,
                runtime_version=args.runtime_version,
                hostlibs_flavor=args.hostlibs_flavor,
                clang_path=args.clang,
            )
            print("\n".join(inputs) if args.print_inputs else key)
            return 0

        if args.kind == "skiasharp":
            if not args.skiasharp_version:
                raise CacheKeyError("--skiasharp-version is required for skiasharp")
            key, inputs = skiasharp_cache_key(
                deps_file=deps_file,
                os_name=args.os,
                arch=args.arch,
                skiasharp_version=args.skiasharp_version,
                clang_path=args.clang,
                gcc_path=args.gcc,
            )
            print("\n".join(inputs) if args.print_inputs else key)
            return 0
    except CacheKeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    exit(main())
