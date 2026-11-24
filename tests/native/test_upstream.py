"""scripts/upstream.py check/apply against a fake upstream built from the pinned versions; no network access.

Run with python -m unittest discover -s tests/native.
"""

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

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
repo_versions = package_inputs.repo_versions


def next_patch(version):
    """Synthetic newer release: increments the third component (the last one for four-part versions)."""
    parts = version.split(".")
    index = 2 if len(parts) == 3 else len(parts) - 1
    parts[index] = str(int(parts[index]) + 1)
    return ".".join(parts)


def fake_commit(text):
    return hashlib.sha1(text.encode()).hexdigest()


def nuspec_xml(package, version, dependencies=(), commit=None):
    items = "".join(f'<dependency id="{name}" version="{value}" exclude="Build" />' for name, value in dependencies)
    repository = f'<repository type="git" url="https://example.invalid/r" commit="{commit}" />' if commit else ""
    return (f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://schemas.microsoft.com/packaging/2013/05/'
            f'nuspec.xsd"><metadata><id>{package}</id><version>{version}</version>{repository}<dependencies>'
            f'<group targetFramework="net8.0">{items}</group><group targetFramework="net10.0">{items}</group>'
            f'</dependencies></metadata></package>').encode()


class FakeClient:
    def __init__(self):
        self.responses, self.refs, self.streams = {}, {}, {}

    def add_json(self, url, value):
        self.responses[url] = json.dumps(value).encode()

    def add_nuspec(self, package, version, dependencies=(), commit=None):
        url = f"{upstream.NUGET}/{package.lower()}/{version.lower()}/{package.lower()}.nuspec"
        self.responses[url] = nuspec_xml(package, version, dependencies, commit)

    def add_ref(self, repository, ref, commit):
        self.refs.setdefault(repository, {})[ref] = commit

    def get(self, url, accept=None):
        if url not in self.responses:
            raise upstream.NotFound(f"GET {url}: HTTP 404 Not Found")
        return self.responses[url]

    def json(self, url):
        return json.loads(self.get(url))

    def stream(self, url):
        yield from self.streams[url]

    def ls_remote(self, repository, *patterns):
        refs = self.refs.get(repository, {})
        return {pattern: refs[pattern] for pattern in patterns if pattern in refs}


# The official build containers in the runtime's pipeline resources: one pinned by digest, the others by tag only.
# Legs whose names contain linux_x64 come first, so a test fails when another leg is taken for linux_x64.
CONTAINER_TAGS = {"linux-x64": "azurelinux-3.0-net99.0-cross-amd64",
                  "linux-arm64": "azurelinux-3.0-net99.0-cross-arm64",
                  "linux-musl-x64": "azurelinux-3.0-net99.0-cross-amd64-musl",
                  "linux-musl-arm64": "azurelinux-3.0-net99.0-cross-arm64-musl"}
PINNED_DIGEST = "sha256:" + "1" * 64
MANIFESTS = {rid: f'{{"schemaVersion": 2, "config": "{rid}"}}'.encode() for rid in ("linux-x64", "linux-arm64", "linux-musl-arm64")}
PREREQS = "mcr.microsoft.com/dotnet-buildtools/prereqs"
PIPELINE_RESOURCES = f"""    containers:
      SourceBuild_linux_x64:
        image: {PREREQS}:almalinux-9-source-build-amd64

      linux_x64_dev_innerloop:
        image: {PREREQS}:ubuntu-24.04

      linux_arm64:
        image: {PREREQS}:{CONTAINER_TAGS["linux-arm64"]}
        env:
          ROOTFS_DIR: /crossrootfs/arm64

      linux_musl_x64:
        image: {PREREQS}:{CONTAINER_TAGS["linux-musl-x64"]}@{PINNED_DIGEST}
        env:
          ROOTFS_DIR: /crossrootfs/x64

      linux_musl_arm64:
        image: {PREREQS}:{CONTAINER_TAGS["linux-musl-arm64"]}
        env:
          ROOTFS_DIR: /crossrootfs/arm64

      linux_x64:
        image: {PREREQS}:{CONTAINER_TAGS["linux-x64"]}
        env:
          ROOTFS_DIR: /crossrootfs/x64
"""
CONTAINERS = {
    "linux-x64": f"{PREREQS}:{CONTAINER_TAGS['linux-x64']}@sha256:{hashlib.sha256(MANIFESTS['linux-x64']).hexdigest()}",
    "linux-arm64": f"{PREREQS}:{CONTAINER_TAGS['linux-arm64']}@sha256:"
                   f"{hashlib.sha256(MANIFESTS['linux-arm64']).hexdigest()}",
    "linux-musl-x64": f"{PREREQS}:{CONTAINER_TAGS['linux-musl-x64']}@{PINNED_DIGEST}",
    "linux-musl-arm64": f"{PREREQS}:{CONTAINER_TAGS['linux-musl-arm64']}@sha256:"
                        f"{hashlib.sha256(MANIFESTS['linux-musl-arm64']).hexdigest()}",
}


def manifest_url(rid):
    return f"{upstream.MCR}/v2/dotnet-buildtools/prereqs/manifests/{CONTAINER_TAGS[rid]}"


def add_pipeline_resources(client, commit):
    client.add_json(f"{upstream.GITHUB_API}/repos/dotnet/dotnet/contents/{upstream.VMR_PIPELINE_RESOURCES}?ref={commit}",
                    {"type": "file", "content": base64.b64encode(PIPELINE_RESOURCES.encode()).decode()})
    for rid, manifest in MANIFESTS.items():
        client.responses[manifest_url(rid)] = manifest


def release(runtime, sdk, security=False, cves=()):
    return {"release-version": runtime, "release-date": "2099-01-01", "security": security,
            "cve-list": [{"cve-id": cve} for cve in cves], "runtime": {"version": runtime},
            "sdk": {"version": sdk}, "sdks": [{"version": sdk}]}


