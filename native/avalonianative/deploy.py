import os

from conan.errors import ConanException
from conan.tools.files import copy


def deploy(graph, output_folder, **kwargs):
    packages = [dep for _, dep in graph.root.conanfile.dependencies.items() if dep.ref.name == "avalonianative"]
    if len(packages) != 1:
        raise ConanException("Expected exactly one avalonianative dependency")
    package = packages[0]
    arch = {"x86_64": "x64", "armv8": "arm64"}[str(package.settings.arch)]
    destination = os.path.join(output_folder, str(package.ref.version), f"osx-{arch}")
    for name in ("libAvaloniaNative.a", "manifest.json"):
        source = os.path.join(package.package_folder, "lib")
        if not os.path.isfile(os.path.join(source, name)):
            raise ConanException(f"Missing AvaloniaNative payload: {name}")
        copy(graph.root.conanfile, name, src=source, dst=destination)
    copy(graph.root.conanfile, "*", src=os.path.join(package.package_folder, "licenses"), dst=os.path.join(destination, "licenses"))
