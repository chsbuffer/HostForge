"""Fingerprint only inputs consumed by one native component/version/RID."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path


def rootfs_key(root, rid):
    files = [root / f"native/skiasharp/{rid}.Dockerfile"]
    if rid.startswith("linux-musl-"):
        arch = "x86_64" if rid.endswith("-x64") else "aarch64"
        files += [root / f"native/skiasharp/fontconfig-alpine3.17-{arch}.sha256"]
    digest = hashlib.sha256("\n".join(p.read_text(encoding="utf-8") for p in files).encode()).hexdigest()[:24]
    return f"target-rootfs-v2-{rid}-{digest}"


def fingerprint(root, component, version, rid, toolchain, generator=""):
    platform, arch = rid.rsplit("-", 1)
    profile_os = {"win": "windows", "osx": "macos"}.get(platform, platform)
    profile = f"{profile_os}-{arch}"
    directory = f"native/{component}"
    files = [f"{directory}/conanfile.py", f"{directory}/deploy.py"]
    values = {"version": version, "rid": rid, "toolchain": toolchain}
    tasks = []
    if component == "skiasharp":
        values["source"] = json.loads((root / directory / "versions.json").read_text())[version]
        files += [f"{directory}/git-sync-deps", f"{directory}/{profile_os.split('-')[0]}.py"]
        tasks = [f"_skiasharp-{profile_os.split('-')[0]}"]
        if platform == "win":
            files += [f"{directory}/profiles/windows-x64", "scripts/libHarfBuzzSharp.vcxproj.in"]
        if platform.startswith("linux"):
            files += [f"{directory}/profiles/linux-x64", f"{directory}/{rid}.Dockerfile"]
            if platform == "linux-musl":
                apk_arch = "x86_64" if arch == "x64" else "aarch64"
                files += [f"{directory}/fontconfig-alpine3.17-{apk_arch}.sha256"]
    elif component == "angle":
        files += [f"{directory}/conandata.yml", *[p.relative_to(root).as_posix() for p in (root / directory / "patches").glob("*")]]
        tasks = ["build-angle"]
    elif component == "hostlibs":
        tasks = [f"_hostlibs-{profile_os}"]
        if platform == "linux":
            files += ["native/skiasharp/linux-x64.Dockerfile"]
        values["pgo"] = True
        values["generator"] = generator
    elif component == "avalonianative":
        profile = rid
        files += [f"{directory}/conandata.yml"]
        tasks = ["build-avalonianative"]
    else:
        raise ValueError(component)
    files += [f"{directory}/profiles/{profile}"]
    # Changes to unrelated tasks or Python project metadata do not affect a library.
    taskfile = (root / "Taskfile.yml").read_text(encoding="utf-8")
    for task in tasks:
        match = re.search(rf"^  {re.escape(task)}:\n.*?(?=^  [\w-]+:|\Z)", taskfile, re.M | re.S)
        if match is None:
            raise ValueError(f"Missing build task: {task}")
        values[task] = match.group()
    dependencies = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    values["build-tools"] = sorted(d for d in dependencies if d.startswith(("conan==", "ninja==")))
    inputs = {path: (root / path).read_text(encoding="utf-8").replace("\r\n", "\n") for path in sorted(set(files))}
    digest = hashlib.sha256(json.dumps([values, inputs], sort_keys=True).encode()).hexdigest()[:24]
    tools = hashlib.sha256(toolchain.encode()).hexdigest()[:12]
    return {"key": f"native-v3-{component}-{version}-{rid}-{tools}-{digest}",
            "compiler-prefix": f"objects-v1-{component}-{version}-{rid}-{tools}-",
            "digest": digest, "inputs": sorted(inputs), "toolchain": toolchain,
            "rootfs-key": rootfs_key(root, rid) if rid.startswith("linux") else ""}


def detect_toolchain(component, rid):
    if rid.startswith("win-"):
        installer = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
        vs = subprocess.check_output([str(installer), "-latest", "-prerelease", "-products", "*",
                                      "-version", "[18,19)", "-property", "installationPath"], text=True).strip()
        if not vs:
            raise RuntimeError("VS 2026 is required by the Windows native profiles")
        msvc = (Path(vs) / "VC/Auxiliary/Build/Microsoft.VCToolsVersion.default.txt").read_text().strip()
        llvm = ""
        if component == "skiasharp":
            action = Path(__file__).resolve().parent.parent / ".github/actions/install-llvm/action.yml"
            version = re.search(r"\$Version = '([^']+)'", action.read_text(encoding="utf-8")).group(1)
            llvm = f"llvm-{version}-"
            sdk = "10.0.26100.0"  # Explicitly selected by the Skia profiles.
        else:
            kits = Path(os.environ["ProgramFiles(x86)"]) / "Windows Kits/10/Include"
            sdk = max((p.name for p in kits.iterdir() if re.fullmatch(r"10\.0\.\d+\.\d+", p.name)),
                      key=lambda value: tuple(map(int, value.split("."))))
        return f"{llvm}msvc-{msvc}-sdk-{sdk}"
    if rid.startswith("osx-"):
        return subprocess.check_output(["xcodebuild", "-version"], text=True).strip() + " / " + subprocess.check_output(["xcrun", "--show-sdk-version"], text=True).strip()
    if component == "skiasharp":
        return "llvm-20.1.8-cross"  # The per-RID Dockerfile pins the complete image digest.
    return subprocess.check_output(["clang", "--version"], text=True).splitlines()[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=["skiasharp", "angle", "hostlibs", "avalonianative"])
    parser.add_argument("version")
    parser.add_argument("rid")
    parser.add_argument("--toolchain")
    args = parser.parse_args()
    generator = (subprocess.check_output(["cmake", "--version"], text=True).splitlines()[0]
                 if args.component == "hostlibs" else "")
    result = fingerprint(Path(__file__).resolve().parent.parent, args.component, args.version, args.rid,
                         args.toolchain or detect_toolchain(args.component, args.rid), generator)
    print(json.dumps(result, indent=2))
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for key in ("key", "compiler-prefix", "digest", "rootfs-key"):
                output.write(f"{key}={result[key]}\n")