# An excerpt of ANGLE's DEPS in its real format (the depot_tools entry is copied from a chromium/* branch).
DEPS = """# This file is used to manage the dependencies of the ANGLE git repo.

vars = {
  'chromium_git': 'https://chromium.googlesource.com',
}

deps = {
  'build': {
    'url': Var('chromium_git') + '/chromium/src/build.git@BUILD',
    'condition': 'not build_with_chromium',
  },

  'third_party/dawn': {
    'url': Var('dawn_git') + '/dawn.git' + '@' +  Var('dawn_revision'),
    'condition': 'checkout_angle_dawn_deps and not build_with_chromium'
  },

  'third_party/depot_tools': {
    'url': Var('chromium_git') + '/chromium/tools/depot_tools.git@DEPOT_TOOLS',
    'condition': 'not build_with_chromium',
  },
}

hooks = [
  {
    # Ensure that the DEPS'd "depot_tools" has its self-update capability
    # disabled.
    'name': 'disable_depot_tools_selfupdate',
    'pattern': '.',
    'condition': 'not build_with_chromium',
    'action': [
        'python3',
        'third_party/depot_tools/update_depot_tools_toggle.py',
        '--disable',
    ],
  },
]
"""


def deps_text(depot_tools, build="b" * 40):
    return DEPS.replace("DEPOT_TOOLS", depot_tools).replace("BUILD", build)


def angle_source(root):
    text = (root / "native/angle/conandata.yml").read_text(encoding="utf-8")
    return (re.search(r'^sources:\n  url: "([^"]+)"', text, re.M).group(1),
            re.search(r'^depot_tools:\n(?:  .*\n)*?  commit: "([0-9a-f]+)"', text, re.M).group(1))


def tool_pins(root):
    return {tool.name: tool.pin(root) for tool in (*upstream.TOOLS, upstream.LLVM)}


TOOL_FILES = list(dict.fromkeys([tool.path for tool in upstream.TOOLS] + [upstream.LLVM_ACTION]))


