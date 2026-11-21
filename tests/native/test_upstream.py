"""scripts/upstream.py check/apply against a fake upstream built from the pinned versions; no network access.

Run with python -m unittest discover -s tests/native.
"""

import base64
import contextlib
import gzip
import hashlib
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
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
        self.responses, self.refs, self.streams, self.requests, self.accepts = {}, {}, {}, [], {}

    def add_json(self, url, value):
        self.responses[url] = json.dumps(value).encode()

    def add_nuspec(self, package, version, dependencies=(), commit=None):
        url = f"{upstream.NUGET}/{package.lower()}/{version.lower()}/{package.lower()}.nuspec"
        self.responses[url] = nuspec_xml(package, version, dependencies, commit)

    def add_ref(self, repository, ref, commit):
        self.refs.setdefault(repository, {})[ref] = commit

    def get(self, url, accept=None):
        self.requests.append(url)
        if accept:
            self.accepts[url] = accept
        if url not in self.responses:
            raise upstream.NotFound(f"GET {url}: HTTP 404 Not Found")
        return self.responses[url]

    def json(self, url):
        return json.loads(self.get(url))

    def stream(self, url):
        self.requests.append(url)
        yield from self.streams[url]

    def ls_remote(self, repository, *patterns):
        refs = self.refs.get(repository, {})
        return {pattern: refs[pattern] for pattern in patterns if pattern in refs}


# The official build containers in the runtime's pipeline resources: one pinned by digest, the others by tag only.
# Legs whose names contain linux_x64 come first, so a test fails when another leg is taken for linux_x64.
CONTAINER_TAGS = {"linux-x64": "azurelinux-3.0-net99.0-cross-amd64",
                  "linux-musl-x64": "azurelinux-3.0-net99.0-cross-amd64-musl",
                  "linux-musl-arm64": "azurelinux-3.0-net99.0-cross-arm64-musl"}
PINNED_DIGEST = "sha256:" + "1" * 64
MANIFESTS = {rid: f'{{"schemaVersion": 2, "config": "{rid}"}}'.encode() for rid in ("linux-x64", "linux-musl-arm64")}
PREREQS = "mcr.microsoft.com/dotnet-buildtools/prereqs"
PIPELINE_RESOURCES = f"""    containers:
      SourceBuild_linux_x64:
        image: {PREREQS}:almalinux-9-source-build-amd64

      linux_x64_dev_innerloop:
        image: {PREREQS}:ubuntu-24.04

      linux_arm64:
        image: {PREREQS}:azurelinux-3.0-net99.0-cross-arm64
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
    "linux-musl-x64": f"{PREREQS}:{CONTAINER_TAGS['linux-musl-x64']}@{PINNED_DIGEST}",
    "linux-musl-arm64": f"{PREREQS}:{CONTAINER_TAGS['linux-musl-arm64']}@sha256:"
                        f"{hashlib.sha256(MANIFESTS['linux-musl-arm64']).hexdigest()}",
}


def manifest_url(rid):
    return f"{upstream.MCR}/v2/dotnet-buildtools/prereqs/manifests/{CONTAINER_TAGS[rid]}"


def add_pipeline_resources(client, commit, text=PIPELINE_RESOURCES):
    client.add_json(f"{upstream.GITHUB_API}/repos/dotnet/dotnet/contents/{upstream.VMR_PIPELINE_RESOURCES}?ref={commit}",
                    {"type": "file", "content": base64.b64encode(text.encode()).decode()})
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

# Like native/angle/patches/angle-deps.patch: removes a dependency, with the depot_tools entry as context.
DEPS_PATCH = """diff --git a/DEPS b/DEPS
--- a/DEPS
+++ b/DEPS
@@ -11,10 +11,5 @@ deps = {
   },

