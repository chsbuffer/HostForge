import os
import re

from conan.errors import ConanException
from conan.tools.files import copy, mkdir, rmdir

_ARCH = {"x86_64": "x64", "armv8": "arm64"}


def deploy(graph, output_folder: str, **kwargs):
    packages = [
        dependency
        for _, dependency in graph.root.conanfile.dependencies.items()
        if dependency.ref.name == "hostlibs"
    ]
    if len(packages) != 1:
        raise ConanException("Expected exactly one hostlibs dependency")

    package = packages[0]
    version = str(package.ref.version)
    # Only Windows packages have the pgo option; Linux packages are always the PGO build.
    flavor = "default" if package.options.get_safe("pgo", True) else "no-pgo"
    os_name = str(package.settings.os)
    arch = _ARCH[str(package.settings.arch)]
    rid = f"{'win' if os_name == 'Windows' else 'linux'}-{arch}"
    if os_name == "Linux" and package.options.libc == "musl":
        rid = f"linux-musl-{arch}"
    parts = (version, flavor, rid)
    if not all(re.fullmatch(r"[A-Za-z0-9][\w.+-]*", part) for part in parts):
        raise ConanException(f"Unexpected HostLibs deploy directory: {parts}")
    target = os.path.join(output_folder, *parts)

    # Replace the directory: objects of a rebuilt package can be named after another
    # (random) Conan build folder, and AppHost.Static packs every file in it.
    rmdir(graph.root.conanfile, target)
    mkdir(graph.root.conanfile, target)
    copy(
        graph.root.conanfile,
        "*",
        src=package.package_folder,
        dst=target,
        excludes=("conaninfo.txt", "conanmanifest.txt"),
    )