def fake_upstream(root, dotnet=False, security=True, mismatched_vmr=False, avalonia=False, avalonia_skia=None,
                  skiasharp=False, vulnerable=False, angle_head=None, eol=(), angle_depot_tools=None,
                  avalonia_minor=False, avalonia_major=False, skiasharp_milestone=False, tools=False, llvm=None,
                  skiasharp_build=None):
    """A FakeClient whose releases are the repository's pins plus the requested newer releases."""
    pinned = repo_versions.load(root)
    client = FakeClient()

    index = []
    for runtime in pinned["runtimes"].values():
        channel = ".".join(runtime.split(".")[:2])
        source = pinned["hostlibs-sources"][runtime]
        releases = [release(runtime, source["tag"][1:])]
        client.add_ref(upstream.VMR, f"refs/tags/{source['tag']}", source["commit"])
        client.add_nuspec("Microsoft.NETCore.App.Ref", runtime, commit=source["commit"])
        if dotnet:
            new, sdk = next_patch(runtime), next_patch(source["tag"][1:])
            releases.insert(0, release(new, sdk, security, [f"CVE-2099-{channel}"] if security else []))
            # An annotated tag: the peeled commit is the one to pin.
            client.add_ref(upstream.VMR, f"refs/tags/v{sdk}", fake_commit("tag object " + sdk))
            client.add_ref(upstream.VMR, f"refs/tags/v{sdk}^{{}}", fake_commit(sdk))
            client.add_nuspec("Microsoft.NETCore.App.Ref", new,
                              commit=fake_commit("other") if mismatched_vmr else fake_commit(sdk))
            add_pipeline_resources(client, fake_commit(sdk))
        url = f"https://example.invalid/release-metadata/{channel}/releases.json"
        index.append({"channel-version": channel, "releases.json": url, "eol-date": "2099-01-01",
                      "support-phase": "eol" if channel in eol else "active"})
        client.add_json(url, {"releases": releases})
    client.add_json(upstream.DOTNET_INDEX, {"releases-index": index})

    current = pinned["avalonia"]
    harfbuzz = pinned["harfbuzz"][pinned["skiasharp"]]

    def add_avalonia(version, skia):
        client.add_nuspec("Avalonia.Desktop", version, [("Avalonia", version), ("Avalonia.Skia", version),
                                                        ("Avalonia.Win32", version), ("Tmds.DBus.Protocol", "0.1")])
        client.add_nuspec("Avalonia", version, commit=fake_commit("avalonia " + version))
        client.add_nuspec("Avalonia.Skia", version, [("Avalonia", version), ("SkiaSharp", skia), ("HarfBuzzSharp", harfbuzz)])
        client.add_nuspec("Avalonia.Win32", version, [("Avalonia", version),
                                                      ("Avalonia.Angle.Windows.Natives", pinned["angle"])])
        client.add_ref(upstream.AVALONIA_REPOSITORY, f"refs/tags/{version}", fake_commit("avalonia " + version))
        client.streams[upstream.AVALONIA_ARCHIVE.format(commit=fake_commit("avalonia " + version))] = \
            [b"archive ", version.encode()]

    versions = [current, next_patch(current) + "-beta1"]
    add_avalonia(current, pinned["skiasharp"])
    if avalonia:
        versions.append(next_patch(current))
        add_avalonia(next_patch(current), avalonia_skia or pinned["skiasharp"])
    major, minor = (int(part) for part in current.split(".")[:2])
    if avalonia_minor:
        # Two releases of the next minor (the newer one is the candidate) and a newer prerelease.
        versions += [f"{major}.{minor + 1}.0", avalonia_minor_version(current), f"{major}.{minor + 1}.2-beta1"]
        add_avalonia(avalonia_minor_version(current), pinned["skiasharp"])
    if avalonia_major:
        versions.append(f"{major + 1}.0.0")
    client.add_json(f"{upstream.NUGET}/avalonia/index.json", {"versions": versions})

    build_files = json.loads((Path(root) / upstream.SKIASHARP_BUILD).read_text(encoding="utf-8"))

    def add_skiasharp(version, harfbuzz_version, line):
        client.add_json(f"{upstream.GITHUB_API}/repos/mono/SkiaSharp/contents/externals/skia?ref=v{version}",
                        {"type": "submodule", "sha": fake_commit("skia " + version)})
        # Unchanged build files unless skiasharp_build maps a path to its new text (None: removed at the tag).
        for path, sha in build_files.get(line, {}).items():
            text = (skiasharp_build or {}).get(path, False)
            if text is not False:
                client.add_json(f"{upstream.GITHUB_API}/repos/mono/SkiaSharp/git/blobs/{sha}",
                                {"content": base64.b64encode(f"old {path}\n".encode()).decode()})
                if text is None:
                    continue
                sha = hashlib.sha1(text.encode()).hexdigest()
                client.add_json(f"{upstream.GITHUB_API}/repos/mono/SkiaSharp/git/blobs/{sha}",
                                {"content": base64.b64encode(text.encode()).decode()})
            client.add_json(f"{upstream.GITHUB_API}/repos/mono/SkiaSharp/contents/{path}?ref=v{version}",
                            {"type": "file", "sha": sha})
        client.add_nuspec("SkiaSharp.HarfBuzz", version, [("SkiaSharp", version), ("HarfBuzzSharp", harfbuzz_version)])
        client.add_nuspec("SkiaSharp", version, commit=fake_commit("skiasharp " + version))
        client.add_ref(upstream.SKIASHARP_REPOSITORY, f"refs/tags/v{version}", fake_commit("skiasharp " + version))

    lines = pinned["skiasharp-versions"]
    skia_versions = list(lines)
    for line in lines:
        line_harfbuzz = pinned["skiasharp-sources"][line]["harfbuzz"]
        if skiasharp:
            skia_versions.append(next_patch(line))
            add_skiasharp(next_patch(line), next_patch(line_harfbuzz), line)
        if skiasharp_milestone:
            skia_versions.append(skiasharp_milestone_version(line))
            add_skiasharp(skiasharp_milestone_version(line), next_patch(next_patch(line_harfbuzz)), line)
    client.add_json(f"{upstream.NUGET}/skiasharp/index.json", {"versions": skia_versions})
    client.add_json(f"{upstream.NUGET}/harfbuzzsharp/index.json",
                    {"versions": [source["harfbuzz"] for source in pinned["skiasharp-sources"].values()]})

    angle_url, depot_tools = angle_source(root)
    head = angle_head or pinned["angle-commit"] or "2" * 40
    client.add_ref(angle_url, f"refs/heads/{pinned['angle-branch']}", head)
    client.responses[f"{angle_url[:-4]}/+/{head}/DEPS?format=TEXT"] = \
        base64.b64encode(deps_text(angle_depot_tools or depot_tools).encode())

    base = {"skiasharp": [{"url": "https://example.invalid/old", "severity": 2, "versions": "(, 1.0.0)"}]}
    if vulnerable:
        base["avalonia"] = [{"url": "https://example.invalid/advisory", "severity": 3, "versions": f"[{current}]"}]
    client.add_json(upstream.NUGET_VULNERABILITIES, [{"@name": "base", "@id": "https://example.invalid/base.json"},
                                                     {"@name": "update", "@id": "https://example.invalid/update.json"}])
    client.add_json("https://example.invalid/base.json", base)
    client.add_json("https://example.invalid/update.json", [])

    pins = tool_pins(root)
    latest = {name: next_patch(version) if tools else version for name, version in pins.items()}
    client.add_json(f"{upstream.GITHUB_API}/repos/go-task/task/releases/latest", {"tag_name": "v" + latest["Task"]})
    client.add_json(f"{upstream.GITHUB_API}/repos/astral-sh/uv/releases/latest", {"tag_name": latest["uv"]})
    for name in ("conan", "ninja"):
        # A newer release candidate and a newer yanked release are not candidates.
        client.add_json(f"{upstream.PYPI}/{name}/json", {"info": {"version": latest[name]}, "releases": {
            pins[name]: [{"yanked": False}], latest[name]: [{"yanked": False}],
            next_patch(latest[name]) + "rc1": [{"yanked": False}], next_patch(next_patch(latest[name])): [{"yanked": True}]}})
    for name in ("TUnit", "Microsoft.Build.Locator"):
        client.add_json(f"{upstream.NUGET}/{name.lower()}/index.json",
                        {"versions": list(dict.fromkeys([pins[name], latest[name], next_patch(latest[name]) + "-beta"]))})
    llvm = llvm or pins["LLVM"]
    llvm_major = int(llvm.split(".")[0])

    def llvm_release(version, **flags):
        names = [f"LLVM-{version}-win64.msi", "clang.src.tar.xz"]
        return dict({"tag_name": f"llvmorg-{version}", "assets": [{"name": name} for name in names]}, **flags)

    client.add_json(upstream.LLVM_RELEASES, [llvm_release(f"{llvm_major + 2}.1.0", draft=True),
                                             llvm_release(f"{llvm_major + 1}.1.0", prerelease=True),
                                             {"tag_name": f"llvmorg-{llvm_major + 1}.1.0-rc1", "prerelease": True},
                                             llvm_release(llvm), llvm_release(pins["LLVM"])])
    return client


def avalonia_minor_version(current):
    major, minor = (int(part) for part in current.split(".")[:2])
    return f"{major}.{minor + 1}.1"


def skiasharp_milestone_version(line):
    major, milestone = (int(part) for part in line.split(".")[:2])
    return f"{major}.{milestone + 1}.0"


class UpstreamTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hostforge-upstream-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in [*package_inputs.SOURCES, *TOOL_FILES, upstream.SKIASHARP_BUILD]:
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, self.root / relative)
        self.relock()
        self.pinned = repo_versions.load(self.root)

    def relock(self):
        records = package_inputs.compute(self.root)
        (self.root / package_inputs.LOCK).write_text(package_inputs.render(package_inputs.entries(records)),
                                                     encoding="utf-8")

    def run_main(self, client, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = upstream.main(["--root", str(self.root), *arguments], client=client)
        return code, stdout.getvalue(), stderr.getvalue()

    def apply(self, client, *arguments):
        """apply with --metadata into a file outside the root: (code, stdout, stderr, metadata or None)."""
        directory = tempfile.TemporaryDirectory(prefix="hostforge-metadata-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "metadata.json"
        code, stdout, stderr = self.run_main(client, "apply", *arguments, "--metadata", str(path))
        metadata = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return code, stdout, stderr, metadata

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def props(self):
        return package_inputs.evaluate([self.root / package_inputs.PROPS])

    def assert_consistent(self):
        self.assertEqual([], repo_versions.problems(repo_versions.load(self.root)))
        records = package_inputs.compute(self.root)
        lock = package_inputs.read_lock(self.root)
        self.assertEqual([], package_inputs.guard(lock, records))
        self.assertEqual([], package_inputs.stale(lock, records))


class CheckTests(UpstreamTestCase):
    def test_reports_newer_security_releases(self):
        client = fake_upstream(self.root, dotnet=True)
        summary = self.root / "summary.md"
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            code, stdout, _ = self.run_main(client, "check", "--fail-on-security")
        self.assertEqual(1, code)
        for runtime in self.pinned["runtimes"].values():
            channel = ".".join(runtime.split(".")[:2])
            self.assertIn(f"CVE-2099-{channel}", stdout)
            self.assertIn(next_patch(runtime), stdout)
        markdown = summary.read_text(encoding="utf-8")
        self.assertIn("## Upstream check", markdown)
        self.assertIn("action needed", markdown.lower())

    def test_vulnerable_pinned_package_fails(self):
        code, stdout, _ = self.run_main(fake_upstream(self.root, vulnerable=True), "check", "--fail-on-security")
        self.assertEqual(1, code)
        self.assertIn("https://example.invalid/advisory", stdout)
        self.assertEqual(0, self.run_main(fake_upstream(self.root, vulnerable=True), "check")[0])

    def test_unreachable_service_is_an_error(self):
        client = fake_upstream(self.root)
        del client.responses[upstream.DOTNET_INDEX]
        code, stdout, _ = self.run_main(client, "check", "--fail-on-security")
        self.assertEqual(2, code)
        self.assertIn(upstream.DOTNET_INDEX, stdout)


class ApplyTests(UpstreamTestCase):
    def test_dotnet_patch_updates(self):
        major = int(self.props()["_Major"])
        code, stdout, stderr = self.run_main(fake_upstream(self.root, dotnet=True), "apply", "--dotnet")
        self.assertEqual(0, code, stderr)
        pinned = repo_versions.load(self.root)
        for framework, runtime in self.pinned["runtimes"].items():
            new = next_patch(runtime)
            sdk = next_patch(self.pinned["hostlibs-sources"][runtime]["tag"][1:])
            self.assertEqual(new, pinned["runtimes"][framework])
            self.assertEqual({"tag": f"v{sdk}", "commit": fake_commit(sdk), "containers": CONTAINERS},
                             pinned["hostlibs-sources"][new])
        self.assertEqual(major + 1, int(self.props()["_Major"]), "one increment per run")
        self.assertIn("CVE-2099-", stdout)
        self.assert_consistent()

    def test_refuses_an_unverified_vmr_commit(self):
        before = self.snapshot()
        code, stdout, stderr = self.run_main(fake_upstream(self.root, dotnet=True, mismatched_vmr=True),
                                             "apply", "--dotnet")
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertIn("not applied", stdout)

    def test_dotnet_patch_before_an_unpublished_one(self):
        # releases.json lists the newest patch before nuget.org has its Ref nuspec: the patch before it applies.
        client = fake_upstream(self.root, dotnet=True, security=False)
        framework, runtime = next(iter(self.pinned["runtimes"].items()))
        url = f"https://example.invalid/release-metadata/{'.'.join(runtime.split('.')[:2])}/releases.json"
        newest = next_patch(next_patch(runtime))
        sdk = next_patch(next_patch(self.pinned["hostlibs-sources"][runtime]["tag"][1:]))
        releases = json.loads(client.responses[url])
        releases["releases"].insert(0, release(newest, sdk, security=True, cves=["CVE-2099-0002"]))
        client.add_json(url, releases)
        client.add_ref(upstream.VMR, f"refs/tags/v{sdk}", fake_commit(sdk))
        code, _, stderr, metadata = self.apply(client, "--dotnet")
        self.assertEqual(0, code, stderr)
        self.assertEqual(next_patch(runtime), repo_versions.load(self.root)["runtimes"][framework])
        notes = [note for note in metadata["notes"] if note.startswith(framework)]
        self.assertEqual(1, len(notes), metadata["notes"])
        self.assertRegex(notes[0], rf"^{re.escape(f'{framework} {newest} not applied: ')}.*HTTP 404")
        # The update does not claim the CVEs of the release it does not apply.
        self.assertNotIn("CVE-2099-0002", " ".join(metadata["updates"]))
        self.assertEqual({"before": True, "after": True, "cves": []}, metadata["security"])
        self.assert_consistent()

    def test_avalonia_patch_update(self):
        props = self.root / package_inputs.PROPS
        props.write_text(re.sub(r"(<AvaloniaNativePackageVersion>\$\(AvaloniaNativeVersion\)\.)\d+",
                                r"\g<1>2", props.read_text(encoding="utf-8")), encoding="utf-8")
        self.relock()
        previous = self.props()
        old = self.pinned["avalonia"]
        old_commit = self.pinned["avalonia-sources"][self.pinned["avalonia-native"]]["commit"]
        readme = self.root / "src/package-avalonia-native-static/README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(f"Archives are built from Avalonia {old} source, commit\n`{old_commit}`.\n", encoding="utf-8")
        notes = self.root / "docs/notes.md"
        notes.parent.mkdir()
        notes.write_text(f"Avalonia source `{old_commit}`.\n", encoding="utf-8")
        code, stdout, stderr = self.run_main(fake_upstream(self.root, avalonia=True), "apply", "--avalonia")
        self.assertEqual(0, code, stderr)
        new = next_patch(self.pinned["avalonia"])
        new_commit = fake_commit("avalonia " + new)
        self.assertEqual(f"Archives are built from Avalonia {new} source, commit\n`{new_commit}`.\n",
                         readme.read_text(encoding="utf-8"))
        self.assertIn(f"docs/notes.md:1: Avalonia source `{old_commit}`.", stdout)
        self.assertIn(f"Avalonia {old} -> {new} (", stdout)
        values = self.props()
        self.assertEqual((new, new), (values["AvaloniaVersion"], values["AvaloniaNativeVersion"]))
        self.assertEqual(f"{new}.1", values["AvaloniaNativePackageVersion"])
        self.assertEqual(previous["_Major"], values["_Major"])
        self.assertEqual(previous["AvaloniaAppHostPackageVersion"], values["AvaloniaAppHostPackageVersion"])
        self.assertEqual(f"{new}.{values['_Major']}", values["AvaloniaAppHostMacOSPackageVersion"])
        commit = fake_commit("avalonia " + new)
        source = repo_versions.load(self.root)["avalonia-sources"][new]
        self.assertEqual({"url": upstream.AVALONIA_ARCHIVE.format(commit=commit), "commit": commit,
                          "sha256": hashlib.sha256(b"archive " + new.encode()).hexdigest()}, source)
        self.assertIn(self.pinned["avalonia"], repo_versions.load(self.root)["avalonia-sources"])
        self.assert_consistent()

    def test_avalonia_dependency_change_is_report_only(self):
        before = self.snapshot()
        client = fake_upstream(self.root, avalonia=True, avalonia_skia=next_patch(self.pinned["skiasharp"]))
        code, stdout, stderr = self.run_main(client, "apply", "--avalonia")
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertIn("not applied", stdout)
        self.assertIn("SkiaSharp", stdout)

    def test_skiasharp_patch_updates(self):
        major = int(self.props()["_Major"])
        code, stdout, stderr = self.run_main(fake_upstream(self.root, skiasharp=True), "apply", "--skiasharp")
        self.assertEqual(0, code, stderr)
        pinned = repo_versions.load(self.root)
        expected = {next_patch(line): {"skia-commit": fake_commit("skia " + next_patch(line)),
                                       "harfbuzz": next_patch(self.pinned["skiasharp-sources"][line]["harfbuzz"]),
                                       "skiasharp-commit": fake_commit("skiasharp " + next_patch(line))}
                    for line in self.pinned["skiasharp-versions"]}
        self.assertEqual(expected, pinned["skiasharp-sources"])
        self.assertEqual(next_patch(self.pinned["skiasharp"]), pinned["skiasharp"])
        self.assertEqual(major + 1, int(self.props()["_Major"]))
        self.assert_consistent()


def evaluated(root):
    return package_inputs.compute(root)


def set_text(path, pattern, replacement):
    text = path.read_text(encoding="utf-8")
    changed, count = re.subn(pattern, replacement, text, flags=re.M)
    if count != 1:
        raise AssertionError(f"{path.name}: {pattern} matched {count} times")
    path.write_text(changed, encoding="utf-8")


class AngleTests(UpstreamTestCase):
    HEAD = "3" * 40
    DEPOT_TOOLS = "4" * 40

    def angle(self):
        text = (self.root / "native/angle/conandata.yml").read_text(encoding="utf-8")
        return upstream._yaml_section(text, "sources")["commit"], upstream._yaml_section(text, "depot_tools")["commit"]

    def test_moved_head_pins_the_head_and_its_depot_tools(self):
        records, (_, old_tools) = evaluated(self.root), angle_source(self.root)
        major = int(self.props()["_Major"])
        client = fake_upstream(self.root, angle_head=self.HEAD, angle_depot_tools=self.DEPOT_TOOLS)
        code, stdout, stderr, metadata = self.apply(client, "--angle")
        self.assertEqual(0, code, stderr)
        self.assertEqual((self.HEAD, self.DEPOT_TOOLS), self.angle())
        # ANGLE keeps AngleVersion, so the package gets the next revision; ANGLE is an AppHost input too.
        old = records["ChsBuffer.Angle.Static"]["default"]["version"]
        base, revision = old.rsplit(".", 1)
        self.assertEqual(f"{base}.{int(revision) + 1}", evaluated(self.root)["ChsBuffer.Angle.Static"]["default"]["version"])
        self.assertEqual(major + 1, int(self.props()["_Major"]))
        self.assertEqual(self.pinned["angle"], self.props()["AngleVersion"])
        self.assertEqual(["Directory.Build.props", "native/angle/conandata.yml", package_inputs.LOCK], metadata["changed"])
        self.assertIn(f"depot_tools {old_tools} -> {self.DEPOT_TOOLS} from DEPS", stdout)
        self.assertRegex(metadata["title"], rf"^build\(deps\): update ANGLE to {re.escape(self.pinned['angle-branch'])}@3+$")
        self.assertIn(self.HEAD, metadata["target"])
        self.assertIn(self.DEPOT_TOOLS, metadata["target"])
        self.assertTrue(any(line.startswith("ChsBuffer.Angle.Static: revision") for line in metadata["updates"]))
        self.assert_consistent()


class PackageVersionBumpTests(UpstreamTestCase):
    """bump_package_versions: every family whose locked version would get other inputs gets a new version."""

    def bump(self):
        lock = package_inputs.read_lock(self.root)
        before = evaluated(self.root)
        updates = upstream.bump_package_versions(self.root, lock)
        after = evaluated(self.root)
        self.assertEqual([], package_inputs.guard(lock, after))
        changed = {(family, variant): (before[family][variant]["version"], record["version"])
                   for family, variants in after.items() for variant, record in variants.items()
                   if record["version"] != before[family][variant]["version"]}
        return changed, updates

    @staticmethod
    def next_number(version):
        head, number = re.fullmatch(r"(.*\.)(\d+)", version).groups()
        return f"{head}{int(number) + 1}"

    def apphost(self, before):
        return {("ChsBuffer.Avalonia.AppHost", variant): (record["version"], None)
                for variant, record in before["ChsBuffer.Avalonia.AppHost"].items()}

    def assert_bumped(self, changed, expected, apphost):
        """expected: {(family, variant): old}; the AppHost moves to the next <_Major> when `apphost`."""
        for key, old in expected.items():
            self.assertEqual((old, self.next_number(old)), changed.pop(key), key)
        moved = {key: value for key, value in changed.items() if key[0] == "ChsBuffer.Avalonia.AppHost"}
        self.assertEqual(apphost, bool(moved))
        for key in moved:
            del changed[key]
        self.assertEqual({}, changed)

    def test_skia_commit_of_one_line(self):
        before = evaluated(self.root)
        line = self.pinned["skiasharp-versions"][0]
        sources = json.loads((self.root / "native/skiasharp/versions.json").read_text(encoding="utf-8"))
        sources[line][0] = "1" * 40
        (self.root / "native/skiasharp/versions.json").write_text(json.dumps(sources), encoding="utf-8")
        variant = package_inputs._skia_variant(line)
        changed, _ = self.bump()
        self.assert_bumped(changed, {("ChsBuffer.SkiaSharp.Static", variant):
                                     before["ChsBuffer.SkiaSharp.Static"][variant]["version"]}, apphost=True)


class KeepGoingTests(UpstreamTestCase):
    def test_failed_lookup_is_skipped_and_recorded(self):
        client = fake_upstream(self.root, dotnet=True, avalonia=True)
        del client.responses[f"{upstream.NUGET}/avalonia.desktop/{next_patch(self.pinned['avalonia'])}/avalonia.desktop.nuspec"]
        before = self.snapshot()
        code, _, stderr, metadata = self.apply(client)
        self.assertEqual(2, code)
        self.assertIn("Nothing was written", stderr)
        self.assertIn("--keep-going", stderr)
        self.assertIsNone(metadata)
        self.assertEqual(before, self.snapshot())

        code, stdout, stderr, metadata = self.apply(client, "--keep-going")
        self.assertEqual(0, code, stderr)
        self.assertEqual({next_patch(runtime) for runtime in self.pinned["runtimes"].values()},
                         set(repo_versions.load(self.root)["runtimes"].values()))
        self.assertEqual(self.pinned["avalonia"], self.props()["AvaloniaVersion"])
        self.assertEqual(1, len(metadata["errors"]))
        self.assertRegex(metadata["errors"][0], r"^Avalonia: GET .*HTTP 404")
        self.assertIn("Lookup errors", stdout)
        self.assert_consistent()


class MetadataTests(UpstreamTestCase):
    def test_security_release(self):
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, dotnet=True))
        self.assertEqual(0, code, stderr)
        cves = sorted(f"CVE-2099-{'.'.join(runtime.split('.')[:2])}" for runtime in self.pinned["runtimes"].values())
        self.assertEqual({"before": True, "after": False, "cves": cves}, metadata["security"])
        self.assertEqual("inputs", metadata["group"])
        self.assertEqual([package_inputs.PROPS, "native/hostlibs/versions.json", package_inputs.LOCK], metadata["changed"])
        self.assertTrue(metadata["title"].startswith("fix(deps): update .NET "), metadata["title"])
        self.assertTrue(metadata["title"].endswith(" (security)"))
        self.assertLessEqual(len(metadata["title"]), upstream.TITLE_LENGTH)
        self.assertNotIn("\n", metadata["title"])
        for cve in cves:
            self.assertIn(cve, metadata["body"])
        self.assertIn("ChsBuffer.Avalonia.AppHost: &lt;_Major> -> ", metadata["body"])
        self.assertNotIn("<!--", metadata["body"])
        self.assertEqual(len(self.pinned["runtimes"]) + 1, len(metadata["updates"]), "one line per runtime, one bump")
        self.assertEqual([], metadata["errors"])

    def test_unresolved_finding_is_not_a_fix(self):
        client = fake_upstream(self.root, dotnet=True, mismatched_vmr=True, avalonia=True)
        code, _, stderr, metadata = self.apply(client)
        self.assertEqual(0, code, stderr)
        self.assertEqual({"before": True, "after": True, "cves": []}, metadata["security"])
        self.assertTrue(metadata["title"].startswith("build(deps): update Avalonia"), metadata["title"])
        self.assertTrue(any("not applied" in note for note in metadata["notes"]))
        self.assertIn("### Not applied", metadata["body"])