-  'third_party/dawn': {
-    'url': Var('dawn_git') + '/dawn.git' + '@' +  Var('dawn_revision'),
-    'condition': 'checkout_angle_dawn_deps and not build_with_chromium'
-  },
-
   'third_party/depot_tools': {
     'url': Var('chromium_git') + '/chromium/tools/depot_tools.git@DEPOT_TOOLS',
     'condition': 'not build_with_chromium',
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
                  avalonia_minor=False, avalonia_minor_skia=None, avalonia_major=False, skiasharp_milestone=False,
                  skiasharp_major=False, tools=False, llvm=None, llvm_asset=True, skiasharp_build=None):
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
        add_avalonia(avalonia_minor_version(current), avalonia_minor_skia or pinned["skiasharp"])
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
    if skiasharp_major:
        skia_versions.append(f"{max(int(line.split('.')[0]) for line in lines) + 1}.0.0")
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

    def llvm_release(version, assets=True, **flags):
        names = [f"LLVM-{version}-win64.msi" if assets else f"LLVM-{version}-Linux-X64.tar.xz", "clang.src.tar.xz"]
        return dict({"tag_name": f"llvmorg-{version}", "assets": [{"name": name} for name in names]}, **flags)

    client.add_json(upstream.LLVM_RELEASES, [llvm_release(f"{llvm_major + 2}.1.0", draft=True),
                                             llvm_release(f"{llvm_major + 1}.1.0", prerelease=True),
                                             {"tag_name": f"llvmorg-{llvm_major + 1}.1.0-rc1", "prerelease": True},
                                             llvm_release(llvm, assets=llvm_asset), llvm_release(pins["LLVM"])])
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

    def test_json_report_when_everything_is_current(self):
        code, stdout, stderr = self.run_main(fake_upstream(self.root), "check", "--json", "--fail-on-security")
        self.assertEqual(0, code, stderr + stdout)
        report = json.loads(stdout)
        self.assertFalse(report["security"])
        self.assertEqual([], report["errors"])
        for item in report["dotnet"]:
            self.assertEqual([], item["pinned-source"]["problems"])
            self.assertEqual(self.pinned["hostlibs-sources"][item["pinned"]]["commit"], item["pinned-source"]["commit"])
        avalonia = report["avalonia"]["dependencies"][self.pinned["avalonia"]]
        self.assertEqual(["Avalonia.Skia"], avalonia["SkiaSharp"]["declared-by"])
        self.assertEqual(["Avalonia.Win32"], avalonia["Avalonia.Angle.Windows.Natives"]["declared-by"])
        self.assertIsNone(report["avalonia"]["patch"])
        pins = tool_pins(self.root)
        self.assertEqual({tool["name"]: pins[tool["name"]] for tool in report["tools"]},
                         {tool["name"]: tool["latest"] for tool in report["tools"]})
        locator = next(tool for tool in report["tools"] if tool["name"] == "Microsoft.Build.Locator")
        self.assertEqual((pins["Microsoft.Build.Locator"], "tools"), (locator["pinned"], locator["group"]))
        self.assertNotIn("Microsoft.Build", [tool["name"] for tool in report["tools"]])

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

    def test_informational_section_errors_do_not_fail(self):
        client = fake_upstream(self.root)
        del client.responses["https://pypi.org/pypi/conan/json"]
        code, stdout, _ = self.run_main(client, "check", "--fail-on-security", "--json")
        self.assertEqual(0, code)
        self.assertEqual(["tools"], [error["section"] for error in json.loads(stdout)["errors"]])

    def test_angle_branch_moved(self):
        code, stdout, _ = self.run_main(fake_upstream(self.root, angle_head="3" * 40), "check", "--json")
        self.assertEqual(0, code)
        self.assertTrue(json.loads(stdout)["angle"]["moved"])


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

    def test_dotnet_update_needs_the_build_containers(self):
        client = fake_upstream(self.root, dotnet=True)
        for url in [url for url in client.responses if upstream.VMR_PIPELINE_RESOURCES in url]:
            del client.responses[url]
        before = self.snapshot()
        code, _, stderr = self.run_main(client, "apply", "--dotnet")
        self.assertEqual(2, code)
        self.assertIn(upstream.VMR_PIPELINE_RESOURCES, stderr)
        self.assertEqual(before, self.snapshot())

    def test_dry_run_writes_nothing(self):
        before = self.snapshot()
        code, stdout, stderr = self.run_main(fake_upstream(self.root, dotnet=True, avalonia=True, skiasharp=True),
                                             "apply", "--dry-run")
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertIn("Planned changes:", stdout)
        self.assertIn("+++ b/Directory.Build.props", stdout)
        self.assertIn("+++ b/" + package_inputs.LOCK, stdout)

    def test_refuses_an_unverified_vmr_commit(self):
        before = self.snapshot()
        code, stdout, stderr = self.run_main(fake_upstream(self.root, dotnet=True, mismatched_vmr=True),
                                             "apply", "--dotnet")
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertIn("not applied", stdout)

    def test_avalonia_patch_update(self):
        props = self.root / package_inputs.PROPS
        props.write_text(re.sub(r"(<AvaloniaNativePackageVersion>\$\(AvaloniaNativeVersion\)\.)\d+",
                                r"\g<1>2", props.read_text(encoding="utf-8")), encoding="utf-8")
        self.relock()
        major = self.props()["_Major"]
        code, stdout, stderr = self.run_main(fake_upstream(self.root, avalonia=True), "apply", "--avalonia")
        self.assertEqual(0, code, stderr)
        new = next_patch(self.pinned["avalonia"])
        values = self.props()
        self.assertEqual((new, new), (values["AvaloniaVersion"], values["AvaloniaNativeVersion"]))
        self.assertEqual(f"{new}.1", values["AvaloniaNativePackageVersion"])
        self.assertEqual(major, values["_Major"], "Avalonia is not an AppHost input")
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
                                       "harfbuzz": next_patch(self.pinned["skiasharp-sources"][line]["harfbuzz"])}
                    for line in self.pinned["skiasharp-versions"]}
        self.assertEqual(expected, pinned["skiasharp-sources"])
        self.assertEqual(next_patch(self.pinned["skiasharp"]), pinned["skiasharp"])
        self.assertEqual(major + 1, int(self.props()["_Major"]))
        self.assert_consistent()

    def test_lists_remaining_mentions_of_old_versions(self):
        runtime = next(iter(self.pinned["runtimes"].values()))
        notes = self.root / "docs/notes.md"
        notes.parent.mkdir()
        notes.write_text(f"Built from {runtime}.\nNot {runtime}0 or 1{runtime}.\n", encoding="utf-8")
        code, stdout, stderr = self.run_main(fake_upstream(self.root, dotnet=True), "apply", "--dotnet")
        self.assertEqual(0, code, stderr)
        self.assertIn("docs/notes.md:1:", stdout)
        self.assertNotIn("docs/notes.md:2:", stdout)

    def test_refuses_to_apply_over_a_failing_guard(self):
        props = self.root / package_inputs.PROPS
        hostlibs = self.root / "native/hostlibs/versions.json"
        sources = json.loads(hostlibs.read_text(encoding="utf-8"))
        next(iter(sources.values()))["commit"] = "0" * 40
        hostlibs.write_text(json.dumps(sources), encoding="utf-8")
        before = props.read_bytes()
        code, _, stderr = self.run_main(fake_upstream(self.root, dotnet=True), "apply", "--dotnet")
        self.assertEqual(2, code)
        self.assertIn("<_Major>", stderr)
        self.assertEqual(before, props.read_bytes())

def evaluated(root):
    return package_inputs.compute(root)


def set_text(path, pattern, replacement):
    text = path.read_text(encoding="utf-8")
    changed, count = re.subn(pattern, replacement, text, flags=re.M)
    if count != 1:
        raise AssertionError(f"{path.name}: {pattern} matched {count} times")
    path.write_text(changed, encoding="utf-8")


