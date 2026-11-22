"""Pinned versions appear only in their canonical files; run with python -m unittest discover -s tests/native.

Upgrading an upstream version must only touch the canonical files read by scripts/repo_versions.py. Everything
else (Taskfile defaults, recipes, samples, tests, CI) reads or derives the version instead of repeating it.
"""

import fnmatch
import importlib.util
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("repo_versions", ROOT / "scripts/repo_versions.py")
repo_versions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repo_versions)
spec = importlib.util.spec_from_file_location("package_inputs", ROOT / "scripts/package_inputs.py")
package_inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_inputs)

# Files that may contain pinned versions: documentation, the canonical files, the lock generated from them, the
# record of reviewed SkiaSharp build files (keyed like native/skiasharp/versions.json) and the Python tests whose
# fixtures use realistic versions. Every other file is scanned.
EXCLUDED = (
    "*.md",
    "*.txt",
    "tests/native/*",
    package_inputs.LOCK,
    "Directory.Build.props",
    "native/hostlibs/versions.json",
    "native/skiasharp/versions.json",
    "native/skiasharp/upstream-build.json",
    "native/avalonianative/conandata.yml",
    "native/angle/conandata.yml",
)

# Lines that are canonical inside an otherwise scanned file.
CANONICAL_LINES = {
    "native/angle/conanfile.py": re.compile(r'^    version = "[^"]+"$'),
}


def pinned_versions(versions, packages):
    """Every upstream version the canonical files pin, and the package versions derived from them."""
    return sorted({
        versions["avalonia"], versions["avalonia-native"], versions["angle"],
        *versions["skiasharp-versions"], *versions["harfbuzz"].values(),
        *versions["runtimes"].values(), *versions["hostlibs-versions"],
        *(record["version"] for family in packages.values() for record in family.values()),
    })


def pattern(versions):
    # A version, also as a v-prefixed tag, that is not part of a longer number or version (11.2.3, 1.2.30 and
    # 1.2.3.4 do not contain 1.2.3). "_", "-" and "." may join it to a name, as in skia_1.2.3.zip and
    # Name.win-x64.1.2.3.nupkg; literals() drops the matches that follow a numeric part instead (0.1.2.3).
    literals = "|".join(re.escape(version) for version in sorted(versions, key=len, reverse=True))
    return re.compile(rf"(?<![0-9A-Za-z])v?(?:{literals})(?![0-9A-Za-z]|\.\d)")


# The text before a match that makes it the end of a longer version: a part that is a number and a dot, such as
# "0.", "v1." or the "1." of "Name.1.", but not the "x64." of a runtime identifier. A lookbehind of fixed width
# cannot tell them apart.
_LONGER_VERSION = re.compile(r"(?:^|[^0-9A-Za-z])v?\d+\.$")


def literals(regex, line):
    """The matches of `regex` in `line` that are whole versions."""
    return [match.group() for match in regex.finditer(line) if not _LONGER_VERSION.search(line, 0, match.start())]


def scanned_files(root):
    listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=root, check=True, capture_output=True).stdout.decode("utf-8")
    for path in sorted(set(filter(None, listed.split("\0")))):
        if not any(fnmatch.fnmatchcase(path, excluded) for excluded in EXCLUDED) and (root / path).is_file():
            yield path


def find_literals(root, regex):
    found = []
    for path in scanned_files(root):
        try:
            text = (root / path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        allowed = CANONICAL_LINES.get(path)
        for number, line in enumerate(text.splitlines(), 1):
            if allowed is not None and allowed.match(line):
                continue
            found += [f"{path}:{number}: {literal}" for literal in literals(regex, line)]
    return found


class VersionLiteralTests(unittest.TestCase):
    def test_pinned_versions_are_not_repeated(self):
        found = find_literals(ROOT, pattern(pinned_versions(repo_versions.load(), package_inputs.compute())))
        self.assertEqual([], found, "Read pinned versions from scripts/repo_versions.py or Directory.Build.props "
                                    "instead of repeating them:\n" + "\n".join(found))

    def test_pattern_respects_number_boundaries(self):
        regex = pattern(["1.2.3", "4.5.6.7"])
        for text in ("1.2.3", '"1.2.3"', "/1.2.3/", "1.2.3-rc.1", "v=4.5.6.7;", "tag v1.2.3", "/tree/v1.2.3/",
                     "skia_1.2.3.zip", "1.2.3_x64", "Pkg.1.2.3.nupkg", "Pkg.win-x64.1.2.3.nupkg",
                     "Pkg.linux-musl-arm64.4.5.6.7.nupkg", "pkg/1.2.3/pkg.1.2.3.nupkg", "a.b.1.2.3"):
            with self.subTest(text=text):
                self.assertTrue(literals(regex, text))
        for text in ("11.2.3", "1.2.30", "1.2.3.4", "14.5.6.7", "4.5.6.70", "x1.2.3", "xv1.2.3", "1.2.3a",
                     "0.1.2.3", "9.1.2.3", "v1.1.2.3", "Pkg.1.1.2.3", "Pkg.x64.9.4.5.6.7"):
            with self.subTest(text=text):
                self.assertEqual([], literals(regex, text))


if __name__ == "__main__":
    unittest.main()