class AvaloniaMinorTests(UpstreamTestCase):
    def test_newest_minor_with_the_same_dependencies(self):
        props = self.root / package_inputs.PROPS
        set_text(props, r"(<AvaloniaNativePackageVersion>\$\(AvaloniaNativeVersion\)\.)\d+", r"\g<1>3")
        self.relock()
        previous = self.props()
        client = fake_upstream(self.root, avalonia=True, avalonia_minor=True, avalonia_major=True)
        code, stdout, stderr, metadata = self.apply(client, "--group", "avalonia-minor")
        self.assertEqual(0, code, stderr)
        new = avalonia_minor_version(self.pinned["avalonia"])
        values = self.props()
        self.assertEqual((new, new, f"{new}.1"), (values["AvaloniaVersion"], values["AvaloniaNativeVersion"],
                                                  values["AvaloniaNativePackageVersion"]))
        self.assertEqual(previous["_Major"], values["_Major"])
        self.assertEqual(previous["AvaloniaAppHostPackageVersion"], values["AvaloniaAppHostPackageVersion"])
        self.assertEqual(f"{new}.{values['_Major']}", values["AvaloniaAppHostMacOSPackageVersion"])
        commit = fake_commit("avalonia " + new)
        self.assertEqual({"url": upstream.AVALONIA_ARCHIVE.format(commit=commit), "commit": commit,
                          "sha256": hashlib.sha256(b"archive " + new.encode()).hexdigest()},
                         repo_versions.load(self.root)["avalonia-sources"][new])
        self.assertEqual(f"build(deps): update Avalonia to {new}", metadata["title"])
        self.assertIsNone(metadata["security"])
        newer_major = f"{int(self.pinned['avalonia'].split('.')[0]) + 1}.0.0"
        self.assertEqual([f"Avalonia newer major releases (report only): {newer_major}"], metadata["notes"])
        self.assert_consistent()


