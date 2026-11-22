"""Read the upstream versions pinned by the repository's canonical files.

Taskfile defaults, CI matrices and tests read these values instead of repeating them:

- Directory.Build.props: Avalonia, AvaloniaNative, ANGLE, the default SkiaSharp line, the HarfBuzzSharp
  version of each SkiaSharp line, the supported target frameworks and the .NET runtime of each one.
- native/hostlibs/versions.json: VMR tag and commit of each .NET runtime.
- native/skiasharp/versions.json: mono/skia commit, HarfBuzzSharp version and mono/SkiaSharp tag commit of each
  SkiaSharp version.
- native/avalonianative/conandata.yml: Avalonia source archive of each version.
- native/angle/conandata.yml: ANGLE source branch and commit.

Usage: python scripts/repo_versions.py [--get KEY] [--github-output]
"""

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _properties(root):
    """Return {name: [(condition, text), ...]} for every property element of Directory.Build.props."""
    properties = {}
    for group in ET.parse(root / "Directory.Build.props").getroot().iter("PropertyGroup"):
        for element in group:
            properties.setdefault(element.tag, []).append(
                (element.get("Condition", "").replace(" ", ""), (element.text or "").strip()))
    return properties


def _literal(properties, name):
    """Value of a property set once, either unconditionally or as the default for an empty value."""
    values = [text for condition, text in properties.get(name, []) if condition in ("", f"'$({name})'==''")]
    if len(values) != 1 or "$(" in values[0]:
        raise ValueError(f"Directory.Build.props: expected one literal {name}, found {values}")
    return values[0]


def _harfbuzz(properties):
    """HarfBuzzSharp version of each SkiaSharp version, from the conditions of HarfBuzzVersion."""
    versions = {}
    for condition, text in properties.get("HarfBuzzVersion", []):
        match = re.search(r"'\$\(SkiaSharpVersion\)'=='([^']+)'", condition)
        if match:
            versions[match.group(1)] = text
    return versions


def _yaml_fields(block, indent):
    return dict(re.findall(rf'^ {{{indent}}}([\w-]+): *"?([^"\n]*)"?$', block, re.M))


def _avalonia_sources(text):
    sources = {}
    for match in re.finditer(r'^  "([^"]+)":\n((?:    .*\n?)+)', text, re.M):
        sources[match.group(1)] = _yaml_fields(match.group(2), 4)
    return sources


def _angle_source(text):
    match = re.search(r"^sources:\n((?:  .*\n?)+)", text, re.M)
    if match is None:
        raise ValueError("native/angle/conandata.yml: missing sources")
    return _yaml_fields(match.group(1), 2)


def load(root=ROOT):
    """Return the pinned versions; keys are stable and hyphenated so they map to CI outputs."""
    root = Path(root)
    properties = _properties(root)
    frameworks = _literal(properties, "HostForgeTargetFrameworks").split(";")
    runtimes = {}
    for framework in frameworks:
        match = re.fullmatch(r"net(\d+)\.0", framework)
        if match is None:
            raise ValueError(f"Unsupported target framework: {framework}")
        runtimes[framework] = _literal(properties, f"HostLibsNet{match.group(1)}Version")
    skiasharp = json.loads((root / "native/skiasharp/versions.json").read_text(encoding="utf-8"))
    hostlibs = json.loads((root / "native/hostlibs/versions.json").read_text(encoding="utf-8"))
    angle = _angle_source((root / "native/angle/conandata.yml").read_text(encoding="utf-8"))
    return {
        "avalonia": _literal(properties, "AvaloniaVersion"),
        "avalonia-native": _literal(properties, "AvaloniaNativeVersion"),
        "avalonia-sources": _avalonia_sources((root / "native/avalonianative/conandata.yml").read_text(encoding="utf-8")),
        "angle": _literal(properties, "AngleVersion"),
        "angle-branch": angle.get("branch", ""),
        "angle-commit": angle.get("commit", ""),
        "skiasharp": _literal(properties, "SkiaSharpVersion"),
        "skiasharp-versions": list(skiasharp),
        "skiasharp-sources": {version: {"skia-commit": commit, "harfbuzz": harfbuzz, "skiasharp-commit": tag_commit}
                              for version, (commit, harfbuzz, tag_commit) in skiasharp.items()},
        "harfbuzz": _harfbuzz(properties),
        "target-frameworks": frameworks,
        "runtimes": runtimes,
        "hostlibs": runtimes[_literal(properties, "HostLibsTargetFramework")],
        "hostlibs-versions": list(hostlibs),
        "hostlibs-sources": hostlibs,
    }


def problems(versions):
    """Inconsistencies between the canonical files; empty when they agree."""
    found = []
    if sorted(versions["runtimes"].values()) != sorted(versions["hostlibs-versions"]):
        found.append(f"HostLibsNet*Version {versions['runtimes']} != native/hostlibs/versions.json "
                     f"{versions['hostlibs-versions']}")
    expected = {version: source["harfbuzz"] for version, source in versions["skiasharp-sources"].items()}
    if versions["harfbuzz"] != expected:
        found.append(f"HarfBuzzVersion conditions {versions['harfbuzz']} != native/skiasharp/versions.json {expected}")
    if versions["skiasharp"] not in versions["skiasharp-versions"]:
        found.append(f"SkiaSharpVersion {versions['skiasharp']} is not in native/skiasharp/versions.json")
    if versions["avalonia-native"] != versions["avalonia"]:
        # Consumers restore Avalonia.Native at AvaloniaVersion; a static payload of another version fails HFG0004.
        found.append(f"AvaloniaNativeVersion {versions['avalonia-native']} != AvaloniaVersion {versions['avalonia']}")
    if versions["avalonia-native"] not in versions["avalonia-sources"]:
        found.append(f"AvaloniaNativeVersion {versions['avalonia-native']} has no source in "
                     "native/avalonianative/conandata.yml")
    return found


def _format(value):
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--get", metavar="KEY", help="print one value: strings as-is, other values as JSON")
    parser.add_argument("--github-output", action="store_true", help="also write every value to $GITHUB_OUTPUT")
    args = parser.parse_args()
    pinned = load()
    print(_format(pinned[args.get]) if args.get else json.dumps(pinned, indent=2))
    if args.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in pinned.items():
                output.write(f"{key}={_format(value)}\n")
