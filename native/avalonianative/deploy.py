import os
import re

from conan.errors import ConanException
from conan.tools.files import copy, mkdir, rmdir


def deploy(graph, output_folder, **kwargs):
    packages = [dep for _, dep in graph.root.conanfile.dependencies.items() if dep.ref.name == "avalonianative"]
    if len(packages) != 1:
        raise ConanException("Expected exactly one avalonianative dependency")
    package = packages[0]
    arch = {"x86_64": "x64", "armv8": "arm64"}[str(package.settings.arch)]
    parts = (str(package.ref.version), f"osx-{arch}")
    if not all(re.fullmatch(r"[A-Za-z0-9][\w.+-]*", part) for part in parts):
        raise ConanException(f"Unexpected AvaloniaNative deploy directory: {parts}")
    destination = os.path.join(output_folder, *parts)
    source = os.path.join(package.package_folder, "lib")
    names = ("libAvaloniaNative.a", "manifest.json")
    for name in names:
        if not os.path.isfile(os.path.join(source, name)):
            raise ConanException(f"Missing AvaloniaNative payload: {name}")
    # Replace the directory, so that it holds only the files of this package.
    rmdir(graph.root.conanfile, destination)
    mkdir(graph.root.conanfile, destination)
    for name in names:
        copy(graph.root.conanfile, name, src=source, dst=destination)
    copy(graph.root.conanfile, "*", src=os.path.join(package.package_folder, "licenses"), dst=os.path.join(destination, "licenses"))