class SkiaSharpLineTests(UpstreamTestCase):
    def test_newer_milestone_of_one_line(self):
        line, other = self.pinned["skiasharp-versions"][:2]
        group = f"skiasharp-{line.split('.')[0]}"
        client = fake_upstream(self.root, skiasharp=True, skiasharp_milestone=True)
        code, _, stderr, metadata = self.apply(client, "--group", group)
        self.assertEqual(0, code, stderr)
        new = skiasharp_milestone_version(line)
        sources = repo_versions.load(self.root)["skiasharp-sources"]
        self.assertEqual({"skia-commit": fake_commit("skia " + new),
                          "harfbuzz": next_patch(next_patch(self.pinned["skiasharp-sources"][line]["harfbuzz"])),
                          "skiasharp-commit": fake_commit("skiasharp " + new)},
                         sources[new])
        self.assertEqual(self.pinned["skiasharp-sources"][other], sources[other])
        self.assertNotIn(line, sources)
        self.assertEqual(f"build(deps): update SkiaSharp to {new}", metadata["title"])
        self.assertIn("ChsBuffer.SkiaSharp.Static (skia", metadata["body"])
        self.assert_consistent()

    def build_record(self):
        return json.loads((self.root / upstream.SKIASHARP_BUILD).read_text(encoding="utf-8"))

    def test_changed_build_files_need_a_review(self):
        line = self.pinned["skiasharp-versions"][0]
        changed, removed = sorted(self.build_record()[line])[:2]
        client = fake_upstream(self.root, skiasharp=True, skiasharp_milestone=True,
                               skiasharp_build={changed: "new ~~~ text\n", removed: None})
        code, _, stderr, metadata = self.apply(client, "--group", f"skiasharp-{line.split('.')[0]}")
        self.assertEqual(0, code, stderr)
        new = skiasharp_milestone_version(line)
        record = self.build_record()
        self.assertNotIn(line, record)
        self.assertNotIn(new, record, "a changed build needs a review before it is recorded")
        body = metadata["body"]
        self.assertIn(f"### Upstream build changes ({line} -> {new})", body)
        self.assertIn(f"-old {changed}", body)
        self.assertIn("+new ~~~ text", body)
        self.assertIn("~~~~diff", body, "the fence is longer than any run of tildes in the diff")
        self.assertIn(f"`{removed}` (removed)", body)
        self.assertIn(f'"{changed}": "{hashlib.sha1(b"new ~~~ text" + chr(10).encode()).hexdigest()}"', body)
        self.assertIn(f'"{removed}": null', body)
        self.assertTrue(any("see Upstream build changes" in note for note in metadata["notes"]))