class GroupTests(UpstreamTestCase):
    def test_groups_in_workflow_order(self):
        code, stdout, _ = self.run_main(None, "groups")
        self.assertEqual(0, code)
        majors = sorted({int(line.split(".")[0]) for line in self.pinned["skiasharp-versions"]})
        self.assertEqual(["inputs", "avalonia-minor", *[f"skiasharp-{major}" for major in majors], "tools", "llvm"],
                         stdout.split())
        self.assertGreater(len(majors), 1, "one group per pinned SkiaSharp line")

    def test_subset_flags_belong_to_the_inputs_group(self):
        for group in upstream.group_names(self.root)[1:]:
            for flag in ("--dotnet", "--avalonia", "--skiasharp", "--angle"):
                with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as raised:
                    upstream.main(["--root", str(self.root), "apply", "--group", group, flag], client=FakeClient())
                self.assertEqual(2, raised.exception.code, (group, flag))
                self.assertIn("inputs group only", stderr.getvalue())

    def test_unknown_group_is_an_error(self):
        before = self.snapshot()
        code, _, stderr, metadata = self.apply(fake_upstream(self.root), "--group", "skiasharp-99")
        self.assertEqual(2, code)
        self.assertIn("unknown group skiasharp-99", stderr)
        self.assertIsNone(metadata)
        self.assertEqual(before, self.snapshot())


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

    def test_unchanged_depot_tools_keeps_its_commit(self):
        _, depot_tools = self.angle()
        code, stdout, stderr = self.run_main(fake_upstream(self.root, angle_head=self.HEAD), "apply", "--angle")
        self.assertEqual(0, code, stderr)
        self.assertEqual((self.HEAD, depot_tools), self.angle())
        self.assertIn("(unchanged)", stdout)

    def test_depot_tools_revision_in_the_real_deps_format(self):
        deps = deps_text(self.DEPOT_TOOLS)
        self.assertEqual(self.DEPOT_TOOLS, upstream.depot_tools_revision(deps, "test"))
        # Gitiles serves DEPS base64-encoded; line breaks in the encoding are tolerated.
        client = FakeClient()
        encoded = base64.encodebytes(deps.encode())
        client.responses[f"https://chromium.googlesource.com/angle/angle/+/{self.HEAD}/DEPS?format=TEXT"] = encoded
        self.assertEqual(deps, upstream.angle_deps(client, "https://chromium.googlesource.com/angle/angle.git", self.HEAD))
        with self.assertRaisesRegex(upstream.UpstreamError, "found none"):
            upstream.depot_tools_revision(deps.replace("depot_tools.git@", "depot_tools.git' + '@"), "test")
        with self.assertRaisesRegex(upstream.UpstreamError, "expected one depot_tools revision"):
            upstream.depot_tools_revision(deps + deps_text("5" * 40), "test")

    def test_recipe_checks_with_the_same_expression(self):
        recipe = (ROOT / "native/angle/conanfile.py").read_text(encoding="utf-8")
        found = re.findall(r're\.search\(r"([^"]+)", deps\)', recipe)
        self.assertEqual([upstream.DEPOT_TOOLS_REVISION], found)
        self.assertEqual(self.DEPOT_TOOLS, re.search(found[0], deps_text(self.DEPOT_TOOLS)).group(1))

    def test_ambiguous_deps_is_a_lookup_error(self):
        client = fake_upstream(self.root, angle_head=self.HEAD, dotnet=True)
        url, _ = angle_source(self.root)
        client.responses[f"{url[:-4]}/+/{self.HEAD}/DEPS?format=TEXT"] = \
            base64.b64encode((deps_text("5" * 40) + deps_text("6" * 40)).encode())
        before = self.snapshot()
        code, _, stderr, metadata = self.apply(client)
        self.assertEqual(2, code)
        self.assertIn("expected one depot_tools revision", stderr)
        self.assertIsNone(metadata)
        self.assertEqual(before, self.snapshot())
        pinned = self.angle()
        code, _, stderr, metadata = self.apply(client, "--keep-going")
        self.assertEqual(0, code, stderr)
        self.assertEqual(pinned, self.angle())
        self.assertEqual(1, len(metadata["errors"]))
        self.assertRegex(metadata["errors"][0], r"^ANGLE: .*expected one depot_tools revision")
        self.assertIn("### Lookup errors", metadata["body"])
        self.assertTrue(any(path == "native/hostlibs/versions.json" for path in metadata["changed"]), ".NET applied")

    def test_deps_patch_that_no_longer_applies_is_noted(self):
        _, depot_tools = angle_source(self.root)
        patch = self.root / "native/angle/patches/angle-deps.patch"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(DEPS_PATCH.replace("DEPOT_TOOLS", depot_tools), encoding="utf-8")
        self.assertEqual([], upstream.deps_patch_problems(patch.read_text(encoding="utf-8"), deps_text(depot_tools)))
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, angle_head=self.HEAD), "--angle", "--dry-run")
        self.assertEqual(0, code, stderr)
        self.assertEqual([], metadata["notes"])
        # A new depot_tools revision changes a context line of the patch.
        client = fake_upstream(self.root, angle_head=self.HEAD, angle_depot_tools=self.DEPOT_TOOLS)
        code, _, stderr, metadata = self.apply(client, "--angle", "--dry-run")
        self.assertEqual(0, code, stderr)
        self.assertEqual(1, len(metadata["notes"]))
        self.assertIn("native/angle/patches/angle-deps.patch no longer applies to DEPS", metadata["notes"][0])
        self.assertIn("@@ -11,10 +11,5 @@", metadata["notes"][0])
        self.assertIn("### Notes", metadata["body"])

    def test_deps_patch_hunks(self):
        deps = deps_text("1" * 40)
        patch = DEPS_PATCH.replace("DEPOT_TOOLS", "1" * 40)
        self.assertEqual([], upstream.deps_patch_problems(patch, deps))
        self.assertEqual(["@@ -11,10 +11,5 @@ deps = {"],
                         upstream.deps_patch_problems(patch, deps.replace("dawn_revision", "dawn_commit")))
        # Hunks of other files are not compared with DEPS.
        other = patch.replace("a/DEPS", "a/BUILD.gn").replace("b/DEPS", "b/BUILD.gn")
        self.assertEqual([], upstream.deps_patch_problems(other, "unrelated"))


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

    def test_angle_commit(self):
        before = evaluated(self.root)
        major = int(self.props()["_Major"])
        set_text(self.root / "native/angle/conandata.yml", r'^(sources:\n(?:  .*\n)*?  commit: *")[0-9a-f]*(")',
                 r"\g<1>" + "1" * 40 + r"\g<2>")
        changed, updates = self.bump()
        self.assert_bumped(changed, {("ChsBuffer.Angle.Static", "default"):
                                     before["ChsBuffer.Angle.Static"]["default"]["version"]}, apphost=True)
        self.assertEqual(major + 1, int(self.props()["_Major"]), "<_Major> once for all AppHost variants")
        self.assertEqual(["ChsBuffer.Avalonia.AppHost", "ChsBuffer.Angle.Static"],
                         [update.description.split(":")[0] for update in updates])

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

    def test_avalonia_source_archive(self):
        before = evaluated(self.root)
        set_text(self.root / "native/avalonianative/conandata.yml",
                 rf'^(  "{re.escape(self.pinned["avalonia-native"])}":\n(?:    .*\n)*?    sha256: ")[0-9a-f]+(")',
                 r"\g<1>" + "e" * 64 + r"\g<2>")
        changed, _ = self.bump()
        self.assert_bumped(changed, {("ChsBuffer.Avalonia.Native.Static", "default"):
                                     before["ChsBuffer.Avalonia.Native.Static"]["default"]["version"]}, apphost=False)

    def test_vmr_commit(self):
        before = evaluated(self.root)
        hostlibs = self.root / "native/hostlibs/versions.json"
        sources = json.loads(hostlibs.read_text(encoding="utf-8"))
        runtime = next(iter(sources))
        sources[runtime]["commit"] = "0" * 40
        hostlibs.write_text(json.dumps(sources), encoding="utf-8")
        changed, _ = self.bump()
        # One <Version> serves every target framework, so all of them move to the next prerelease number.
        expected = {("ChsBuffer.AppHost.Static", framework): record["version"]
                    for framework, record in before["ChsBuffer.AppHost.Static"].items()}
        self.assert_bumped(changed, expected, apphost=True)

    def test_nothing_to_bump(self):
        self.assertEqual(({}, []), self.bump())


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

    def test_dotnet_failure_aborts_even_with_keep_going(self):
        client = fake_upstream(self.root, avalonia=True)
        channel = ".".join(next(iter(self.pinned["runtimes"].values())).split(".")[:2])
        del client.responses[f"https://example.invalid/release-metadata/{channel}/releases.json"]
        before = self.snapshot()
        for arguments in (("--keep-going",), ("--keep-going", "--dotnet")):
            code, _, stderr, metadata = self.apply(client, *arguments)
            self.assertEqual(2, code, arguments)
            self.assertIsNone(metadata)
            self.assertEqual(before, self.snapshot())

    def test_security_state_failure_aborts(self):
        client = fake_upstream(self.root, avalonia=True)
        del client.responses["https://example.invalid/update.json"]
        before = self.snapshot()
        code, _, stderr, metadata = self.apply(client, "--keep-going", "--avalonia")
        self.assertEqual(2, code)
        self.assertIn("cannot evaluate the security state", stderr)
        self.assertIsNone(metadata)
        self.assertEqual(before, self.snapshot())
        # Without --metadata the security state is not needed.
        code, _, stderr = self.run_main(client, "apply", "--keep-going", "--avalonia")
        self.assertEqual(0, code, stderr)
        self.assertEqual(next_patch(self.pinned["avalonia"]), self.props()["AvaloniaVersion"])


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

    def test_target_is_deterministic(self):
        client = lambda: fake_upstream(self.root, dotnet=True, avalonia=True, skiasharp=True,  # noqa: E731
                                       angle_head="3" * 40)
        first = self.apply(client(), "--dry-run")[3]
        second = self.apply(client(), "--dry-run")[3]
        self.assertEqual(first, second)
        applied = self.apply(client())[3]
        self.assertEqual(first["target"], applied["target"], "--dry-run and apply describe the same target")
        self.assertEqual(first["title"], applied["title"])
        self.assertEqual(first["changed"], applied["changed"])
        for name in (".NET", "Avalonia", "SkiaSharp", "ANGLE"):
            self.assertIn(name, first["target"])
        self.assertNotIn("sha256", first["target"])
        self.assertLessEqual(len(first["title"]), upstream.TITLE_LENGTH)
        self.assertEqual("fix(deps): update .NET, Avalonia, SkiaSharp and ANGLE (security)", first["title"])

    def test_nothing_to_change(self):
        before = self.snapshot()
        code, stdout, stderr, metadata = self.apply(fake_upstream(self.root))
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertEqual({"group": "inputs", "changed": [], "target": "", "updates": [], "notes": [], "errors": [],
                          "security": {"before": False, "after": False, "cves": []}},
                         {key: value for key, value in metadata.items() if key not in ("title", "body")})
        self.assertEqual("build(deps): no updates for inputs", metadata["title"])
        self.assertIn("Nothing to change.", metadata["body"])
        self.assertIn("(none: nothing to update)", stdout)

    def test_resolved_advisory_is_a_security_fix(self):
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, avalonia=True, vulnerable=True), "--avalonia")
        self.assertEqual(0, code, stderr)
        self.assertEqual({"before": True, "after": False, "cves": []}, metadata["security"])
        self.assertEqual(f"fix(deps): update Avalonia to {next_patch(self.pinned['avalonia'])} (security)",
                         metadata["title"])
        self.assertIn("Resolves the critical advisory https://example.invalid/advisory", metadata["body"])

    def test_unresolved_finding_is_not_a_fix(self):
        client = fake_upstream(self.root, dotnet=True, mismatched_vmr=True, avalonia=True)
        code, _, stderr, metadata = self.apply(client)
        self.assertEqual(0, code, stderr)
        self.assertEqual({"before": True, "after": True, "cves": []}, metadata["security"])
        self.assertTrue(metadata["title"].startswith("build(deps): update Avalonia"), metadata["title"])
        self.assertTrue(any("not applied" in note for note in metadata["notes"]))
        self.assertIn("### Not applied", metadata["body"])

    def test_eol_channel_stays_a_finding(self):
        channel = ".".join(next(iter(self.pinned["runtimes"].values())).split(".")[:2])
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, eol=(channel,), avalonia=True))
        self.assertEqual(0, code, stderr)
        self.assertEqual({"before": True, "after": True, "cves": []}, metadata["security"])
        self.assertIn(f".NET {channel}", metadata["body"])
        self.assertTrue(metadata["title"].startswith("build(deps):"))

    def test_other_groups_have_no_security_state(self):
        client = fake_upstream(self.root)
        del client.responses[upstream.NUGET_VULNERABILITIES]
        for group in upstream.group_names(self.root)[1:]:
            code, _, stderr, metadata = self.apply(client, "--group", group)
            self.assertEqual(0, code, stderr)
            self.assertEqual((group, None, [], ""), (metadata["group"], metadata["security"], metadata["changed"],
                                                     metadata["target"]))

    def test_old_versions_still_mentioned(self):
        runtime = next(iter(self.pinned["runtimes"].values()))
        notes = self.root / "docs/notes.md"
        notes.parent.mkdir()
        notes.write_text(f"<!-- Built from {runtime}. -->\n", encoding="utf-8")
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, dotnet=True), "--dotnet")
        self.assertEqual(0, code, stderr)
        self.assertIn("### Old versions still mentioned", metadata["body"])
        self.assertIn("`docs/notes.md:1`", metadata["body"])
        self.assertNotIn("<!--", metadata["body"])


