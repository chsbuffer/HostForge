import os

from conan.errors import ConanException
from conan.tools.files import copy, mkdir, rmdir

_ARCH = {"x86_64": "x64", "armv8": "arm64"}


def deploy(graph, output_folder: str, **kwargs):
    packages = [
        dependency
        for _, dependency in graph.root.conanfile.dependencies.items()
        if dependency.ref.name == "angle"
    ]
    if len(packages) != 1:
        raise ConanException("Expected exactly one angle dependency")

    package = packages[0]
    version = str(package.ref.version)
    os_name = str(package.settings.os)
    arch = _ARCH[str(package.settings.arch)]
    rid = f"{'win' if os_name == 'Windows' else 'linux'}-{arch}"
    target = os.path.join(output_folder, version, rid)

    mkdir(graph.root.conanfile, target)
    copy(
        graph.root.conanfile,
        "*.lib",
        src=os.path.join(package.package_folder, "lib"),
        dst=target,
        keep_path=False,
    )
    copy(
        graph.root.conanfile,
        "*.def",
        src=os.path.join(package.package_folder, "res"),
        dst=target,
        keep_path=False,
    )
    licenses = os.path.join(package.package_folder, "res", "licenses")
    if not os.path.isfile(os.path.join(licenses, "angle", "LICENSE")):
        raise ConanException(f"Missing ANGLE licenses: {licenses}")
    rmdir(graph.root.conanfile, os.path.join(target, "licenses"))
    copy(
        graph.root.conanfile,
        "*",
        src=licenses,
        dst=os.path.join(target, "licenses"),
    )