class ToolsTests(UpstreamTestCase):
    def test_updates_every_tool(self):
        # uv.lock is not tracked (`uv run` relocks by itself): a local one is left alone.
        lock = self.root / "uv.lock"
        lock.write_text("version = 1\n", encoding="utf-8")
        pins = tool_pins(self.root)
        before = package_inputs.compute(self.root)
        msbuild = re.search(r'Include="Microsoft\.Build" Version="([^"]+)"',
                            (self.root / "tests/HostForge.TestInfra/HostForge.TestInfra.csproj").read_text(encoding="utf-8"))
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, tools=True), "--group", "tools")
        self.assertEqual(0, code, stderr)
        after = tool_pins(self.root)
        for tool in upstream.TOOLS:
            self.assertEqual(next_patch(pins[tool.name]), after[tool.name], tool.name)
        self.assertEqual(pins["LLVM"], after["LLVM"])
        self.assertEqual("version = 1\n", lock.read_text(encoding="utf-8"))
        self.assertEqual([".github/actions/install-task/action.yml", ".github/actions/install-uv/action.yml",
                          "pyproject.toml", "tests/Directory.Build.targets",
                          "tests/HostForge.TestInfra/HostForge.TestInfra.csproj"], metadata["changed"])
        self.assertIn(msbuild.group(0), (self.root / "tests/HostForge.TestInfra/HostForge.TestInfra.csproj")
                      .read_text(encoding="utf-8"), "Microsoft.Build follows the SDK")
        self.assertEqual(before, package_inputs.compute(self.root))
        self.assertEqual("build(deps): update 6 tools", metadata["title"], "too long with all names")
        self.assertEqual(6, len(metadata["updates"]))
        self.assertIsNone(metadata["security"])


class RepositoryRecordTests(unittest.TestCase):
    def test_every_skiasharp_version_records_its_reviewed_build_files(self):
        versions = json.loads((ROOT / "native/skiasharp/versions.json").read_text(encoding="utf-8"))
        record = json.loads((ROOT / upstream.SKIASHARP_BUILD).read_text(encoding="utf-8"))
        missing = sorted(set(versions) - set(record))
        self.assertEqual([], missing, f"The upstream build files of SkiaSharp {', '.join(missing)} are not reviewed: "
                         "port the changes listed in the pull request to native/skiasharp, then add the entry it "
                         f"shows to {upstream.SKIASHARP_BUILD}")
        self.assertEqual(sorted(versions), sorted(record), f"{upstream.SKIASHARP_BUILD} lists other versions")
        for version, files in record.items():
            self.assertTrue(files and all(isinstance(sha, str) for sha in files.values()), version)


