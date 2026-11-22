import os
import re

from conan.errors import ConanException
from conan.tools.files import copy, mkdir, rmdir

_ARCH = {"x86_64": "x64", "armv8": "arm64"}


def deploy(graph, output_folder: str, **kwargs):
    packages = [
        dependency
        for _, dependency in graph.root.conanfile.dependencies.items()
        if dependency.ref.name == "skiasharp"
    ]
    if len(packages) != 1:
        raise ConanException("Expected exactly one skiasharp dependency")

    package = packages[0]
    version = str(package.ref.version)
    os_name = str(package.settings.os)
    arch = _ARCH[str(package.settings.arch)]
    rid = f"{ {'Windows': 'win', 'Linux': 'linux', 'Macos': 'osx'}[os_name]}-{arch}"
    if os_name == "Linux" and package.options.libc == "musl":
        rid = f"linux-musl-{arch}"
    if not all(re.fullmatch(r"[A-Za-z0-9][\w.+-]*", part) for part in (version, rid)):
        raise ConanException(f"Unexpected SkiaSharp deploy directory: {(version, rid)}")
    target = os.path.join(output_folder, version, rid)

    # SkiaSharp.Static packs every file of the directory, and manifest.json must hash each of them, so a file
    # left by an earlier payload layout must not survive a redeploy.
    rmdir(graph.root.conanfile, target)
    mkdir(graph.root.conanfile, target)
    copy(
        graph.root.conanfile,
        "*",
        src=os.path.join(package.package_folder, "lib"),
        dst=target,
        keep_path=True,
    )
