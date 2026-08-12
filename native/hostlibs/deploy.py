import os

from conan.errors import ConanException
from conan.tools.files import copy, mkdir

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
    flavor = "default" if package.options.pgo else "no-pgo"
    os_name = str(package.settings.os)
    arch = _ARCH[str(package.settings.arch)]
    rid = f"{'win' if os_name == 'Windows' else 'linux'}-{arch}"
    target = os.path.join(output_folder, version, flavor, rid)

    mkdir(graph.root.conanfile, target)
    copy(
        graph.root.conanfile,
        "*",
        src=package.package_folder,
        dst=target,
        excludes=("conaninfo.txt", "conanmanifest.txt"),
    )