class LlvmTests(UpstreamTestCase):
    def pins(self):
        text = (self.root / upstream.LLVM_ACTION).read_text(encoding="utf-8")
        return re.findall(r"key: llvm-([\w.]+)-win64", text) + re.findall(r"\$Version = '([^']+)'", text)

    def test_updates_both_pins(self):
        version = next_patch(self.pins()[0])
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, llvm=version), "--group", "llvm")
        self.assertEqual(0, code, stderr)
        self.assertEqual([version, version], self.pins())
        self.assertEqual([upstream.LLVM_ACTION], metadata["changed"])
        self.assertEqual(f"build(deps): update LLVM to {version}", metadata["title"])
        self.assertEqual([upstream.LLVM_NOTE], metadata["notes"])
        self.assertIn("bumps no package version", metadata["body"])
        self.assertIsNone(metadata["security"])


class HelperTests(unittest.TestCase):
    def test_version_ranges(self):
        self.assertTrue(upstream.in_range("1.5.0", "[1.0.0, 2.0.0)"))
        self.assertFalse(upstream.in_range("2.0.0", "[1.0.0, 2.0.0)"))
        self.assertTrue(upstream.in_range("2.0.0", "[1.0.0, 2.0.0]"))
        self.assertFalse(upstream.in_range("1.0.0", "(1.0.0, 2.0.0]"))
        self.assertTrue(upstream.in_range("0.9.0", "(, 1.0.0)"))
        self.assertTrue(upstream.in_range("1.0.0", "[1.0.0]"))
        self.assertFalse(upstream.in_range("1.0.1", "[1.0.0]"))
        self.assertTrue(upstream.in_range("3.0.0", "1.0.0"))
        self.assertTrue(upstream.in_range("5.0.0", "[4.0.0, )"))
        self.assertEqual("2.3.4.5", upstream.minimum("[2.3.4.5, )"))
        self.assertEqual("2.3.4.5", upstream.minimum("2.3.4.5"))


class FailureTests(UpstreamTestCase):
    """Failures keep their exit codes: 1 only for findings, 2 when a command cannot complete."""

    def test_end_of_life_channel_is_a_security_finding(self):
        channel = ".".join(next(iter(self.pinned["runtimes"].values())).split(".")[:2])
        code, stdout, _ = self.run_main(fake_upstream(self.root, eol=(channel,)), "check", "--fail-on-security")
        self.assertEqual(1, code)
        self.assertIn(f".NET {channel} is end of life", stdout)
        self.assertIn("Security: ACTION NEEDED", stdout)
        self.assertEqual(0, self.run_main(fake_upstream(self.root, eol=(channel,)), "check")[0])

    def test_failed_write_restores_every_file(self):
        before = self.snapshot()
        replace, calls = os.replace, []

        def flaky(source, destination):
            calls.append(Path(destination).name)
            if len(calls) == 2:
                raise PermissionError(13, "Permission denied", str(destination))
            return replace(source, destination)

        with mock.patch.object(upstream.os, "replace", side_effect=flaky):
            code, _, stderr = self.run_main(fake_upstream(self.root, dotnet=True), "apply", "--dotnet")
        self.assertEqual(2, code)
        self.assertIn("every file was restored", stderr)
        self.assertGreater(len(calls), 2, "the first file was replaced, then restored")
        self.assertEqual(before, self.snapshot())

    def test_keeps_byte_order_mark_and_line_endings(self):
        props = self.root / package_inputs.PROPS
        hostlibs = self.root / "native/hostlibs/versions.json"
        props.write_bytes(b"\xef\xbb\xbf" + props.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        hostlibs.write_bytes(hostlibs.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        code, _, stderr = self.run_main(fake_upstream(self.root, dotnet=True), "apply", "--dotnet")
        self.assertEqual(0, code, stderr)
        self.assertEqual({next_patch(runtime) for runtime in self.pinned["runtimes"].values()},
                         set(repo_versions.load(self.root)["runtimes"].values()))
        for path in (props, hostlibs):
            data = path.read_bytes()
            self.assertIn(b"\r\n", data, path.name)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), path.name)
        self.assertTrue(props.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertFalse(hostlibs.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assert_consistent()


class VmrSourceTests(unittest.TestCase):
    """The VMR tag of a runtime: the 1xx SDK tag whose commit built Microsoft.NETCore.App.Ref."""

    @staticmethod
    def client(tags):
        client = FakeClient()
        for tag, commit in tags.items():
            client.add_ref(upstream.VMR, f"refs/tags/{tag}", commit)
        client.add_nuspec("Microsoft.NETCore.App.Ref", "9.0.0", commit="c100")
        return client

    @staticmethod
    def release(*sdks):
        return {"runtime": {"version": "9.0.0"}, "sdk": {"version": sdks[0]}, "sdks": [{"version": sdk} for sdk in sdks]}

    def test_picks_the_1xx_sdk_built_from_the_ref_commit(self):
        # As released: 9.0.0 lists 9.0.101 before 9.0.100.
        client = self.client({"v9.0.101": "c101", "v9.0.100": "c100"})
        result = upstream.vmr_source(client, "9.0.0", self.release("9.0.101", "9.0.100"), "9.0")
        self.assertEqual(("v9.0.100", "c100", []), (result["tag"], result["commit"], result["problems"]))


class ClientTests(unittest.TestCase):
    def test_token_is_not_sent_after_a_redirect(self):
        client = upstream.Client(token="secret")
        request = client._request("https://api.github.com/repos/o/r/tarball/main", True)
        self.assertEqual("Bearer secret", request.get_header("Authorization"))
        redirected = urllib.request.HTTPRedirectHandler().redirect_request(
            request, io.BytesIO(), 302, "Found", {}, "https://codeload.example.invalid/o/r/tar.gz/main")
        self.assertIsNone(redirected.get_header("Authorization"))
        self.assertEqual(upstream.USER_AGENT, redirected.get_header("User-agent"))
        self.assertIsNone(client._request("https://api.nuget.org/v3/index.json", True).get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
