"""Inputs of every published package version, and the lock that keeps them tied to their versions.

nuget.org keeps the first upload of a version and CI pushes with --skip-duplicate, so a package whose
inputs change while its version stays the same is silently not published. The lock
(package-inputs.lock.json) is an append-only history: for each package family, one entry per version with
the variant it is built for and its declared inputs. compute() returns one record per variant:

- ChsBuffer.Avalonia.AppHost (RID, Build and meta packages), one record per SkiaSharp line:
  the runtime and VMR source of each target framework, the SkiaSharp line with its skia commit and
  HarfBuzzSharp version, and ANGLE (linked into the Windows templates).
- ChsBuffer.AppHost.Static, one record per target framework: runtime and VMR source.
- ChsBuffer.SkiaSharp.Static, one record per SkiaSharp line: skia commit and HarfBuzzSharp version.
- ChsBuffer.Angle.Static: AngleVersion, branch and commit.
- ChsBuffer.Avalonia.Native.Static: Avalonia source archive (url, sha256, commit).

Versions are evaluated from Directory.Build.props and AppHostStatic.csproj exactly as MSBuild does for
the default (empty) _Preview, so the lock follows the real version formulas, revisions included.

Rules, enforced by `python scripts/upstream.py lock [--check]` and tests/native/test_package_inputs.py:
the variants of a family compute distinct versions; a version already in the history keeps its variant and
inputs; every computed version is in the history (`lock` appends it); versions of a variant never move
below its highest version in the history; versions are written in the form nuget.org identifies them by, so
that the version strings of the lock are the versions on nuget.org. A committed entry never changes: `lock` and `lock --check [--base
<ref>]` require every version to keep the entry of the first commit on the first-parent history of HEAD (or
<ref>) whose lock holds it. Every commit on main counts as published: any push may publish, and a push whose
check failed still becomes the parent of the next one.
"""

import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = "package-inputs.lock.json"
PROPS = "Directory.Build.props"
APPHOST_STATIC = "src/package-apphost-static/AppHostStatic.csproj"

# Files read by compute(); apply stages copies of them.
SOURCES = (PROPS, APPHOST_STATIC, "native/hostlibs/versions.json", "native/skiasharp/versions.json",
           "native/avalonianative/conandata.yml", "native/angle/conandata.yml")