class AvaloniaMinorTests(UpstreamTestCase):
    def test_newest_minor_with_the_same_dependencies(self):
        props = self.root / package_inputs.PROPS
        set_text(props, r"(<AvaloniaNativePackageVersion>\$\(AvaloniaNativeVersion\)\.)\d+", r"\g<1>3")
        self.relock()
        major = self.props()["_Major"]
        client = fake_upstream(self.root, avalonia=True, avalonia_minor=True, avalonia_major=True)
        code, stdout, stderr, metadata = self.apply(client, "--group", "avalonia-minor")
        self.assertEqual(0, code, stderr)
        new = avalonia_minor_version(self.pinned["avalonia"])
        values = self.props()
        self.assertEqual((new, new, f"{new}.1"), (values["AvaloniaVersion"], values["AvaloniaNativeVersion"],
                                                  values["AvaloniaNativePackageVersion"]))
        self.assertEqual(major, values["_Major"])
        commit = fake_commit("avalonia " + new)
        self.assertEqual({"url": upstream.AVALONIA_ARCHIVE.format(commit=commit), "commit": commit,
                          "sha256": hashlib.sha256(b"archive " + new.encode()).hexdigest()},
                         repo_versions.load(self.root)["avalonia-sources"][new])
        self.assertEqual(f"build(deps): update Avalonia to {new}", metadata["title"])
        self.assertIsNone(metadata["security"])
        newer_major = f"{int(self.pinned['avalonia'].split('.')[0]) + 1}.0.0"
        self.assertEqual([f"Avalonia newer major releases (report only): {newer_major}"], metadata["notes"])
        self.assert_consistent()

    def test_dependency_change_is_report_only(self):
        before = self.snapshot()
        client = fake_upstream(self.root, avalonia_minor=True, avalonia_minor_skia=next_patch(self.pinned["skiasharp"]))
        code, _, stderr, metadata = self.apply(client, "--group", "avalonia-minor")
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], metadata["changed"])
        self.assertEqual(1, len(metadata["notes"]))
        self.assertRegex(metadata["notes"][0], r"not applied \(report only\): SkiaSharp: ")

    def test_tag_that_is_not_the_built_commit_is_report_only(self):
        client = fake_upstream(self.root, avalonia_minor=True)
        new = avalonia_minor_version(self.pinned["avalonia"])
        client.add_ref(upstream.AVALONIA_REPOSITORY, f"refs/tags/{new}", "9" * 40)
        before = self.snapshot()
        code, _, stderr, metadata = self.apply(client, "--group", "avalonia-minor")
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertIn("was built from", metadata["notes"][0])

    def test_patch_releases_belong_to_the_inputs_group(self):
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, avalonia=True), "--group", "avalonia-minor")
        self.assertEqual(0, code, stderr)
        self.assertEqual([], metadata["changed"])


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
                          "harfbuzz": next_patch(next_patch(self.pinned["skiasharp-sources"][line]["harfbuzz"]))},
                         sources[new])
        self.assertEqual(self.pinned["skiasharp-sources"][other], sources[other])
        self.assertNotIn(line, sources)
        self.assertEqual(f"build(deps): update SkiaSharp to {new}", metadata["title"])
        self.assertIn("ChsBuffer.SkiaSharp.Static (skia", metadata["body"])
        self.assert_consistent()

    def test_package_docs_follow_the_new_versions(self):
        line = self.pinned["skiasharp-versions"][0]
        harfbuzz = self.pinned["skiasharp-sources"][line]["harfbuzz"]
        readme = self.root / "src/package-skiasharp-static/README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(f"Use {line} ({line}.1, HarfBuzzSharp {harfbuzz}); not {line}.9 or 1{line}.\n",
                          encoding="utf-8")
        client = fake_upstream(self.root, skiasharp=True, skiasharp_milestone=True)
        code, _, stderr, metadata = self.apply(client, "--group", f"skiasharp-{line.split('.')[0]}")
        self.assertEqual(0, code, stderr)
        new = skiasharp_milestone_version(line)
        new_harfbuzz = repo_versions.load(self.root)["skiasharp-sources"][new]["harfbuzz"]
        self.assertEqual(f"Use {new} ({new}.1, HarfBuzzSharp {new_harfbuzz}); not {line}.9 or 1{line}.\n",
                         readme.read_text(encoding="utf-8"))
        self.assertIn("src/package-skiasharp-static/README.md", metadata["changed"])

    def build_record(self):
        return json.loads((self.root / upstream.SKIASHARP_BUILD).read_text(encoding="utf-8"))

    def test_unchanged_build_files_carry_over(self):
        line = self.pinned["skiasharp-versions"][0]
        files = self.build_record()[line]
        client = fake_upstream(self.root, skiasharp=True, skiasharp_milestone=True)
        code, _, stderr, metadata = self.apply(client, "--group", f"skiasharp-{line.split('.')[0]}")
        self.assertEqual(0, code, stderr)
        record = self.build_record()
        self.assertNotIn(line, record)
        self.assertEqual(files, record[skiasharp_milestone_version(line)])
        self.assertIn(upstream.SKIASHARP_BUILD, metadata["changed"])
        self.assertNotIn("Upstream build changes", metadata["body"])

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

    def test_newer_major_is_report_only(self):
        majors = sorted({int(line.split(".")[0]) for line in self.pinned["skiasharp-versions"]})
        before = self.snapshot()
        client = fake_upstream(self.root, skiasharp_major=True)
        code, _, stderr, metadata = self.apply(client, "--group", f"skiasharp-{majors[-1]}")
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([f"SkiaSharp newer major releases (report only): {majors[-1] + 1}.0.0"], metadata["notes"])
        # Reported once, by the group of the closest pinned line below it.
        self.assertEqual([], self.apply(client, "--group", f"skiasharp-{majors[0]}")[3]["notes"])

    def test_patch_releases_belong_to_the_inputs_group(self):
        group = f"skiasharp-{self.pinned['skiasharp-versions'][0].split('.')[0]}"
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, skiasharp=True), "--group", group)
        self.assertEqual(0, code, stderr)
        self.assertEqual([], metadata["changed"])


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

    def test_failed_lookup_skips_only_that_tool(self):
        client = fake_upstream(self.root, tools=True)
        del client.responses[f"{upstream.PYPI}/conan/json"]
        pins = tool_pins(self.root)
        before = self.snapshot()
        code, _, stderr, metadata = self.apply(client, "--group", "tools")
        self.assertEqual(2, code)
        self.assertIn("conan: GET", stderr)
        self.assertEqual(before, self.snapshot())
        code, _, stderr, metadata = self.apply(client, "--group", "tools", "--keep-going")
        self.assertEqual(0, code, stderr)
        after = tool_pins(self.root)
        self.assertEqual(pins["conan"], after["conan"])
        self.assertEqual(next_patch(pins["ninja"]), after["ninja"])
        self.assertEqual(next_patch(pins["Task"]), after["Task"])
        self.assertEqual(1, len(metadata["errors"]))
        self.assertTrue(metadata["errors"][0].startswith("conan: GET "))

    def test_current_tools_change_nothing(self):
        code, _, stderr, metadata = self.apply(fake_upstream(self.root), "--group", "tools")
        self.assertEqual(0, code, stderr)
        self.assertEqual(([], ""), (metadata["changed"], metadata["target"]))

    def test_build_args_are_the_pins_of_the_image_tools(self):
        pins = tool_pins(self.root)
        expected = f"--build-arg TASK_VERSION={pins['Task']} --build-arg UV_VERSION={pins['uv']}\n"
        self.assertEqual((0, expected, ""), self.run_main(None, "build-args"))
        code, _, _, _ = self.apply(fake_upstream(self.root, tools=True), "--group", "tools")
        self.assertEqual(0, code)
        self.assertIn(f"TASK_VERSION={next_patch(pins['Task'])} ", self.run_main(None, "build-args")[1])


