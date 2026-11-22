"""The package documentation names the current native package versions; run with python -m unittest discover -s
tests/native.

The README files are packed into the packages. A fix revision is bumped by hand in Directory.Build.props, not by
the upstream workflow that updates the documentation, and NuGet resolves a version that does not exist (an
upstream version such as 1.2.3) to the lowest version above it, the oldest revision. So the documentation that
gives a package version of a native package family gives the current version of each of its lines, and no other
revision of them.
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _module(name):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


package_inputs = _module("package_inputs")
upstream = _module("upstream")

# The native package families: upstream version, three parts, and a fix revision (DEVELOPMENT.md).
NATIVE = ("ChsBuffer.SkiaSharp.Static", "ChsBuffer.Angle.Static", "ChsBuffer.Avalonia.Native.Static")
FOUR_PARTS = re.compile(r"(?<![\w.])\d+\.\d+\.\d+\.\d+(?!\w|\.\d)")


def upstream_versions(pinned):
    """Pinned upstream versions, which the documentation may name; the ANGLE version has four parts too."""
    return {pinned["angle"], *pinned["harfbuzz"].values()}


def problems(texts, records, pins):
    """Problems of the documents {path: text}: a four-part version of a native package line that is not its
    current version, and a document that gives a version of a family but not the current version of each line."""
    lines = {record["version"].rsplit(".", 1)[0]: (family, record["version"])
             for family in NATIVE for record in records[family].values()}
    found = []
    for path, text in texts.items():
        families = set()
        for version in dict.fromkeys(FOUR_PARTS.findall(text)):
            family, current = lines.get(version.rsplit(".", 1)[0], (None, None))
            if family is None or version in pins:
                continue
            families.add(family)
            if version != current:
                found.append(f"{path}: {version} is not the current {family} version {current}")
        for family in sorted(families):
            found += [f"{path}: gives {family} versions but not {record['version']}"
                      for record in records[family].values()
                      if not upstream._mention_pattern([record["version"]]).search(text)]
    return found


class PackageDocumentationTests(unittest.TestCase):
    def test_documentation_gives_the_current_native_package_versions(self):
        texts = {path: (ROOT / path).read_text(encoding="utf-8") for path in upstream.DOC_FILES}
        found = problems(texts, package_inputs.compute(ROOT), upstream_versions(package_inputs.repo_versions.load(ROOT)))
        self.assertEqual([], found, "Give the package versions of Directory.Build.props in the package documentation "
                                    f"({', '.join(upstream.DOC_FILES)}):\n" + "\n".join(found))

    def test_problems(self):
        records = {"ChsBuffer.SkiaSharp.Static": {"skia3": {"version": "3.1.2.1"}, "skia4": {"version": "4.5.6.2"}},
                   "ChsBuffer.Angle.Static": {"default": {"version": "2.1.7.1"}},
                   "ChsBuffer.Avalonia.Native.Static": {"default": {"version": "12.1.6.1"}}}
        upstream_pins = {"2.1.7.20260419", "8.3.1.5"}
        current = 'Version="3.1.2.1" ... use 4.5.6.2 for SkiaSharp 4, ANGLE 2.1.7.20260419, HarfBuzz 8.3.1.5'
        self.assertEqual([], problems({"a.md": current, "b.md": "SkiaSharp 3.1.2 and 4.5.6", "c.md": ""},
                                      records, upstream_pins))
        self.assertEqual(["a.md: gives ChsBuffer.SkiaSharp.Static versions but not 4.5.6.2"],
                         problems({"a.md": 'Version="3.1.2.1"; use version 4.5.6 for SkiaSharp 4'}, records,
                                  upstream_pins))
        self.assertEqual(["a.md: 4.5.6.1 is not the current ChsBuffer.SkiaSharp.Static version 4.5.6.2",
                          "a.md: 2.1.7.2 is not the current ChsBuffer.Angle.Static version 2.1.7.1",
                          "a.md: gives ChsBuffer.Angle.Static versions but not 2.1.7.1",
                          "a.md: gives ChsBuffer.SkiaSharp.Static versions but not 4.5.6.2"],
                         problems({"a.md": "3.1.2.1 4.5.6.1 2.1.7.2"}, records, upstream_pins))


if __name__ == "__main__":
    unittest.main()
