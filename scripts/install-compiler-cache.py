"""Install the pinned, portable sccache binary used by native builds."""

import hashlib
import os
import platform
import tarfile
import urllib.request
from pathlib import Path

VERSION = "0.18.0"
PACKAGES = {
    ("Windows", "AMD64"): ("x86_64-pc-windows-msvc", "1a63c1be2beab3f04d27e4cc145443e092e02d3dd83a51030989829d7023091b"),
    ("Linux", "x86_64"): ("x86_64-unknown-linux-musl", "45f1447fbe231e3037bde351ef70677dd212216c8d62ae7ca409fecc4d6acc89"),
    ("Darwin", "arm64"): ("aarch64-apple-darwin", "308184519b646f5125289e8515b36f6ca65a13a041923994aebe702348674e8e"),
    ("Darwin", "x86_64"): ("x86_64-apple-darwin", "1dade83cc49eeb42337565eccd534b05982820a8e44b851bd7937467a18c7aef"),
}

if __name__ == "__main__":
    target, checksum = PACKAGES[platform.system(), platform.machine()]
    directory = Path("build/tools/sccache").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    package = f"sccache-v{VERSION}-{target}"
    archive = directory / f"{package}.tar.gz"
    if not archive.exists():
        urllib.request.urlretrieve(f"https://github.com/mozilla/sccache/releases/download/v{VERSION}/{package}.tar.gz", archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        raise SystemExit(f"Invalid SHA-256: {archive}")
    binary = "sccache.exe" if platform.system() == "Windows" else "sccache"
    with tarfile.open(archive) as tar:
        member = tar.getmember(f"{package}/{binary}")
        (directory / binary).write_bytes(tar.extractfile(member).read())
    (directory / binary).chmod(0o755)
    print(directory / binary)
    if "GITHUB_ENV" in os.environ:
        with open(os.environ["GITHUB_ENV"], "a") as output:
            output.write(f"HOSTFORGE_COMPILER_CACHE={directory / binary}\n")
        with open(os.environ["GITHUB_PATH"], "a") as output:
            output.write(f"{directory}\n")