class ImageToolTests(unittest.TestCase):
    """The Linux build images install Task and uv at the versions of the build arguments, never at a literal one."""

    def test_dockerfiles_take_the_tool_versions_as_build_arguments(self):
        build_args = {tool.build_arg for tool in upstream.TOOLS if tool.build_arg}
        dockerfiles = sorted(ROOT.glob("native/*/*.Dockerfile"))
        self.assertTrue(dockerfiles)
        for dockerfile in dockerfiles:
            with self.subTest(dockerfile=dockerfile.relative_to(ROOT).as_posix()):
                text = dockerfile.read_text(encoding="utf-8")
                self.assertEqual([], re.findall(r"go-task/task/releases/download/v\d[^/]*|astral-sh/uv:\d\S*", text))
                declared = set(re.findall(r"^ARG (\w+_VERSION)\b", text, re.M))
                self.assertEqual(build_args, declared)
                self.assertIn("releases/download/v$TASK_VERSION/", text)
                self.assertIn("FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv", text)


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

    def test_release_without_the_windows_installer_is_report_only(self):
        before, version = self.snapshot(), next_patch(self.pins()[0])
        code, _, stderr, metadata = self.apply(fake_upstream(self.root, llvm=version, llvm_asset=False), "--group", "llvm")
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertEqual([f"LLVM {version} not applied: the release has no LLVM-{version}-win64.msi (yet)"],
                         metadata["notes"])

    def test_pins_that_disagree_are_an_error(self):
        action = self.root / upstream.LLVM_ACTION
        set_text(action, r"(key: llvm-)[\w.]+(-win64)", r"\g<1>1.2.3\g<2>")
        code, _, stderr, metadata = self.apply(fake_upstream(self.root), "--group", "llvm")
        self.assertEqual(2, code)
        self.assertIn("pin different LLVM versions", stderr)