def _load_repo_versions():
    module = sys.modules.get("repo_versions")
    if module is None:
        spec = importlib.util.spec_from_file_location("repo_versions", Path(__file__).with_name("repo_versions.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules["repo_versions"] = module
    return module


repo_versions = _load_repo_versions()


# --- A small MSBuild property evaluator, limited to the constructs the version properties use. ---

_PROPERTY = re.compile(r"\$\((\w+)\)")
_VERSION_CALL = re.compile(r"\$\(\[System\.Version\]::Parse\('([^']*)'\)\.ToString\((\d)\)\)")
_STARTS_WITH = re.compile(r"\$\((\w+)\.StartsWith\('([^']*)'\)\)")
_COMPARE = re.compile(r"'([^']*)'\s*(==|!=)\s*'([^']*)'")


class Unsupported(ValueError):
    pass


class Unevaluated:
    """Value of a property the evaluator cannot follow; `reason` says why."""

    def __init__(self, reason):
        self.reason = reason

    def __repr__(self):
        return f"Unevaluated({self.reason!r})"


def _expand(text, values):
    def version(match):
        parts = _expand(match.group(1), values).split(".")
        count = int(match.group(2))
        if len(parts) < count:
            raise Unsupported(f"System.Version.ToString({count}) of {'.'.join(parts)!r}")
        return ".".join(parts[:count])

    text = _VERSION_CALL.sub(version, text)
    if "$([" in text or re.search(r"\$\(\w+\.", text) or "@(" in text or "%(" in text:
        raise Unsupported(text)

    def property_value(match):
        value = values.get(match.group(1), "")
        if isinstance(value, Unevaluated):
            raise Unsupported(f"$({match.group(1)}): {value.reason}")
        return value

    return _PROPERTY.sub(property_value, text)


def _condition(condition, values):
    condition = " ".join(condition.split())
    if not condition:
        return True
    if re.search(r"\sor\s", condition, re.I):
        raise Unsupported(condition)
    result = True
    for clause in re.split(r"\s+and\s+", condition, flags=re.I):
        match = _STARTS_WITH.fullmatch(clause)
        if match:
            value = values.get(match.group(1)) or ""
            if isinstance(value, Unevaluated):
                raise Unsupported(f"$({match.group(1)}): {value.reason}")
            result = result and value.startswith(match.group(2))
            continue
        match = _COMPARE.fullmatch(clause)
        if match is None:
            raise Unsupported(condition)
        equal = _expand(match.group(1), values).lower() == _expand(match.group(3), values).lower()
        result = result and (equal if match.group(2) == "==" else not equal)
    return result


def evaluate(files, global_properties=None):
    """Evaluate the PropertyGroups of `files` in order.

    A property whose value or condition is outside the supported subset evaluates to Unevaluated, and so does
    every property of a PropertyGroup whose condition is unsupported; _required turns that into an error.
    """
    global_properties = dict(global_properties or {})
    values = dict(global_properties)
    for path in files:
        for group in ET.parse(path).getroot().iter("PropertyGroup"):
            group_condition = group.get("Condition", "")
            try:
                if not _condition(group_condition, values):
                    continue
            except Unsupported as error:
                for element in group:
                    if isinstance(element.tag, str) and element.tag not in global_properties:
                        values[element.tag] = Unevaluated(
                            f"{element.tag} is in a PropertyGroup with an unsupported condition "
                            f"{' '.join(group_condition.split())!r} ({error})")
                continue
            for element in group:
                if not isinstance(element.tag, str) or element.tag in global_properties:
                    continue
                try:
                    if _condition(element.get("Condition", ""), values):
                        values[element.tag] = _expand((element.text or "").strip(), values)
                except Unsupported as error:
                    values[element.tag] = Unevaluated(f"{element.tag}: unsupported construct {error}")
    return values


def _required(values, name, context):
    value = values.get(name)
    if isinstance(value, Unevaluated):
        raise ValueError(f"{context}: cannot evaluate {name}: {value.reason}; update scripts/package_inputs.py "
                         "to match its definition")
    if not value:
        raise ValueError(f"{context}: cannot evaluate {name}; update scripts/package_inputs.py to match its definition")
    return value


# --- Versions -------------------------------------------------------------------------------------

def version_key(version):
    """Sort key following NuGet SemVer 2.0 precedence (four numeric parts, prerelease before release)."""
    version = version.split("+", 1)[0]
    release, _, prerelease = version.partition("-")
    numbers = [int(part) for part in release.split(".")]
    numbers += [0] * (4 - len(numbers))
    labels = tuple((0, int(label), "") if label.isdigit() else (1, 0, label.lower())
                   for label in prerelease.split(".")) if prerelease else ()
    return (tuple(numbers), 0 if prerelease else 1, labels)


_NUGET_VERSION = re.compile(r"(\d+(?:\.\d+){0,3})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z.-]*)?")


def normalized_version(version):
    """The version string nuget.org identifies `version` by: no leading zeros, three or four numeric parts (no
    fourth part 0), no build metadata, and lower-case prerelease labels, which nuget.org compares ignoring case."""
    match = _NUGET_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"{version!r} is not a NuGet version")
    numbers = [str(int(part)) for part in match.group(1).split(".")]
    numbers += ["0"] * (3 - len(numbers))
    if numbers[3:] == ["0"]:
        numbers.pop()
    release = ".".join(numbers)
    if match.group(2) is None:
        return release
    labels = [str(int(label)) if label.isdigit() else label.lower() for label in match.group(2).split(".")]
    return f"{release}-{'.'.join(labels)}"


def is_stable(version):
    return "-" not in version


# --- Records --------------------------------------------------------------------------------------

def _skia_variant(version):
    return f"skia{version.split('.', 1)[0]}"


def compute(root=ROOT):
    """Return {family: {variant: {"version", "bump", "inputs"}}} for the repository at `root`."""
    root = Path(root)
    pinned = repo_versions.load(root)
    problems = repo_versions.problems(pinned)
    if problems:
        raise ValueError("canonical version files disagree:\n  " + "\n  ".join(problems))
    props = [root / PROPS]

    runtimes = {}
    for framework, runtime in pinned["runtimes"].items():
        source = pinned["hostlibs-sources"][runtime]
        runtimes[framework] = {"version": runtime, "vmr-tag": source["tag"], "vmr-commit": source["commit"]}
    angle = {"version": pinned["angle"], "branch": pinned["angle-branch"], "commit": pinned["angle-commit"]}

    apphost, skiasharp = {}, {}
    for line in pinned["skiasharp-versions"]:
        variant = _skia_variant(line)
        if variant in apphost:
            raise ValueError(f"native/skiasharp/versions.json: two SkiaSharp versions share {variant}")
        values = evaluate(props, {"SkiaSharpVersion": line})
        source = pinned["skiasharp-sources"][line]
        skia = {"version": line, "skia-commit": source["skia-commit"], "harfbuzz": source["harfbuzz"]}
        apphost[variant] = {
            "version": _required(values, "AvaloniaAppHostPackageVersion", PROPS),
            "bump": "increment <_Major> in Directory.Build.props",
            "inputs": {"runtimes": runtimes, "skiasharp": skia, "angle": angle},
        }
        skiasharp[variant] = {
            "version": _required(values, "SkiaSharpPackageVersion", PROPS),
            "bump": f"increment the revision of <SkiaSharpPackageVersion> for SkiaSharp {line.split('.', 1)[0]}.x "
                    "in Directory.Build.props",
            "inputs": skia,
        }

    apphost_static = {}
    for framework, runtime in runtimes.items():
        values = evaluate(props + [root / APPHOST_STATIC], {"HostLibsTargetFramework": framework})
        version = _required(values, "Version", APPHOST_STATIC)
        if not version.startswith(runtime["version"]):
            raise ValueError(f"{APPHOST_STATIC}: Version {version} does not follow HostLibsVersion {runtime['version']}")
        apphost_static[framework] = {
            "version": version,
            "bump": f"increment the prerelease suffix of <Version> in {APPHOST_STATIC}",
            "inputs": runtime,
        }

    values = evaluate(props)
    avalonia = pinned["avalonia-native"]
    avalonia_source = pinned["avalonia-sources"][avalonia]
    return {
        "ChsBuffer.Avalonia.AppHost": apphost,
        "ChsBuffer.AppHost.Static": apphost_static,
        "ChsBuffer.SkiaSharp.Static": skiasharp,
        "ChsBuffer.Angle.Static": {"default": {
            "version": _required(values, "AnglePackageVersion", PROPS),
            "bump": "increment the revision of <AnglePackageVersion> in Directory.Build.props",
            "inputs": angle,
        }},
        "ChsBuffer.Avalonia.Native.Static": {"default": {
            "version": _required(values, "AvaloniaNativePackageVersion", PROPS),
            "bump": "increment the revision of <AvaloniaNativePackageVersion> in Directory.Build.props",
            "inputs": {"avalonia": avalonia, "url": avalonia_source.get("url", ""),
                       "sha256": avalonia_source.get("sha256", ""), "commit": avalonia_source.get("commit", "")},
        }},
    }


# --- Lock document --------------------------------------------------------------------------------
#
# {"comment": [...], "packages": {family: {version: {"variant": ..., "inputs": {...}}}}}: an append-only history
# with one entry per package version, so it is also the source manifest of every published version.

COMMENT = [
    "Maintained by `python scripts/upstream.py lock`, which only appends; do not edit by hand.",
    "One entry per package version: the variant it is built for and the inputs it is built from. nuget.org keeps",
    "the first upload of a version and CI pushes with --skip-duplicate, so changed inputs need a new version, and a",
    "committed entry never changes (CI compares each entry with the first commit on main that holds it).",
]
FAMILIES = ("ChsBuffer.Avalonia.AppHost", "ChsBuffer.AppHost.Static", "ChsBuffer.SkiaSharp.Static",
            "ChsBuffer.Angle.Static", "ChsBuffer.Avalonia.Native.Static")


def entries(records):
    """{family: {version: {"variant", "inputs"}}} of computed records; see conflicts() for shared versions."""
    return {family: {record["version"]: {"variant": variant, "inputs": record["inputs"]}
                     for variant, record in variants.items()} for family, variants in records.items()}


def history(lock):
    """The {family: {version: entry}} history of a lock document ({} for no lock); ValueError when malformed."""
    if lock is None:
        return {}
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        raise ValueError(f'{LOCK}: expected an object with "packages"')
    for family, versions in packages.items():
        if not isinstance(versions, dict):
            raise ValueError(f"{LOCK}: {family}: expected an object of versions")
        for version, entry in versions.items():
            if not isinstance(entry, dict) or set(entry) != {"variant", "inputs"}:
                found = sorted(entry) if isinstance(entry, dict) else type(entry).__name__
                raise ValueError(f'{LOCK}: {family} {version}: expected {{"variant", "inputs"}}, found {found}')
    return packages


def _ordered(packages):
    families = [family for family in FAMILIES if family in packages] + sorted(set(packages) - set(FAMILIES))
    return {family: dict(sorted(packages[family].items(), key=lambda item: version_key(item[0])))
            for family in families}


def document(packages):
    return {"comment": COMMENT, "packages": _ordered(packages)}


def _render(value, indent):
    """JSON with one line per object of scalars, so each runtime or source reads and diffs as one line."""
    if isinstance(value, dict) and value and any(isinstance(item, (dict, list)) for item in value.values()):
        inner = " " * (indent + 2)
        items = [f"{inner}{json.dumps(key)}: {_render(item, indent + 2)}" for key, item in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value) and len(value) > 1:
        inner = " " * (indent + 2)
        return "[\n" + ",\n".join(inner + json.dumps(item) for item in value) + "\n" + " " * indent + "]"
    return json.dumps(value)


def render(packages):
    """Lock file text of the history `packages`."""
    return _render(document(packages), 0) + "\n"


def parse(text, origin=LOCK):
    try:
        lock = json.loads(text)
    except ValueError as error:
        raise ValueError(f"{origin}: invalid JSON ({error})") from None
    history(lock)
    return lock


def read_lock(root=ROOT):
    path = Path(root) / LOCK
    if not path.exists():
        return None
    return parse(path.read_text(encoding="utf-8"))


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        items = {}
        for key, item in value.items():
            items.update(_flatten(item, f"{prefix}/{key}" if prefix else key))
        return items
    return {prefix: value}


def _changes(old, new):
    old, new = _flatten(old), _flatten(new)
    return [(key, old.get(key), new.get(key)) for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]


# --- Rules ----------------------------------------------------------------------------------------
#
# 1. conflicts:   two variants of a family compute the same version.
# 2. violations:  a computed version is in the history with another variant or other inputs.
# 3. missing:     a computed version is not in the history yet (`lock` appends it).
# 4. regressions: a computed version is below the highest version of its family and variant in the history.
# 5. unnormalized: a computed version is not written as nuget.org identifies it, so the lock, keyed by the
#                  string, would take a version already on nuget.org for a new one.
# rewritten_entries() compares a lock with the history of committed locks: a committed entry is never removed or
# changed.

def conflicts(records):
    found = []
    for family, variants in records.items():
        shared = {}
        for variant, record in variants.items():
            shared.setdefault(record["version"], []).append(variant)
        found += [{"family": family, "version": version, "variants": names, "bump": variants[names[-1]]["bump"]}
                  for version, names in shared.items() if len(names) > 1]
    return found


def violations(lock, records):
    found = []
    locked = history(lock)
    for family, variants in records.items():
        for variant, record in variants.items():
            old = locked.get(family, {}).get(record["version"])
            new = {"variant": variant, "inputs": record["inputs"]}
            if old is not None and old != new:
                found.append({"family": family, "variant": variant, "version": record["version"],
                              "bump": record["bump"], "changes": _changes(old, new)})
    return found


def missing(lock, records):
    locked = history(lock)
    return [{"family": family, "variant": variant, "version": record["version"]}
            for family, variants in records.items() for variant, record in variants.items()
            if record["version"] not in locked.get(family, {})]


def regressions(lock, records):
    found = []
    locked = history(lock)
    for family, variants in records.items():
        for variant, record in variants.items():
            newer = sorted((version for version, entry in locked.get(family, {}).items()
                            if entry["variant"] == variant and version_key(version) > version_key(record["version"])),
                           key=version_key)
            if newer:
                found.append({"family": family, "variant": variant, "version": record["version"],
                              "locked": newer[-1], "newer": newer})
    return found


def unnormalized(records):
    found = []
    for family, variants in records.items():
        for variant, record in variants.items():
            normalized = normalized_version(record["version"])
            if normalized != record["version"]:
                found.append({"family": family, "variant": variant, "version": record["version"],
                              "normalized": normalized})
    return found


def append(packages, records):
    """A copy of the history `packages` plus the computed versions it lacks; existing entries stay as they are."""
    result = {family: dict(versions) for family, versions in packages.items()}
    for family, versions in entries(records).items():
        for version, entry in versions.items():
            result.setdefault(family, {}).setdefault(version, entry)
    return result


def first_entries(locks):
    """{family: {version: (origin, entry)}}: the entry of each version in the first of `locks` that holds it.

    `locks` is a history [(origin, lock document)], oldest first."""
    first = {}
    for origin, lock in locks:
        for family, versions in history(lock).items():
            for version, entry in versions.items():
                first.setdefault(family, {}).setdefault(version, (origin, entry))
    return first


def rewritten_entries(locks, lock):
    """Versions of the history `locks` [(origin, lock document)], oldest first, whose first entry `lock` removed
    (changes None) or changed; "origin" names the lock that first holds the version."""
    found = []
    current = history(lock)
    for family, versions in first_entries(locks).items():
        for version, (origin, entry) in versions.items():
            now = current.get(family, {}).get(version)
            if now != entry:
                found.append({"family": family, "version": version, "origin": origin,
                              "changes": None if now is None else _changes(entry, now)})
    return found


def changed_entries(base, lock):
    """Entries of the `base` lock document that `lock` removed or changed."""
    return rewritten_entries([(None, base)], lock)


def stale(lock, records):
    """Why the lock needs `python scripts/upstream.py lock`: computed versions it lacks, or an outdated comment."""
    if lock is None:
        return [f"{LOCK} does not exist"]
    differences = [f"{_label(item['family'], item['variant'])} {item['version']}: not in the lock"
                   for item in missing(lock, records)]
    if lock.get("comment") != COMMENT:
        differences.append("the lock comment differs")
    return differences


def _label(family, variant):
    return family if variant == "default" else f"{family} ({variant})"


def _value(value):
    return "(none)" if value is None else value


def describe_conflict(conflict):
    return (f"{conflict['family']}: variants {', '.join(conflict['variants'])} all compute version "
            f"{conflict['version']}; each variant needs a version of its own. Fix the version formula of the "
            f"variants (to publish new inputs: {conflict['bump']}).")


def describe_violation(violation):
    lines = [f"{_label(violation['family'], violation['variant'])} {violation['version']} is already in the lock "
             "with other inputs:"]
    lines += [f"  {key}: {_value(old)} -> {_value(new)}" for key, old, new in violation["changes"]]
    lines.append(f"  Fix: {violation['bump']}, then run `python scripts/upstream.py lock` (task upstream-lock).")
    return "\n".join(lines)


def describe_regression(regression):
    return (f"{_label(regression['family'], regression['variant'])} moved back from {regression['locked']} to "
            f"{regression['version']}; nuget.org keeps the earlier upload of a version, so versions only move forward.")


def describe_unnormalized(item):
    return (f"{_label(item['family'], item['variant'])} {item['version']}: nuget.org identifies this version as "
            f"{item['normalized']}. Change its version formula so that it yields {item['normalized']} (no leading "
            "zeros, no fourth part 0, no build metadata, lower-case prerelease labels).")


def describe_changed_entry(item, origin):
    if item["changes"] is None:
        return f"{item['family']} {item['version']}: in the lock of {origin} but removed"
    return "\n".join([f"{item['family']} {item['version']}: changed since the lock of {origin}:"]
                     + [f"  {key}: {_value(old)} -> {_value(new)}" for key, old, new in item["changes"]])


def guard(lock, records):
    """Messages for rules 1, 2, 4 and 5: computed versions that cannot be published as they are."""
    return ([describe_unnormalized(item) for item in unnormalized(records)]
            + [describe_conflict(item) for item in conflicts(records)]
            + [describe_violation(item) for item in violations(lock, records)]
            + [describe_regression(item) for item in regressions(lock, records)])