class CheckWordingTests(UpstreamTestCase):
    def test_reports_the_group_of_each_update(self):
        client = fake_upstream(self.root, dotnet=True, avalonia_minor=True, avalonia_major=True, skiasharp_milestone=True,
                               skiasharp_major=True, angle_head="3" * 40, tools=True)
        code, stdout, _ = self.run_main(client, "check")
        self.assertEqual(0, code, stdout)
        self.assertIn(f"newer minor {avalonia_minor_version(self.pinned['avalonia'])}: apply group avalonia-minor", stdout)
        self.assertIn("newer major releases (report only)", stdout)
        for line in self.pinned["skiasharp-versions"]:
            self.assertIn(f"newer milestone {skiasharp_milestone_version(line)} (apply group skiasharp-"
                          f"{line.split('.')[0]})", stdout)
        self.assertIn("apply group inputs moves the pin to the branch head", stdout)
        self.assertIn("Microsoft.Build.Locator", stdout)
        self.assertNotIn("Toolchain (report only)", stdout)



class Response(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers


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

    def test_client_decompresses_and_scopes_the_token(self):
        client = upstream.Client(token="secret", attempts=1)
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value = Response(gzip.compress(b'{"value": 1}'), {"Content-Encoding": "gzip"})
            self.assertEqual({"value": 1}, client.json("https://api.nuget.org/v3/index.json"))
            request = urlopen.call_args[0][0]
            self.assertIsNone(request.get_header("Authorization"))
            self.assertEqual(upstream.USER_AGENT, request.get_header("User-agent"))
            urlopen.return_value = Response(b'{"value": 2}', {})
            self.assertEqual({"value": 2}, client.json("https://api.github.com/repos/o/r"))
            self.assertEqual("Bearer secret", urlopen.call_args[0][0].get_header("Authorization"))

    def test_client_reports_http_errors(self):
        client = upstream.Client(attempts=2)
        error = urllib.error.HTTPError("https://example.invalid/x", 503, "Service Unavailable", {}, io.BytesIO())
        with mock.patch("urllib.request.urlopen", side_effect=error) as urlopen, mock.patch("time.sleep") as sleep:
            with self.assertRaisesRegex(upstream.UpstreamError, r"https://example.invalid/x: HTTP 503"):
                client.get("https://example.invalid/x")
        self.assertEqual(2, urlopen.call_count)
        self.assertEqual(1, sleep.call_count)


class FailureTests(UpstreamTestCase):
    """Failures keep their exit codes: 1 only for findings, 2 when a command cannot complete."""

    def test_security_verdict_survives_a_missing_ref_nuspec(self):
        client = fake_upstream(self.root, dotnet=True)
        for runtime in self.pinned["runtimes"].values():
            new = next_patch(runtime)
            del client.responses[f"{upstream.NUGET}/microsoft.netcore.app.ref/{new}/microsoft.netcore.app.ref.nuspec"]
        code, stdout, stderr = self.run_main(client, "check", "--json", "--fail-on-security")
        self.assertEqual(1, code, stderr)
        report = json.loads(stdout)
        self.assertTrue(report["security"])
        self.assertEqual([], report["errors"])
        for item in report["dotnet"]:
            self.assertTrue(item["security"])
            self.assertTrue(any("HTTP 404" in problem for problem in item["latest-source"]["problems"]))
        code, stdout, _ = self.run_main(client, "check")
        self.assertEqual(0, code)
        self.assertIn("Security: ACTION NEEDED", stdout)

    def test_end_of_life_channel_is_a_security_finding(self):
        channel = ".".join(next(iter(self.pinned["runtimes"].values())).split(".")[:2])
        code, stdout, _ = self.run_main(fake_upstream(self.root, eol=(channel,)), "check", "--fail-on-security")
        self.assertEqual(1, code)
        self.assertIn(f".NET {channel} is end of life", stdout)
        self.assertIn("Security: ACTION NEEDED", stdout)
        self.assertEqual(0, self.run_main(fake_upstream(self.root, eol=(channel,)), "check")[0])

    def test_malformed_security_data_is_an_error_not_a_finding(self):
        client = fake_upstream(self.root)
        client.add_json(upstream.DOTNET_INDEX, {"releases-index": ["not an object"]})
        code, stdout, _ = self.run_main(client, "check", "--fail-on-security")
        self.assertEqual(2, code)
        self.assertIn("AttributeError", stdout)
        self.assertIn("Security: UNKNOWN", stdout)

    def test_unexpected_exceptions_exit_2(self):
        with mock.patch.object(package_inputs, "compute", side_effect=KeyError("boom")):
            code, _, stderr = self.run_main(None, "lock", "--check")
        self.assertEqual(2, code)
        self.assertIn("error: unexpected KeyError", stderr)

    def test_contents_listing_instead_of_a_submodule_is_reported(self):
        client = fake_upstream(self.root, skiasharp=True)
        for line in self.pinned["skiasharp-versions"]:
            client.add_json(f"{upstream.GITHUB_API}/repos/mono/SkiaSharp/contents/externals/skia?ref=v{next_patch(line)}",
                            [{"type": "file", "name": "BUILD.gn"}])
        before = self.snapshot()
        code, stdout, stderr = self.run_main(client, "apply", "--skiasharp")
        self.assertEqual(0, code, stderr)
        self.assertEqual(before, self.snapshot())
        self.assertIn("is not a submodule", stdout)

    def test_failed_lookup_aborts_and_suggests_a_narrower_selection(self):
        client = fake_upstream(self.root, dotnet=True, skiasharp=True)
        line = self.pinned["skiasharp-versions"][0]
        del client.responses[f"{upstream.GITHUB_API}/repos/mono/SkiaSharp/contents/externals/skia?ref=v{next_patch(line)}"]
        before = self.snapshot()
        code, _, stderr = self.run_main(client, "apply")
        self.assertEqual(2, code)
        self.assertEqual(before, self.snapshot())
        self.assertIn("Nothing was written", stderr)
        self.assertIn("select them: --dotnet --avalonia", stderr)

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

    def test_apply_appends_to_the_lock(self):
        before = package_inputs.read_lock(self.root)
        code, _, stderr = self.run_main(fake_upstream(self.root, dotnet=True), "apply", "--dotnet")
        self.assertEqual(0, code, stderr)
        after = package_inputs.read_lock(self.root)
        self.assertEqual([], package_inputs.changed_entries(before, after))
        self.assertGreater(sum(map(len, package_inputs.history(after).values())),
                           sum(map(len, package_inputs.history(before).values())))


class BuildContainerTests(unittest.TestCase):
    """The official Linux build containers of a VMR commit, as the HostLibs Linux builds use them."""

    def test_pins_every_container_by_digest(self):
        client = FakeClient()
        add_pipeline_resources(client, "c1")
        self.assertEqual(CONTAINERS, upstream.build_containers(client, "c1"))
        for rid in MANIFESTS:
            self.assertEqual(upstream.REGISTRY_MANIFESTS, client.accepts[manifest_url(rid)])
        self.assertNotIn(manifest_url("linux-musl-x64"), client.requests, "a digest in the pipeline is used as is")

    def test_missing_container_is_an_error(self):
        for leg in upstream.BUILD_CONTAINERS.values():
            with self.subTest(leg=leg):
                client = FakeClient()
                add_pipeline_resources(client, "c1", PIPELINE_RESOURCES.replace(f"      {leg}:\n", f"      {leg}_old:\n"))
                with self.assertRaisesRegex(upstream.UpstreamError, rf"\b{leg}\b"):
                    upstream.build_containers(client, "c1")

    def test_other_registries_are_an_error(self):
        client = FakeClient()
        add_pipeline_resources(client, "c1", PIPELINE_RESOURCES.replace(f"{PREREQS}:{CONTAINER_TAGS['linux-x64']}\n",
                                                                        f"example.invalid/prereqs:{CONTAINER_TAGS['linux-x64']}\n"))
        with self.assertRaisesRegex(upstream.UpstreamError, r"\blinux_x64\b"):
            upstream.build_containers(client, "c1")


class VmrSourceTests(unittest.TestCase):
    """The VMR tag of a runtime: the 1xx SDK tag whose commit built Microsoft.NETCore.App.Ref."""

    @staticmethod
    def client(tags, ref_commit="c100"):
        client = FakeClient()
        for tag, commit in tags.items():
            client.add_ref(upstream.VMR, f"refs/tags/{tag}", commit)
        if ref_commit:
            client.add_nuspec("Microsoft.NETCore.App.Ref", "9.0.0", commit=ref_commit)
        return client

    @staticmethod
    def release(*sdks):
        return {"runtime": {"version": "9.0.0"}, "sdk": {"version": sdks[0]}, "sdks": [{"version": sdk} for sdk in sdks]}

    def test_picks_the_1xx_sdk_built_from_the_ref_commit(self):
        # As released: 9.0.0 lists 9.0.101 before 9.0.100.
        client = self.client({"v9.0.101": "c101", "v9.0.100": "c100"})
        result = upstream.vmr_source(client, "9.0.0", self.release("9.0.101", "9.0.100"), "9.0")
        self.assertEqual(("v9.0.100", "c100", []), (result["tag"], result["commit"], result["problems"]))

    def test_no_1xx_sdk_matches(self):
        client = self.client({"v9.0.101": "c101", "v9.0.100": "c100"}, ref_commit="other")
        result = upstream.vmr_source(client, "9.0.0", self.release("9.0.101", "9.0.100"), "9.0")
        self.assertIsNone(result["tag"])
        self.assertEqual(1, len(result["problems"]))
        self.assertIn("none of the 1xx SDK tags (v9.0.101 c101, v9.0.100 c100)", result["problems"][0])

    def test_no_1xx_sdk(self):
        result = upstream.vmr_source(self.client({}), "9.0.0", self.release("9.0.200"), "9.0")
        self.assertIsNone(result["tag"])
        self.assertEqual(["no 1xx SDK listed for runtime 9.0.0"], result["problems"])

    def test_single_1xx_sdk_with_another_commit(self):
        result = upstream.vmr_source(self.client({"v9.0.100": "c999"}), "9.0.0", self.release("9.0.100"), "9.0")
        self.assertEqual(("v9.0.100", "c999"), (result["tag"], result["commit"]))
        self.assertIn("was built from c100", result["problems"][0])

    def test_unpublished_ref_nuspec_is_a_problem_not_an_exception(self):
        client = self.client({"v9.0.100": "c100"}, ref_commit=None)
        result = upstream.vmr_source(client, "9.0.0", self.release("9.0.100"), "9.0")
        self.assertEqual(("v9.0.100", "c100"), (result["tag"], result["commit"]))
        self.assertTrue(any("HTTP 404" in problem for problem in result["problems"]))


class FailingRead(Response):
    def __init__(self, error):
        super().__init__(b"", {})
        self.error = error

    def read(self, *args):
        raise self.error


class ClientTests(unittest.TestCase):
    def test_retries_failures_while_reading(self):
        client = upstream.Client(attempts=2)
        responses = [FailingRead(TimeoutError("The read operation timed out")), Response(b'{"ok": true}', {})]
        with mock.patch("urllib.request.urlopen", side_effect=responses) as urlopen, mock.patch("time.sleep"):
            self.assertEqual({"ok": True}, client.json("https://example.invalid/x"))
        self.assertEqual(2, urlopen.call_count)

    def test_reports_failures_while_reading(self):
        client = upstream.Client(attempts=2)
        for error in (TimeoutError("The read operation timed out"), http.client.IncompleteRead(b"par", 10),
                      ConnectionResetError(104, "Connection reset by peer")):
            with mock.patch("urllib.request.urlopen", side_effect=lambda *args, error=error, **kwargs: FailingRead(error)), \
                    mock.patch("time.sleep"):
                with self.assertRaisesRegex(upstream.UpstreamError, rf"{type(error).__name__}.*after 2 attempts"):
                    client.get("https://example.invalid/x")

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
