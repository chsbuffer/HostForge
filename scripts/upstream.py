"""Track the upstream releases HostForge packages are built from, and keep the package lock current.

  lock    [--check [--base GIT_REF]] [--force]
          Append the package versions computed from the repository to package-inputs.lock.json (offline), an
          append-only history. Refuses (exit 1) when variants of a family share a version, when a locked
          version would get other inputs, when a version moves backwards, or when an entry of the lock committed
          in HEAD was changed. --force replaces or removes only entries that are not in the lock of HEAD.
          --check changes nothing and also requires the entries of the lock at GIT_REF (default HEAD) unchanged;
          a GIT_REF without a lock skips that comparison.
  check   [--json] [--fail-on-security]
          Read-only report: newer .NET runtimes (with CVEs and their VMR tag/commit), end-of-life .NET channels,
          Avalonia patch, minor and major releases with the SkiaSharp/HarfBuzzSharp/ANGLE versions they declare,
          SkiaSharp/HarfBuzzSharp lines, the ANGLE branch head, NuGet advisories for pinned packages and toolchain
          releases, each with the apply group that handles it. Writes a Markdown summary to $GITHUB_STEP_SUMMARY
          when set. Exit code 1 (with --fail-on-security only): a newer .NET security release, an end-of-life
          .NET channel or a vulnerable pinned package; 2: the .NET or advisory check could not complete.
  apply   [--group NAME] [--dotnet] [--avalonia] [--skiasharp] [--angle] [--keep-going] [--metadata FILE]
          [--dry-run]
          Update the files of one group, computed from the repository as it is (one pull request per group):
            inputs          (default) .NET runtime patch releases with the official Linux build
                            containers of their VMR commit (a preview pin is report only), the Avalonia patch
                            release of the pinned minor, SkiaSharp patch releases of the pinned milestones and the
                            head of the pinned ANGLE branch with the depot_tools revision of its DEPS. --dotnet,
                            --avalonia, --skiasharp and --angle select a subset.
            avalonia-minor  the newest release of the newest Avalonia minor of the pinned major, when it declares
                            the SkiaSharp, HarfBuzzSharp and ANGLE versions of the pinned Avalonia; otherwise
                            report only, like newer majors.
            skiasharp-<N>   one per pinned SkiaSharp line: the newest release of a newer milestone of N.x; newer
                            majors are report only.
            tools           Task, uv, conan and ninja (the == pins in pyproject.toml), TUnit and
                            Microsoft.Build.Locator. Microsoft.Build is not tracked: it is referenced without its
                            runtime assets, which come from the SDK, so it follows the SDK.
            llvm            the LLVM release installed by .github/actions/install-llvm; this changes the compiled
                            binaries but no package version.
          Groups that change package inputs give every package family whose locked version would get other
          inputs a new version (<_Major> + 1 once, otherwise its revision or prerelease number + 1) and append
          to the lock. Writes every file or none, and lists tracked files that still mention the old versions.
          --keep-going records a failed lookup other than .NET and skips that item. --metadata FILE writes a
          JSON summary for the pull request (group, changed files, target, title, body, updates, notes, errors
          and, for inputs, the security verdict before and after), also when nothing changed, never on exit
          code 2; with --dry-run it describes the files that would change. Exit code 2: the group could not
          be computed (also when a .NET lookup or the security verdict fails); nothing was written.
  groups  Print the apply groups, one per line.
  build-args
          Print the docker build arguments of the Linux build images: the pinned versions of the tools they
          install (TASK_VERSION, UV_VERSION). The Dockerfiles take them as arguments, so a tool update
          changes no Dockerfile and no native cache key.

Taskfile: task upstream-lock|upstream-check|upstream-apply -- <arguments>. GITHUB_TOKEN, when set, is sent to
api.github.com only.
"""

import argparse
import base64
import contextlib
import copy
import difflib
import gzip
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_inputs  # noqa: E402
from package_inputs import is_stable, version_key  # noqa: E402

repo_versions = package_inputs.repo_versions
ROOT = package_inputs.ROOT

USER_AGENT = "HostForge-upstream-check"
TIMEOUT = 60
DOTNET_INDEX = "https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json"
VMR = "https://github.com/dotnet/dotnet"
MCR = "https://mcr.microsoft.com"
# The runtime's pipeline resources name the container of each official build leg; HostLibs builds each Linux RID
# in the container of its leg.
VMR_PIPELINE_RESOURCES = "src/runtime/eng/pipelines/common/templates/pipeline-with-resources.yml"
BUILD_CONTAINERS = {"linux-x64": "linux_x64", "linux-musl-x64": "linux_musl_x64", "linux-musl-arm64": "linux_musl_arm64"}
# The media types docker pull accepts, so that a tag resolves to the digest docker would pull.
REGISTRY_MANIFESTS = ("application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, "
                      "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json")
NUGET = "https://api.nuget.org/v3-flatcontainer"
NUGET_VULNERABILITIES = "https://api.nuget.org/v3/vulnerabilities/index.json"
GITHUB_API = "https://api.github.com"
PYPI = "https://pypi.org/pypi"
AVALONIA_REPOSITORY = "https://github.com/AvaloniaUI/Avalonia"
AVALONIA_ARCHIVE = "https://codeload.github.com/AvaloniaUI/Avalonia/tar.gz/{commit}"
SKIASHARP_REPOSITORY = "https://github.com/mono/SkiaSharp"
LLVM_RELEASES = f"{GITHUB_API}/repos/llvm/llvm-project/releases?per_page=30"
# Avalonia packages declare these; the AppHost and static packages depend on the same three.
TRACKED_DEPENDENCIES = ("SkiaSharp", "HarfBuzzSharp", "Avalonia.Angle.Windows.Natives")
SEVERITY = {0: "low", 1: "moderate", 2: "high", 3: "critical"}
APPHOST = "ChsBuffer.Avalonia.AppHost"

ANGLE_DATA = "native/angle/conandata.yml"
# The mono/SkiaSharp build files (git blob SHAs) that the native/skiasharp recipes were checked against, per version.
SKIASHARP_BUILD = "native/skiasharp/upstream-build.json"
BUILD_DIFF_LINES = 200
# The depot_tools revision in ANGLE's DEPS. native/angle/conanfile.py checks conandata.yml with the same
# expression (the recipe runs under conan and cannot import this script; tests/native keeps the two equal).
DEPOT_TOOLS_REVISION = r"/chromium/tools/depot_tools\.git@([0-9a-f]{40})"
LLVM_ACTION = ".github/actions/install-llvm/action.yml"
PYPROJECT = "pyproject.toml"
LLVM_NOTE = ("This changes the compiled binaries but bumps no package version, so merging does not republish "
             "anything. To republish, bump the package versions in this pull request.")
INPUT_KINDS = ("dotnet", "avalonia", "skiasharp", "angle")
TITLE_LENGTH = 72

_VERSION = re.compile(r"\d+(?:\.\d+){1,3}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_TAG = re.compile(r"v\d+(?:\.\d+){1,3}")
_IMAGE = re.compile(r"mcr\.microsoft\.com/([a-z0-9][a-z0-9._/-]*):([\w][\w.-]*)(?:@(sha256:[0-9a-f]{64}))?")


class UpstreamError(Exception):
    pass


class LookupFailed(UpstreamError):
    """A failed upstream lookup of one item; --keep-going records it and skips the item instead."""


class NotFound(UpstreamError):
    """HTTP 404."""


def _reason(error):
    return str(error) if isinstance(error, UpstreamError) else f"{type(error).__name__}: {error}"


def _malformed(**values):
    """Problems for the {name: (value, pattern)} that are set but do not have the expected form."""
    return [f"unexpected {name} {value!r}" for name, (value, pattern) in values.items()
            if value is not None and (not isinstance(value, str) or not pattern.fullmatch(value))]


def _checked(value, pattern, what):
    """`value` when it has the expected form; values from upstream are written into files and workflows."""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise UpstreamError(f"{what}: unexpected value {value!r}")
    return value


# --- Network -------------------------------------------------------------------------------------

class Client:
    """Read-only access to upstream services; tests substitute an object with the same methods."""

    def __init__(self, timeout=TIMEOUT, token=None, attempts=3):
        self.timeout = timeout
        self.token = token
        self.attempts = attempts

    def _request(self, url, compressed, accept=None):
        github = urlsplit(url).hostname == "api.github.com"
        headers = {"User-Agent": USER_AGENT}
        if compressed:
            headers["Accept-Encoding"] = "gzip"
        if github:
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        if accept:
            headers["Accept"] = accept
        request = urllib.request.Request(url, headers=headers)
        if github and self.token:
            # Unredirected: urllib copies ordinary headers to the target of a redirect, which may be another host.
            request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        return request

    def _retry(self, url, compressed, action=None, accept=None):
        """action(response) for a fresh response, retrying transient failures, including those while reading;
        the open response itself when `action` is None."""
        for attempt in range(1, self.attempts + 1):
            try:
                response = urllib.request.urlopen(self._request(url, compressed, accept), timeout=self.timeout)
                if action is None:
                    return response
                with response:
                    return action(response)
            except urllib.error.HTTPError as error:
                error.close()
                retry = error.code >= 500 or error.code == 429
                if not retry or attempt == self.attempts:
                    hint = ""
                    if error.code in (403, 429) and urlsplit(url).hostname == "api.github.com" and not self.token:
                        hint = " (GitHub API rate limit? set GITHUB_TOKEN)"
                    raise (NotFound if error.code == 404 else UpstreamError)(
                        f"GET {url}: HTTP {error.code} {error.reason}{hint}") from None
            except (OSError, http.client.HTTPException, EOFError, zlib.error) as error:
                # URLError, timeouts and resets are OSErrors; IncompleteRead is an HTTPException; a truncated gzip
                # body raises EOFError or zlib.error.
                if attempt == self.attempts:
                    reason = getattr(error, "reason", None) or f"{type(error).__name__}: {error}"
                    raise UpstreamError(f"GET {url}: {reason} (after {attempt} attempts, "
                                        f"timeout {self.timeout}s)") from None
            time.sleep(2 * attempt)
        raise AssertionError("unreachable")

    @staticmethod
    def _body(response):
        data = response.read()
        if response.headers.get("Content-Encoding", "") == "gzip" or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data

    def get(self, url, accept=None):
        """The response body. `accept` asks for one representation, served as is, e.g. a manifest to hash."""
        return self._retry(url, accept is None, self._body, accept)

    def json(self, url):
        data = self.get(url)
        if data.startswith(b")]}'"):
            data = data.split(b"\n", 1)[1]
        try:
            return json.loads(data)
        except ValueError as error:
            raise UpstreamError(f"GET {url}: invalid JSON ({error})") from None

    def stream(self, url, chunk_size=1 << 20):
        """Yield the raw response body, exactly as served (no content decoding). A failure while reading is not
        retried, because the chunks already yielded cannot be taken back."""
        with self._retry(url, False) as response:
            while True:
                try:
                    chunk = response.read(chunk_size)
                except (OSError, http.client.HTTPException) as error:
                    raise UpstreamError(f"GET {url}: {type(error).__name__}: {error} while reading") from None
                if not chunk:
                    return
                yield chunk

    def ls_remote(self, repository, *patterns):
        """{ref: commit} of the refs matching `patterns`, retrying failures like the HTTP reads, except a timeout:
        its limit covers the whole command, so retrying would make a hung git endpoint cost every lookup several
        limits, and the workflow runs the check and all groups within one job timeout."""
        command = ["git", "ls-remote", repository, *patterns]
        environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        for attempt in range(1, self.attempts + 1):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout * 2,
                                        env=environment, check=False)
            except FileNotFoundError:
                raise UpstreamError("git is required for ls-remote") from None
            except subprocess.TimeoutExpired:
                raise UpstreamError(f"git ls-remote {repository}: timed out after {self.timeout * 2}s") from None
            if result.returncode == 0:
                break
            reason = result.stderr.strip() or f"exit code {result.returncode}"
            if attempt == self.attempts:
                raise UpstreamError(f"git ls-remote {repository}: {reason} (after {attempt} attempts)")
            time.sleep(2 * attempt)
        refs = {}
        for line in result.stdout.splitlines():
            commit, _, ref = line.partition("\t")
            refs[ref] = commit
        return refs


class CachingClient:
    """Remembers the reads of one apply run, failed ones included, so the security verdicts before and after the
    update and the planners see the same upstream state and nothing is fetched twice."""

    def __init__(self, client):
        self._client = client
        self._cache = {}

    def _cached(self, key, read):
        if key not in self._cache:
            try:
                self._cache[key] = read()
            except UpstreamError as error:
                self._cache[key] = error
        value = self._cache[key]
        if isinstance(value, UpstreamError):
            raise copy.copy(value)
        return copy.deepcopy(value)

    def get(self, url, accept=None):
        return self._cached(("get", url, accept), lambda: self._client.get(url, accept))

    def json(self, url):
        return self._cached(("json", url), lambda: self._client.json(url))

    def stream(self, url):
        return self._client.stream(url)

    def ls_remote(self, repository, *patterns):
        return self._cached(("ls-remote", repository, patterns), lambda: self._client.ls_remote(repository, *patterns))


def tag_commit(client, repository, tag):
    """Commit of a tag, peeling annotated tags; None when the tag does not exist."""
    refs = client.ls_remote(repository, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")


def nuget_versions(client, package):
    return client.json(f"{NUGET}/{package.lower()}/index.json")["versions"]


def nuget_latest(client, package):
    stable = [version for version in nuget_versions(client, package) if is_stable(version)]
    if not stable:
        raise UpstreamError(f"{package}: no stable release on nuget.org")
    return max(stable, key=version_key)


def nuspec(client, package, version):
    package, version = package.lower(), version.lower()
    data = client.get(f"{NUGET}/{package}/{version}/{package}.nuspec")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise UpstreamError(f"{package} {version}: invalid nuspec ({error})") from None


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def nuspec_dependencies(root):
    """{dependency id: set of declared version ranges} across all target framework groups."""
    found = {}
    for element in root.iter():
        if _local(element.tag) == "dependency" and element.get("id"):
            found.setdefault(element.get("id"), set()).add(element.get("version", ""))
    return found


def nuspec_commit(root):
    for element in root.iter():
        if _local(element.tag) == "repository":
            return element.get("commit")
    return None


def minimum(version_range):
    """Minimum version of a NuGet dependency range ("1.2.3" means >= 1.2.3)."""
    text = version_range.strip()
    if text[:1] in "[(":
        return text[1:].split(",", 1)[0].rstrip("])").strip()
    return text


def in_range(version, version_range):
    """Whether `version` satisfies a NuGet version range."""
    text = version_range.strip()
    key = version_key(version)
    if text[:1] not in "[(":
        return key >= version_key(text)
    if "," not in text:
        return key == version_key(text[1:-1].strip())
    low, high = (part.strip() for part in text[1:-1].split(",", 1))
    if low and (key < version_key(low) or (text[0] == "(" and key == version_key(low))):
        return False
    if high and (key > version_key(high) or (text[-1] == ")" and key == version_key(high))):
        return False
    return True


def _short(commit):
    return commit[:12] if commit else "-"


def _numbers(version, count):
    """The first `count` numeric components of a version, e.g. the milestone (2) or the major (1)."""
    return tuple(int(part) for part in version.split("-", 1)[0].split(".")[:count])


# --- .NET ----------------------------------------------------------------------------------------

def _sdk_1xx(release, channel):
    """Every 1xx feature-band SDK a release lists (a release may list several, e.g. 9.0.101 and 9.0.100)."""
    sdks = [sdk.get("version", "") for sdk in release.get("sdks") or []]
    if release.get("sdk"):
        sdks.append(release["sdk"].get("version", ""))
    return [sdk for sdk in dict.fromkeys(sdks) if re.fullmatch(rf"{re.escape(channel)}\.(1\d\d)(-.*)?", sdk)]


def vmr_source(client, runtime, release, channel):
    """VMR tag and commit that built `runtime`, cross-checked against Microsoft.NETCore.App.Ref.

    Of the 1xx SDKs the release lists, the one whose peeled tag commit is the commit in the Ref nuspec wins.
    "problems" say why the release cannot be pinned (a nuspec not published yet, a missing tag, commits that
    disagree); "lookup-errors" are reads that failed otherwise (HTTP 5xx, timeouts, git ls-remote), which leave
    it unknown. Nothing is raised, so that the security verdict does not depend on these lookups."""
    result = {"runtime": runtime, "tag": None, "commit": None, "nuget-commit": None, "problems": [],
              "lookup-errors": []}
    candidates = _sdk_1xx(release, channel) if release else []
    if not candidates:
        result["problems"].append(f"no 1xx SDK listed for runtime {runtime}")
        return result
    try:
        result["nuget-commit"] = nuspec_commit(nuspec(client, "Microsoft.NETCore.App.Ref", runtime))
        if result["nuget-commit"] is None:
            result["problems"].append(f"Microsoft.NETCore.App.Ref {runtime} declares no repository commit")
    except NotFound as error:
        result["problems"].append(f"Microsoft.NETCore.App.Ref {runtime}: {error}")
    except UpstreamError as error:
        result["lookup-errors"].append(f"Microsoft.NETCore.App.Ref {runtime}: {error}")
    try:
        tags = []
        for sdk in candidates:
            tag = f"v{sdk}"
            commit = tag_commit(client, VMR, tag)
            tags.append((tag, commit))
            if commit and commit == result["nuget-commit"]:
                result["tag"], result["commit"] = tag, commit
                return result
    except UpstreamError as error:
        # A missing tag is not an error (tag_commit returns None), so this is a failed lookup.
        result["lookup-errors"].append(str(error))
        return result
    if len(tags) == 1:
        result["tag"], result["commit"] = tags[0]
        if result["commit"] is None:
            result["problems"].append(f"{VMR} has no tag {result['tag']}")
        elif result["nuget-commit"]:
            result["problems"].append(f"tag {result['tag']} is {result['commit']} but Microsoft.NETCore.App.Ref "
                                      f"{runtime} was built from {result['nuget-commit']}")
    else:
        found = ", ".join(f"{tag} {commit or '(no tag)'}" for tag, commit in tags)
        result["problems"].append(f"none of the 1xx SDK tags ({found}) is the commit Microsoft.NETCore.App.Ref "
                                  f"{runtime} was built from ({result['nuget-commit'] or 'unknown'})")
    return result


def build_containers(client, commit):
    """{RID: image} of the official Linux build containers at a VMR commit, each pinned by digest.

    The runtime's pipeline resources name them; a tag without a digest is pinned to the digest it has now."""
    where = f"dotnet/dotnet {VMR_PIPELINE_RESOURCES} at {commit}"
    item = client.json(f"{GITHUB_API}/repos/dotnet/dotnet/contents/{VMR_PIPELINE_RESOURCES}?ref={commit}")
    try:
        text = base64.b64decode(item["content"]).decode("utf-8")
    except (KeyError, TypeError, ValueError) as error:
        raise UpstreamError(f"{where}: {_reason(error)}") from None
    containers = {}
    for rid, name in BUILD_CONTAINERS.items():
        match = re.search(rf"^ +{name}:\n +image: *(\S+) *$", text, re.M)
        image = _IMAGE.fullmatch(match.group(1)) if match else None
        if image is None:
            raise UpstreamError(f"{where}: no mcr.microsoft.com image for {name}")
        repository, tag, digest = image.groups()
        if digest is None:
            manifest = client.get(f"{MCR}/v2/{repository}/manifests/{tag}", accept=REGISTRY_MANIFESTS)
            digest = f"sha256:{hashlib.sha256(manifest).hexdigest()}"
        containers[rid] = f"mcr.microsoft.com/{repository}:{tag}@{digest}"
    return containers


def channel_releases(client, url):
    """{runtime version: release} of a channel's releases.json; the first release listing a runtime counts."""
    releases = {}
    for release in client.json(url)["releases"]:
        version = (release.get("runtime") or {}).get("version")
        if version and version not in releases:
            releases[version] = release
    return releases


def dotnet_report(client, pinned):
    index = client.json(DOTNET_INDEX)["releases-index"]
    reports = []
    for framework, runtime in pinned["runtimes"].items():
        channel = ".".join(runtime.split(".")[:2])
        entry = next((item for item in index if item.get("channel-version") == channel), None)
        if entry is None:
            raise UpstreamError(f"{DOTNET_INDEX} has no channel {channel}")
        releases = channel_releases(client, entry["releases.json"])
        newer = sorted((version for version in releases
                        if version_key(version) > version_key(runtime) and (is_stable(version) or not is_stable(runtime))),
                       key=version_key)
        # The security verdict comes first and does not depend on the VMR lookups below.
        security = [{"version": version, "date": releases[version].get("release-date"),
                     "cves": [cve.get("cve-id") for cve in releases[version].get("cve-list") or []]}
                    for version in newer if releases[version].get("security")]
        pinned_source = pinned["hostlibs-sources"].get(runtime, {})
        current = vmr_source(client, runtime, releases.get(runtime), channel)
        if releases.get(runtime) is None:
            current["problems"].append(f"{runtime} is not listed in {entry['releases.json']}")
        if current["tag"] and current["tag"] != pinned_source.get("tag"):
            current["problems"].append(f"pinned tag {pinned_source.get('tag')} != {current['tag']}")
        if current["commit"] and current["commit"] != pinned_source.get("commit"):
            current["problems"].append(f"pinned commit {pinned_source.get('commit')} != {current['commit']}")
        latest = newer[-1] if newer else runtime
        reports.append({
            "framework": framework, "channel": channel, "releases.json": entry["releases.json"],
            "support-phase": entry.get("support-phase"),
            "eol-date": entry.get("eol-date"), "eol": entry.get("support-phase") == "eol",
            "pinned": runtime, "pinned-source": current,
            "latest": latest, "latest-source": vmr_source(client, latest, releases[latest], channel) if newer else current,
            "newer": newer, "security": security,
        })
    return reports


# --- Avalonia ------------------------------------------------------------------------------------

def avalonia_dependencies(client, version):
    """{dependency: {"versions": [...], "declared-by": [...]}} for TRACKED_DEPENDENCIES of Avalonia.Desktop's tree."""
    desktop = nuspec(client, "Avalonia.Desktop", version)
    packages = {"Avalonia.Desktop": desktop}
    for dependency, ranges in sorted(nuspec_dependencies(desktop).items()):
        if dependency.lower().startswith("avalonia.") and any(minimum(item) == version for item in ranges):
            packages[dependency] = nuspec(client, dependency, version)
    tracked = {name.lower(): name for name in TRACKED_DEPENDENCIES}
    found = {}
    for package, root in packages.items():
        for dependency, ranges in nuspec_dependencies(root).items():
            name = tracked.get(dependency.lower())
            if name:
                entry = found.setdefault(name, {"versions": set(), "declared-by": set()})
                entry["versions"].update(ranges)
                entry["declared-by"].add(package)
    return {name: {"versions": sorted(entry["versions"], key=lambda item: version_key(minimum(item))),
                   "declared-by": sorted(entry["declared-by"])} for name, entry in sorted(found.items())}


def _ours(pinned):
    return {"SkiaSharp": pinned["skiasharp"], "HarfBuzzSharp": pinned["harfbuzz"].get(pinned["skiasharp"], ""),
            "Avalonia.Angle.Windows.Natives": pinned["angle"]}


def dependency_problems(current, candidate, ours):
    """Why a candidate Avalonia cannot be applied without other updates; empty when its declared dependencies are
    those of the pinned Avalonia."""
    problems = []
    for name in TRACKED_DEPENDENCIES:
        before = (current.get(name) or {}).get("versions", [])
        after = (candidate.get(name) or {}).get("versions", [])
        if before != after:
            problems.append(f"{name}: {', '.join(before) or 'none'} -> {', '.join(after) or 'none'} "
                            f"(declared by {', '.join((candidate.get(name) or current.get(name))['declared-by'])})")
        for declared in after:
            if ours.get(name) and version_key(ours[name]) < version_key(minimum(declared)):
                problems.append(f"{name}: we pin {ours[name]}, below the declared minimum {minimum(declared)}")
    return problems


def avalonia_versions(client):
    return [version for version in nuget_versions(client, "Avalonia") if is_stable(version)]


def avalonia_targets(stable, current):
    """(newest patch of the pinned minor or None, newest release of each newer minor of the pinned major,
    newest release of each newer major), in ascending order."""
    line = _numbers(current, 2)
    patch = max([version for version in stable if _numbers(version, 2) == line] or [current], key=version_key)
    newest = {}
    for version in stable:
        key = _numbers(version, 2)
        if key > line and (key not in newest or version_key(version) > version_key(newest[key])):
            newest[key] = version
    minors = [newest[key] for key in sorted(newest) if key[0] == line[0]]
    majors = {}
    for key in sorted(newest):
        if key[0] > line[0]:
            majors[key[0]] = newest[key]
    return (patch if version_key(patch) > version_key(current) else None), minors, list(majors.values())


def avalonia_candidate(client, version, current_dependencies, ours):
    """A newer Avalonia release with its tag commit; "problems" say why it cannot be applied (other declared
    dependencies, a missing tag or a tag that is not the commit the packages were built from)."""
    dependencies = avalonia_dependencies(client, version)
    commit = tag_commit(client, AVALONIA_REPOSITORY, version)
    built_from = nuspec_commit(nuspec(client, "Avalonia", version))
    problems = dependency_problems(current_dependencies, dependencies, ours)
    if commit is None:
        problems.append(f"{AVALONIA_REPOSITORY} has no tag {version}")
    elif built_from and built_from != commit:
        problems.append(f"tag {version} is {commit} but the Avalonia package was built from {built_from}")
    problems += _malformed(version=(version, _VERSION), commit=(commit, _COMMIT))
    return {"version": version, "commit": commit, "dependencies": dependencies, "problems": problems}


def avalonia_report(client, pinned):
    current, native = pinned["avalonia"], pinned["avalonia-native"]
    patch, minors, majors = avalonia_targets(avalonia_versions(client), current)
    ours = _ours(pinned)
    dependencies = {current: avalonia_dependencies(client, current)}
    report = {"pinned": current, "native": native, "ours": ours, "patch": None, "minor": None,
              "newer-minors": minors, "newer-majors": majors, "dependencies": dependencies}
    for key, version in (("patch", patch), ("minor", minors[-1] if minors else None)):
        if version:
            candidate = avalonia_candidate(client, version, dependencies[current], ours)
            dependencies[version] = candidate.pop("dependencies")
            report[key] = candidate
    return report


# --- SkiaSharp / HarfBuzzSharp -------------------------------------------------------------------

def skiasharp_source(client, version):
    """skia commit (externals/skia at tag v<version>) and HarfBuzzSharp version (SkiaSharp.HarfBuzz nuspec)."""
    problems = []
    contents = client.json(f"{GITHUB_API}/repos/mono/SkiaSharp/contents/externals/skia?ref=v{version}")
    # The contents API answers with a list for a directory and an object for a file or submodule.
    submodule = isinstance(contents, dict) and contents.get("type") == "submodule"
    skia_commit = contents.get("sha") if submodule else None
    if skia_commit is None:
        problems.append(f"externals/skia is not a submodule at v{version}")
    harfbuzz = sorted(nuspec_dependencies(nuspec(client, "SkiaSharp.HarfBuzz", version)).get("HarfBuzzSharp", set()))
    if len(harfbuzz) != 1:
        problems.append(f"SkiaSharp.HarfBuzz {version} declares HarfBuzzSharp {harfbuzz or 'none'}")
    tag = tag_commit(client, SKIASHARP_REPOSITORY, f"v{version}")
    built_from = nuspec_commit(nuspec(client, "SkiaSharp", version))
    if tag is None or (built_from and built_from != tag):
        problems.append(f"tag v{version} is {tag} but SkiaSharp {version} was built from {built_from}")
    harfbuzz_version = minimum(harfbuzz[0]) if len(harfbuzz) == 1 else None
    problems += _malformed(version=(version, _VERSION), **{"skia commit": (skia_commit, _COMMIT),
                                                            "HarfBuzzSharp version": (harfbuzz_version, _VERSION)})
    return {"version": version, "skia-commit": skia_commit, "harfbuzz": harfbuzz_version, "problems": problems}


def skiasharp_blob(client, path, version):
    """Git blob SHA of `path` in mono/SkiaSharp at tag v<version>; None when the tag has no such file."""
    try:
        item = client.json(f"{GITHUB_API}/repos/mono/SkiaSharp/contents/{path}?ref=v{version}")
    except NotFound:
        return None
    sha = item.get("sha") if isinstance(item, dict) and item.get("type") == "file" else None
    if not isinstance(sha, str) or not _COMMIT.fullmatch(sha):
        raise UpstreamError(f"mono/SkiaSharp {path} at v{version} is not a file")
    return sha


def skiasharp_blob_text(client, sha):
    item = client.json(f"{GITHUB_API}/repos/mono/SkiaSharp/git/blobs/{sha}")
    try:
        return base64.b64decode(item["content"]).decode("utf-8")
    except (KeyError, TypeError, ValueError) as error:
        raise UpstreamError(f"mono/SkiaSharp blob {sha}: {_reason(error)}") from None


def skiasharp_build_files(client, root, line, version):
    """The build files recorded for `line` with their blobs at `version`: {"files", "changed": [(path, diff)]}."""
    record = json.loads((Path(root) / SKIASHARP_BUILD).read_text(encoding="utf-8"))
    if not record.get(line):
        raise UpstreamError(f"{SKIASHARP_BUILD} records no build files for SkiaSharp {line}")
    files, changed = {}, []
    for path, old in sorted(record[line].items()):
        files[path] = new = skiasharp_blob(client, path, version)
        if new == old:
            continue
        before = skiasharp_blob_text(client, old).splitlines(True)
        after = skiasharp_blob_text(client, new).splitlines(True) if new else []
        diff = [item if item.endswith("\n") else item + "\n" for item in
                difflib.unified_diff(before, after, f"v{line}/{path}", f"v{version}/{path}" if new else "/dev/null")]
        if len(diff) > BUILD_DIFF_LINES:
            diff = diff[:BUILD_DIFF_LINES] + [f"... {len(diff) - BUILD_DIFF_LINES} more lines\n"]
        changed.append((path, "".join(diff)))
    return {"files": files, "changed": changed}


def skiasharp_versions(client):
    return [version for version in nuget_versions(client, "SkiaSharp") if is_stable(version)]


def skiasharp_targets(stable, line):
    """(newest release of the pinned milestone if newer, newest release of a newer milestone of the same major)."""
    milestone, major = _numbers(line, 2), _numbers(line, 1)
    patch = max([version for version in stable if _numbers(version, 2) == milestone] or [line], key=version_key)
    newer = [version for version in stable if _numbers(version, 1) == major and _numbers(version, 2) > milestone]
    return (patch if version_key(patch) > version_key(line) else None), (max(newer, key=version_key) if newer else None)


def skiasharp_new_majors(stable, lines):
    """{pinned major: [newest release of each newer major it is the closest pinned line below]}."""
    pinned = sorted({_numbers(line, 1)[0] for line in lines})
    newest = {}
    for version in stable:
        major = _numbers(version, 1)[0]
        if major not in pinned and major > pinned[0] and (major not in newest or
                                                          version_key(version) > version_key(newest[major])):
            newest[major] = version
    found = {}
    for major in sorted(newest):
        found.setdefault(max(item for item in pinned if item < major), []).append(newest[major])
    return found


def skiasharp_report(client, pinned):
    skia = skiasharp_versions(client)
    harfbuzz = [version for version in nuget_versions(client, "HarfBuzzSharp") if is_stable(version)]
    lines = []
    for line in pinned["skiasharp-versions"]:
        major = line.split(".")[0]
        patch, milestone = skiasharp_targets(skia, line)
        same_major = max([v for v in skia if v.split(".")[0] == major] or [line], key=version_key)
        pinned_harfbuzz = pinned["skiasharp-sources"][line]["harfbuzz"]
        harfbuzz_major = pinned_harfbuzz.split(".")[0]
        lines.append({"pinned": line, "harfbuzz": pinned_harfbuzz,
                      "harfbuzz-latest": max([v for v in harfbuzz if v.split(".")[0] == harfbuzz_major] or [pinned_harfbuzz],
                                             key=version_key),
                      "milestone-latest": patch or line, "major-latest": same_major, "milestone": milestone,
                      "patch": skiasharp_source(client, patch) if patch else None})
    new_majors = skiasharp_new_majors(skia, pinned["skiasharp-versions"]) if skia else {}
    return {"lines": lines, "newest": max(skia, key=version_key) if skia else None,
            "newer-majors": [version for versions in new_majors.values() for version in versions],
            "harfbuzz-newest": max(harfbuzz, key=version_key) if harfbuzz else None}


# --- ANGLE ---------------------------------------------------------------------------------------

def _yaml_section(text, name):
    """The `key: value` fields of a top-level mapping of a conandata.yml."""
    match = re.search(rf"^{re.escape(name)}:\n((?:  .*\n?)+)", text, re.M)
    if match is None:
        raise UpstreamError(f"{ANGLE_DATA}: missing {name}")
    return repo_versions._yaml_fields(match.group(1), 2)


def depot_tools_revision(deps, where):
    """The depot_tools revision a DEPS file pins; an error unless exactly one revision is found."""
    found = sorted(set(re.findall(DEPOT_TOOLS_REVISION, deps)))
    if len(found) != 1:
        raise UpstreamError(f"{where}: expected one depot_tools revision in DEPS, found {', '.join(found) or 'none'}")
    return found[0]


def angle_deps(client, url, commit):
    """Text of ANGLE's DEPS at `commit` (Gitiles serves it base64-encoded with ?format=TEXT)."""
    base = url[:-4] if url.endswith(".git") else url
    if not (urlsplit(base).hostname or "").endswith(".googlesource.com"):
        raise UpstreamError(f"cannot read DEPS from {url}: only googlesource.com (Gitiles) is supported")
    location = f"{base}/+/{commit}/DEPS?format=TEXT"
    data = client.get(location)
    try:
        return base64.b64decode(b"".join(data.split()), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise UpstreamError(f"GET {location}: not base64-encoded UTF-8 text ({error})") from None


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def deps_patch_problems(patch, deps):
    """Headers of the hunks of a unified diff of DEPS whose context and removed lines are not in `deps`."""
    lines, problems, index, active = patch.splitlines(), [], 0, False
    target = deps.splitlines()
    while index < len(lines):
        line = lines[index]
        index += 1
        if line.startswith("diff --git "):
            active = False
        elif line.startswith("+++ "):
            active = line[4:].split("\t", 1)[0].strip() in ("b/DEPS", "DEPS")
        elif active and _HUNK.match(line):
            match = _HUNK.match(line)
            old, new = int(match.group(1) or 1), int(match.group(2) or 1)
            block = []
            while (old > 0 or new > 0) and index < len(lines):
                body = lines[index]
                index += 1
                if body.startswith("\\"):
                    continue
                if body.startswith("-"):
                    block.append(body[1:])
                    old -= 1
                elif body.startswith("+"):
                    new -= 1
                else:
                    block.append(body[1:])
                    old, new = old - 1, new - 1
            if not any(target[start:start + len(block)] == block for start in range(len(target) - len(block) + 1)):
                problems.append(line)
    return problems


def angle_report(client, root):
    text = (Path(root) / ANGLE_DATA).read_text(encoding="utf-8")
    source = repo_versions._angle_source(text)
    url, branch, pinned = source.get("url", ""), source.get("branch", ""), source.get("commit", "")
    head = client.ls_remote(url, f"refs/heads/{branch}").get(f"refs/heads/{branch}")
    report = {"url": url, "branch": branch, "pinned": pinned, "head": head, "moved": bool(head and head != pinned),
              "commits-ahead": None}
    if head is None:
        raise UpstreamError(f"{url} has no branch {branch}")
    if report["moved"] and pinned and "googlesource.com" in url:
        try:
            base = url[:-4] if url.endswith(".git") else url
            log = client.json(f"{base}/+log/{pinned}..{head}?format=JSON&n=500")
            report["commits-ahead"] = f"{len(log.get('log', []))}{'+' if log.get('next') else ''}"
        except UpstreamError:
            pass
    return report


# --- NuGet advisories ----------------------------------------------------------------------------

def vulnerability_report(client, pinned):
    advisories = {}
    for page in client.json(NUGET_VULNERABILITIES):
        content = client.json(page["@id"])
        if isinstance(content, dict):
            for package, items in content.items():
                advisories.setdefault(package.lower(), []).extend(items)
    packages = [("SkiaSharp", line) for line in pinned["skiasharp-versions"]]
    packages += [("HarfBuzzSharp", source["harfbuzz"]) for source in pinned["skiasharp-sources"].values()]
    packages += [("Avalonia", pinned["avalonia"]), ("Avalonia.Native", pinned["avalonia-native"]),
                 ("Avalonia.Angle.Windows.Natives", pinned["angle"])]
    results = []
    for package, version in dict.fromkeys(packages):
        hits = [{"url": item.get("url"), "severity": SEVERITY.get(item.get("severity"), item.get("severity")),
                 "versions": item.get("versions")}
                for item in advisories.get(package.lower(), []) if in_range(version, item.get("versions", ""))]
        results.append({"package": package, "version": version, "advisories": hits})
    return results


# --- Security verdict ----------------------------------------------------------------------------

def security_findings(dotnet, vulnerabilities):
    """What the security verdict rests on: newer .NET security releases, end-of-life .NET channels and NuGet
    advisories for pinned packages."""
    return {
        "releases": [{"framework": item["framework"], "version": release["version"],
                      "cves": [cve for cve in release["cves"] if cve]}
                     for item in dotnet or [] for release in item["security"]],
        "eol": [{"framework": item["framework"], "channel": item["channel"], "eol-date": item["eol-date"]}
                for item in dotnet or [] if item["eol"]],
        "advisories": [{"package": item["package"], "version": item["version"], "url": hit["url"],
                        "severity": hit["severity"]} for item in vulnerabilities or [] for hit in item["advisories"]],
    }


def insecure(findings):
    return any(findings.values())


def resolved_findings(before, after):
    """(.NET security releases, NuGet advisories) found before an update and no longer after it."""
    releases = {(item["framework"], item["version"]) for item in after["releases"]}
    advisories = {(item["package"], item["url"]) for item in after["advisories"]}
    return ([item for item in before["releases"] if (item["framework"], item["version"]) not in releases],
            [item for item in before["advisories"] if (item["package"], item["url"]) not in advisories])


def describe_findings(findings):
    lines = [f".NET {item['framework']} {item['version']} is a security release "
             f"({', '.join(item['cves']) or 'no CVE ids'})" for item in findings["releases"]]
    lines += [f".NET {item['channel']} ({item['framework']}) is end of life since {item['eol-date']}"
              for item in findings["eol"]]
    lines += [f"{item['package']} {item['version']}: {item['severity']} advisory {item['url']}"
              for item in findings["advisories"]]
    return lines


# --- Toolchain -----------------------------------------------------------------------------------

def _latest_github_release(client, repository, prefix):
    tag = client.json(f"{GITHUB_API}/repos/{repository}/releases/latest")["tag_name"]
    return tag[len(prefix):] if tag.startswith(prefix) else tag


def pypi_latest(client, package):
    """Newest final release of a PyPI project (no pre-, post- or dev releases, not yanked)."""
    data = client.json(f"{PYPI}/{package}/json")
    final = [version for version, files in (data.get("releases") or {}).items()
             if re.fullmatch(r"\d+(?:\.\d+)*", version) and files and not all(item.get("yanked") for item in files)]
    if final:
        return max(final, key=version_key)
    version = data["info"]["version"]
    if not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise UpstreamError(f"{package}: no final release on PyPI (latest {version})")
    return version


def llvm_release(client):
    """Newest llvm/llvm-project release that is neither a draft nor a prerelease: {"version", "assets"}."""
    best = None
    for release in client.json(LLVM_RELEASES):
        match = re.fullmatch(r"llvmorg-(\d+\.\d+\.\d+)", release.get("tag_name") or "")
        if match is None or release.get("draft") or release.get("prerelease"):
            continue
        if best is None or version_key(match.group(1)) > version_key(best["version"]):
            best = {"version": match.group(1), "assets": sorted(asset.get("name", "") for asset in release.get("assets") or [])}
    if best is None:
        raise UpstreamError(f"{LLVM_RELEASES}: no release")
    return best


class Tool:
    """A pinned tool version: `pattern` captures (prefix, version, suffix) exactly once in `path`. The Linux build
    images install the tools that have a `build_arg`, the docker build argument of their version."""

    def __init__(self, name, path, pattern, latest, build_arg=None):
        self.name, self.path, self.pattern, self.latest, self.build_arg = name, path, pattern, latest, build_arg

    def pin(self, root, required=True):
        file = Path(root) / self.path
        matches = list(re.finditer(self.pattern, file.read_text(encoding="utf-8"), re.M)) if file.exists() else []
        if len(matches) != 1:
            if required:
                raise UpstreamError(f"{self.path}: expected one match of {self.pattern}, found {len(matches)}")
            return None
        return matches[0].group(2)

    def set(self, text, version):
        return _substitute(text, self.pattern, rf"\g<1>{version}\g<3>", self.path)


ACTION_VERSION = r"^([ \t]*version:[ \t]*v?)([\w.]+)([ \t]*)$"
TOOLS = (
    Tool("Task", ".github/actions/install-task/action.yml", ACTION_VERSION,
         lambda client: _latest_github_release(client, "go-task/task", "v"), build_arg="TASK_VERSION"),
    Tool("uv", ".github/actions/install-uv/action.yml", ACTION_VERSION,
         lambda client: _latest_github_release(client, "astral-sh/uv", ""), build_arg="UV_VERSION"),
    Tool("conan", PYPROJECT, r'("conan==)([^"]+)(")', lambda client: pypi_latest(client, "conan")),
    Tool("ninja", PYPROJECT, r'("ninja==)([^"]+)(")', lambda client: pypi_latest(client, "ninja")),
    Tool("TUnit", "tests/Directory.Build.targets", r"(<TUnitVersion>)([^<]+)(</TUnitVersion>)",
         lambda client: nuget_latest(client, "TUnit")),
    # Microsoft.Build is not tracked: it is referenced with ExcludeAssets="runtime" and its runtime assembly
    # comes from the SDK, so its version follows the SDK rather than the newest release on nuget.org.
    Tool("Microsoft.Build.Locator", "tests/HostForge.TestInfra/HostForge.TestInfra.csproj",
         r'(<PackageReference\s+Include="Microsoft\.Build\.Locator"\s+Version=")([^"]+)(")',
         lambda client: nuget_latest(client, "Microsoft.Build.Locator")),
)
LLVM = Tool("LLVM", LLVM_ACTION, r"(\$Version\s*=\s*')([^']+)(')", lambda client: llvm_release(client)["version"])
LLVM_CACHE_KEY = Tool("LLVM cache key", LLVM_ACTION, r"(\bkey:[ \t]*llvm-)([\w.]+)(-win64\b)", None)


def _tunit_pins(root):
    """TUnit versions referenced by the test projects, resolving $(Property) from tests/Directory.Build.*."""
    root = Path(root)
    properties = {}
    for name in ("Directory.Build.props", "tests/Directory.Build.props", "tests/Directory.Build.targets"):
        if (root / name).exists():
            properties.update(re.findall(r"<(\w+)(?:\s[^>]*)?>([^<$]+)</\1>", (root / name).read_text(encoding="utf-8")))
    pins = set()
    for project in sorted(root.glob("tests/*/*.csproj")):
        for version in re.findall(r'Include="TUnit"\s+Version="([^"]+)"', project.read_text(encoding="utf-8")):
            pins.add(re.sub(r"\$\((\w+)\)", lambda match: properties.get(match.group(1), match.group(0)), version))
    return ", ".join(sorted(pins)) or None


def tool_report(client, root):
    results = []
    for tool, group in [(LLVM, "llvm")] + [(tool, "tools") for tool in TOOLS]:
        entry = {"name": tool.name, "source": tool.path, "group": group, "pinned": None, "latest": None, "error": None}
        try:
            entry["pinned"] = _tunit_pins(root) if tool.name == "TUnit" else tool.pin(root, required=False)
            entry["latest"] = tool.latest(client)
        except Exception as error:  # noqa: BLE001 - malformed upstream data must not hide the other tools
            entry["error"] = _reason(error)
        results.append(entry)
    return results


# --- check ---------------------------------------------------------------------------------------

SECURITY_SECTIONS = ("dotnet", "vulnerabilities")


def run_check(client, root=ROOT):
    pinned = repo_versions.load(root)
    report = {"errors": []}
    sections = [("dotnet", lambda: dotnet_report(client, pinned)), ("avalonia", lambda: avalonia_report(client, pinned)),
                ("skiasharp", lambda: skiasharp_report(client, pinned)), ("angle", lambda: angle_report(client, root)),
                ("vulnerabilities", lambda: vulnerability_report(client, pinned)), ("tools", lambda: tool_report(client, root))]
    for name, collect in sections:
        try:
            report[name] = collect()
        except Exception as error:  # noqa: BLE001 - upstream data is untrusted; the section reports what failed
            report[name] = None
            report["errors"].append({"section": name, "error": _reason(error)})
    report["errors"] += [{"section": "tools", "error": f"{tool['name']}: {tool['error']}"}
                         for tool in report.get("tools") or [] if tool["error"]]
    # An end-of-life channel gets no further security fixes, so pinning it is a security finding.
    report["security"] = insecure(security_findings(report.get("dotnet"), report.get("vulnerabilities")))
    return report


def _source_problems(source):
    """The problems and failed lookups of a vmr_source result, for the report."""
    return source["problems"] + [f"lookup failed: {error}" for error in source["lookup-errors"]]


def _sections(report):
    """[(title, headers, rows, notes)] shared by the text and Markdown renderings."""
    sections = []
    if report.get("dotnet") is not None:
        rows, notes = [], []
        for item in report["dotnet"]:
            latest = item["latest-source"]
            cves = sorted({cve for release in item["security"] for cve in release["cves"]})
            security = (f"{len(item['security'])} security release(s), {len(cves)} CVE(s)" if item["security"]
                        else "none newer" if not item["newer"] else "no")
            rows.append([item["framework"], item["pinned"], item["latest"], latest["tag"] or "-", _short(latest["commit"]),
                         security, item["support-phase"] or "-"])
            if item["eol"]:
                notes.append(f"{item['framework']}: .NET {item['channel']} is end of life (since {item['eol-date']}) "
                             "and gets no further security fixes; move to a supported channel")
            for release in item["security"]:
                notes.append(f"{item['framework']} {release['version']} ({release['date']}): {', '.join(release['cves']) or 'no CVE ids'}")
            mapping = item["pinned-source"]
            if _source_problems(mapping):
                notes.append(f"{item['framework']} pinned {item['pinned']}: " + "; ".join(_source_problems(mapping)))
            else:
                notes.append(f"{item['framework']} pinned {item['pinned']} -> {mapping['tag']} {mapping['commit']}: "
                             "tag and Microsoft.NETCore.App.Ref commit agree with native/hostlibs/versions.json")
            if item["newer"] and _source_problems(latest):
                notes.append(f"{item['framework']} latest {item['latest']}: " + "; ".join(_source_problems(latest)))
        if any(item["newer"] and is_stable(item["pinned"]) for item in report["dotnet"]):
            notes.append("newer patch releases: apply group inputs")
        previews = [f"{item['framework']} {item['latest']}" for item in report["dotnet"]
                    if item["newer"] and not is_stable(item["pinned"])]
        if previews:
            notes.append("newer releases of preview pins (report only; move a preview pin by hand): "
                         + ", ".join(previews))
        sections.append((".NET runtimes", ["TFM", "pinned", "latest", "VMR tag", "VMR commit", "newer security", "support"],
                         rows, notes))
    if report.get("avalonia") is not None:
        avalonia = report["avalonia"]
        rows, notes = [], []
        for version, dependencies in avalonia["dependencies"].items():
            rows.append([version] + [", ".join(dependencies.get(name, {}).get("versions", [])) or "-"
                                     for name in TRACKED_DEPENDENCIES])
        rows.append(["ours"] + [avalonia["ours"][name] or "-" for name in TRACKED_DEPENDENCIES])
        patch, minor = avalonia["patch"], avalonia["minor"]
        notes.append(f"pinned Avalonia {avalonia['pinned']}, AvaloniaNative {avalonia['native']}; "
                     + (f"patch {patch['version']} ({_short(patch['commit'])})" if patch else "no newer patch"))
        if patch:
            notes.append("patch not applicable (report only): " + "; ".join(patch["problems"]) if patch["problems"]
                         else "patch: apply group inputs")
        if minor:
            notes.append(f"newer minor {minor['version']}: " + (
                "report only, " + "; ".join(minor["problems"]) if minor["problems"] else "apply group avalonia-minor"))
        if avalonia["newer-majors"]:
            notes.append("newer major releases (report only): " + ", ".join(avalonia["newer-majors"]))
        declared = {name: ", ".join(entry["declared-by"]) for name, entry in
                    avalonia["dependencies"][avalonia["pinned"]].items()}
        notes.append("declared by: " + "; ".join(f"{name} <- {packages}" for name, packages in declared.items()))
        sections.append(("Avalonia", ["version", *TRACKED_DEPENDENCIES], rows, notes))
    if report.get("skiasharp") is not None:
        skia = report["skiasharp"]
        rows, notes = [], []
        for line in skia["lines"]:
            rows.append([line["pinned"], line["milestone-latest"], line["major-latest"], line["harfbuzz"],
                         line["harfbuzz-latest"]])
            patch = line["patch"]
            if patch:
                notes.append(f"{line['pinned']} -> {patch['version']}: skia {patch['skia-commit']}, HarfBuzzSharp "
                             f"{patch['harfbuzz']}" + (f" ({'; '.join(patch['problems'])}; report only)"
                                                       if patch["problems"] else " (apply group inputs)"))
            if line["milestone"]:
                notes.append(f"{line['pinned']}: newer milestone {line['milestone']} "
                             f"(apply group skiasharp-{line['pinned'].split('.')[0]})")
        if skia["newer-majors"]:
            notes.append("newer major releases (report only): " + ", ".join(skia["newer-majors"]))
        notes.append(f"newest stable SkiaSharp {skia['newest']}, HarfBuzzSharp {skia['harfbuzz-newest']}")
        sections.append(("SkiaSharp / HarfBuzzSharp", ["pinned", "same milestone", "same major", "HarfBuzzSharp",
                                                       "HarfBuzzSharp latest"], rows, notes))
    if report.get("angle") is not None:
        angle = report["angle"]
        status = ("no commit pinned" if not angle["pinned"] else
                  f"moved ({angle['commits-ahead']} commits ahead)" if angle["moved"] and angle["commits-ahead"]
                  else "moved" if angle["moved"] else "up to date")
        sections.append(("ANGLE", ["branch", "pinned", "head", "status"],
                         [[angle["branch"], _short(angle["pinned"]), _short(angle["head"]), status]],
                         ["apply group inputs moves the pin to the branch head, with the depot_tools revision of its "
                          "DEPS; a branch change follows Avalonia.Angle.Windows.Natives (see Avalonia) and stays "
                          "manual"]))
    if report.get("vulnerabilities") is not None:
        rows = []
        for item in report["vulnerabilities"]:
            found = "; ".join(f"{hit['severity']} {hit['url']} {hit['versions']}" for hit in item["advisories"])
            rows.append([item["package"], item["version"], found or "none"])
        sections.append(("NuGet advisories", ["package", "version", "advisories"], rows, []))
    if report.get("tools") is not None:
        rows = [[tool["name"], tool["pinned"] or "-", tool["latest"] or "-", tool["source"], tool["group"]]
                for tool in report["tools"]]
        sections.append(("Toolchain", ["tool", "pinned", "latest", "pinned in", "apply group"], rows,
                         ["Microsoft.Build is not tracked: its runtime assembly comes from the SDK"]))
    if report["errors"]:
        sections.append(("Errors", ["section", "error"], [[item["section"], item["error"]] for item in report["errors"]], []))
    return sections


def _verdict(report):
    if report["security"]:
        return "ACTION NEEDED (newer .NET security release, end-of-life .NET channel or vulnerable pinned package)"
    if any(error["section"] in SECURITY_SECTIONS for error in report["errors"]):
        return "UNKNOWN (a security check could not complete; see Errors)"
    return "no newer .NET security release, no end-of-life .NET channel, no advisories"


def render_text(report):
    out = []
    for title, headers, rows, notes in _sections(report):
        widths = [max(len(str(row[index])) for row in [headers, *rows]) for index in range(len(headers))]
        out.append(title)
        for row in [headers, *rows]:
            out.append("  " + "  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)).rstrip())
        out += [f"  - {note}" for note in notes]
        out.append("")
    out.append(f"Security: {_verdict(report)}")
    return "\n".join(out) + "\n"


def render_markdown(report):
    out = ["## Upstream check", ""]
    out.append(f"**Security: {_verdict(report)}.**" if report["security"] else f"Security: {_verdict(report)}.")
    out.append("")
    for title, headers, rows, notes in _sections(report):
        out += [f"### {title}", "", "| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
        out += ["| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |" for row in rows]
        out += [""] + [f"- {note}" for note in notes] + ([""] if notes else [])
    return "\n".join(out) + "\n"


def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def command_check(args, client):
    report = run_check(client, args.root)
    sys.stdout.write(json.dumps(_jsonable(report), indent=2) + "\n" if args.json else render_text(report))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(render_markdown(report))
    if args.fail_on_security and report["security"]:
        return 1
    # Only the sections behind the security verdict fail the check; the other sections are informational.
    return 2 if any(error["section"] in SECURITY_SECTIONS for error in report["errors"]) else 0


# --- lock ----------------------------------------------------------------------------------------

def _git(root, *arguments):
    try:
        return subprocess.run(["git", *arguments], cwd=root, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def lock_at(root, ref):
    """The lock document at git revision `ref` of the repository at `root`: (lock, None) when read,
    (None, "no-file") when that revision has no lock, (None, "no-ref") without git or without that revision."""
    resolved = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if resolved is None or resolved.returncode != 0:
        return None, "no-ref"
    commit = resolved.stdout.decode("utf-8").strip()
    # ls-tree and "<commit>:./<path>" resolve paths relative to `root`, which may be below the work tree root.
    listed = _git(root, "ls-tree", "--name-only", commit, "--", package_inputs.LOCK)
    if listed is None or listed.returncode != 0:
        raise UpstreamError(f"git ls-tree {ref}: {listed.stderr.decode('utf-8', 'replace').strip() if listed else ''}")
    if not listed.stdout.strip():
        return None, "no-file"
    shown = _git(root, "show", f"{commit}:./{package_inputs.LOCK}")
    if shown is None or shown.returncode != 0:
        raise UpstreamError(f"git show {ref}:{package_inputs.LOCK}: "
                            f"{shown.stderr.decode('utf-8', 'replace').strip() if shown else ''}")
    return package_inputs.parse(shown.stdout.decode("utf-8"), f"{package_inputs.LOCK} at {ref}"), None


def _print_all(messages, stream=None):
    for message in messages:
        print(message, file=stream or sys.stderr)


def _lock_check(root, records, lock, base):
    """lock --check: rules 1-4, and every entry of the lock at `base` (HEAD by default) unchanged."""
    problems = package_inputs.guard(lock, records)
    _print_all(problems)
    ref = base or "HEAD"
    base_lock, reason = lock_at(root, ref)
    changed = []
    if reason == "no-ref":
        if base:
            raise UpstreamError(f"cannot resolve the git revision {base}")
    elif reason == "no-file":
        if base:
            print(f"{base} has no {package_inputs.LOCK}; skipped the comparison with it.")
    else:
        changed = package_inputs.changed_entries(base_lock, lock)
        _print_all(package_inputs.describe_changed_entry(item, ref) for item in changed)
        if changed:
            print(f"Entries of a committed lock never change: restore them from {ref}. To publish other inputs, "
                  "change the version instead.", file=sys.stderr)
        elif base:
            count = sum(len(versions) for versions in package_inputs.history(base_lock).values())
            print(f"All {count} entries of the lock at {base} are unchanged.")
    differences = package_inputs.stale(lock, records)
    if differences:
        print(f"{package_inputs.LOCK} is out of date:\n  " + "\n  ".join(differences)
              + "\nRun `python scripts/upstream.py lock` (task upstream-lock) and commit the result.", file=sys.stderr)
    if problems or changed or differences:
        return 1
    print(f"{package_inputs.LOCK} is up to date.")
    return 0


def _lock_write(root, records, lock, force):
    """lock [--force]: append the computed versions; --force may replace or remove entries not committed in HEAD."""
    conflicts = package_inputs.conflicts(records)
    if conflicts:
        _print_all(package_inputs.describe_conflict(item) for item in conflicts)
        print(f"Not written: {package_inputs.LOCK}.", file=sys.stderr)
        return 1
    committed, reason = lock_at(root, "HEAD")
    if committed is not None:
        changed = package_inputs.changed_entries(committed, lock)
        if changed:
            _print_all(package_inputs.describe_changed_entry(item, "HEAD") for item in changed)
            print(f"Not written: {package_inputs.LOCK}. Committed entries never change; restore them with "
                  f"`git checkout HEAD -- {package_inputs.LOCK}` and run lock again.", file=sys.stderr)
            return 1
    committed_versions = {(family, version) for family, versions in package_inputs.history(committed).items()
                          for version in versions}

    packages = {family: dict(versions) for family, versions in package_inputs.history(lock).items()}
    violations = package_inputs.violations(lock, records)
    regressions = package_inputs.regressions(lock, records)
    replaced = sorted({(item["family"], item["version"]) for item in violations})
    removed = sorted({(item["family"], version) for item in regressions for version in item["newer"]})
    if violations or regressions:
        _print_all(package_inputs.describe_violation(item) for item in violations)
        _print_all(package_inputs.describe_regression(item) for item in regressions)
        if not force:
            print(f"Not written: {package_inputs.LOCK}. Change the versions above. --force replaces or removes only "
                  "entries that are not committed yet (not in the lock of HEAD).", file=sys.stderr)
            return 1
        blocked = sorted(set(replaced + removed) & committed_versions)
        if blocked:
            print(f"Not written: {package_inputs.LOCK}. --force cannot change entries committed in HEAD: "
                  + ", ".join(f"{family} {version}" for family, version in blocked)
                  + ". Change the versions instead.", file=sys.stderr)
            return 1
        if reason is not None:
            print(f"HEAD has no {package_inputs.LOCK} (or git is unavailable): every entry counts as uncommitted.")
        for family, version in replaced + removed:
            del packages[family][version]

    result = package_inputs.append(packages, records)
    if lock is not None and package_inputs.document(result) == lock:
        print(f"{package_inputs.LOCK} is up to date.")
        return 0
    locked = package_inputs.history(lock)
    lines = [f"replaced {family} {version}" for family, version in replaced]
    lines += [f"removed {family} {version}" for family, version in removed]
    lines += [f"added {family} {version}" for family, versions in result.items() for version in versions
              if version not in locked.get(family, {})]
    if lock is not None and lock.get("comment") != package_inputs.COMMENT:
        lines.append("updated the comment")
    _write_files(root, {package_inputs.LOCK: package_inputs.render(result).encode("utf-8")})
    print(f"Wrote {package_inputs.LOCK}:\n  " + "\n  ".join(lines or ["reformatted"]))
    return 0


def command_lock(args, client=None):
    root = Path(args.root)
    records = package_inputs.compute(root)
    lock = package_inputs.read_lock(root)
    if args.check:
        return _lock_check(root, records, lock, args.base)
    return _lock_write(root, records, lock, args.force)


# --- apply: files --------------------------------------------------------------------------------

def _read(path):
    raw = path.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), ("\r\n" if "\r\n" in raw else "\n")


def _write(path, text, newline):
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))


def _replace_file(path, data):
    """Write `data` to a temporary file next to `path`, then move it over `path` in one step."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _write_files(root, contents):
    """Replace the files {relative path: bytes} under `root`: all of them or, after any failure, none."""
    root = Path(root)
    originals = {relative: (root / relative).read_bytes() if (root / relative).exists() else None
                 for relative in contents}
    current = None
    try:
        for current, data in contents.items():
            _replace_file(root / current, data)
    except BaseException as error:
        unrestored = []
        for relative, original in originals.items():
            path = root / relative
            try:
                if original is None:
                    if path.exists():
                        path.unlink()
                elif not path.exists() or path.read_bytes() != original:
                    _replace_file(path, original)
            except OSError as restore_error:
                unrestored.append(f"{relative} ({restore_error})")
        if unrestored:
            raise UpstreamError(f"writing {current} failed ({type(error).__name__}: {error}) and these files could "
                                "not be restored: " + ", ".join(unrestored)) from error
        if isinstance(error, OSError):
            raise UpstreamError(f"writing {current} failed ({error}); every file was restored") from error
        raise


def _edit(root, relative, change):
    path = Path(root) / relative
    text, newline = _read(path)
    _write(path, change(text), newline)


def _substitute(text, pattern, replacement, where):
    result, count = re.subn(pattern, replacement, text, flags=re.M)
    if count != 1:
        raise UpstreamError(f"{where}: expected one match of {pattern}, found {count}")
    return result


def _set_property(text, name, old, new, where=package_inputs.PROPS):
    return _substitute(text, rf"(<{name}\b[^>]*>){re.escape(old)}(</{name}>)", rf"\g<1>{new}\g<2>", where)


def _set_revision(text, name, base, revision, where=package_inputs.PROPS, condition=""):
    pattern = rf"(<{name}\b[^>]*{condition}[^>]*>{re.escape(base)}\.)(\d+)(</{name}>)"
    return _substitute(text, pattern, rf"\g<1>{revision}\g<3>", where)


def _revision(text, name, base, condition=""):
    match = re.search(rf"<{name}\b[^>]*{condition}[^>]*>{re.escape(base)}\.(\d+)</{name}>", text)
    if match is None:
        raise UpstreamError(f"{package_inputs.PROPS}: cannot find the revision of {name}")
    return int(match.group(1))


def _increment(text, name, where, condition=""):
    """Add one to the number that ends the value of the property `name` (a revision or a prerelease number)."""
    pattern = rf"(<{name}\b[^>]*{condition}[^>]*>[^<]*?\.)(\d+)(</{name}>)"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise UpstreamError(f"{where}: expected one {name} ending in .<number>, found {len(matches)}")
    return _substitute(text, pattern, lambda match: f"{match.group(1)}{int(match.group(2)) + 1}{match.group(3)}", where)


def _dump_entries(entries):
    """Rewrite a versions.json in the repository's one-entry-per-line layout."""
    lines = [f"  {json.dumps(key)}: {json.dumps(value)}" for key, value in entries.items()]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def _replace_entry(text, old, new, value):
    entries = json.loads(text)
    return _dump_entries({(new if key == old else key): (value if key == old else item) for key, item in entries.items()})


def bump_major(text):
    match = re.search(r"<_Major\b[^>]*>(\d+)</_Major>", text)
    if match is None:
        raise UpstreamError(f"{package_inputs.PROPS}: <_Major> is not a number")
    return _substitute(text, r"(<_Major\b[^>]*>)\d+(</_Major>)", rf"\g<1>{int(match.group(1)) + 1}\g<2>",
                       package_inputs.PROPS), int(match.group(1)) + 1


def append_avalonia_source(text, version, url, sha256, commit):
    match = re.search(r"^sources:\n((?:  .*\n?)+)", text, re.M)
    if match is None:
        raise UpstreamError("native/avalonianative/conandata.yml: missing sources")
    head = text[:match.end()]
    if not head.endswith("\n"):
        head += "\n"
    entry = f'  "{version}":\n    url: "{url}"\n    sha256: "{sha256}"\n    commit: "{commit}"\n'
    return head + entry + text[match.end():]


def set_angle_commits(text, commit, depot_tools):
    """conandata.yml of ANGLE with sources.commit and depot_tools.commit replaced."""
    for section, value in (("sources", commit), ("depot_tools", depot_tools)):
        text = _substitute(text, rf'^({section}:\n(?:  .*\n)*?  commit: *")[0-9a-f]*(")', rf"\g<1>{value}\g<2>",
                           ANGLE_DATA)
    return text


# --- apply: package versions ---------------------------------------------------------------------

def _family_label(family, variant):
    return family if variant == "default" else f"{family} ({variant})"


def version_changes(before, after):
    """["family (variant): old -> new"] for every computed package version that differs."""
    return [f"{_family_label(family, variant)}: {before.get(family, {}).get(variant, {}).get('version', '(none)')} -> "
            f"{record['version']}"
            for family, variants in after.items() for variant, record in variants.items()
            if before.get(family, {}).get(variant, {}).get("version") != record["version"]]


def version_pairs(before, after):
    """[(old, new)] package versions of every computed variant whose version changed."""
    return [(before[family][variant]["version"], record["version"])
            for family, variants in after.items() for variant, record in variants.items()
            if before.get(family, {}).get(variant, {}).get("version") not in (None, record["version"])]


def bump_package_versions(stage, lock):
    """Give every package family whose computed version is locked with other inputs a new version: <_Major> + 1
    once for the AppHost, otherwise the revision (or the prerelease number of ChsBuffer.AppHost.Static) + 1.
    Returns one Update per family."""
    stage = Path(stage)
    before = package_inputs.compute(stage)
    families = {}
    for item in package_inputs.violations(lock, before):
        families.setdefault(item["family"], []).append(item["variant"])
    if not families:
        return []
    props, newline = _read(stage / package_inputs.PROPS)
    static, static_newline = _read(stage / package_inputs.APPHOST_STATIC)
    what = {}
    for family, variants in families.items():
        if family == APPHOST:
            props, major = bump_major(props)
            what[family] = f"<_Major> -> {major}"
        elif family == "ChsBuffer.Angle.Static":
            props = _increment(props, "AnglePackageVersion", package_inputs.PROPS)
            what[family] = "revision of <AnglePackageVersion> + 1"
        elif family == "ChsBuffer.Avalonia.Native.Static":
            props = _increment(props, "AvaloniaNativePackageVersion", package_inputs.PROPS)
            what[family] = "revision of <AvaloniaNativePackageVersion> + 1"
        elif family == "ChsBuffer.SkiaSharp.Static":
            for variant in dict.fromkeys(variants):
                major = re.fullmatch(r"skia(\d+)", variant)
                if major is None:
                    raise UpstreamError(f"{family}: unexpected variant {variant}")
                props = _increment(props, "SkiaSharpPackageVersion", package_inputs.PROPS,
                                   condition=rf"StartsWith\('{major.group(1)}\.'\)")
            what[family] = "revision of <SkiaSharpPackageVersion> + 1"
        elif family == "ChsBuffer.AppHost.Static":
            static = _increment(static, "Version", package_inputs.APPHOST_STATIC)
            what[family] = f"prerelease number of <Version> in {package_inputs.APPHOST_STATIC} + 1"
        else:
            raise UpstreamError(f"{family}: no automatic version bump; {before[family][variants[0]]['bump']}")
    _write(stage / package_inputs.PROPS, props, newline)
    _write(stage / package_inputs.APPHOST_STATIC, static, static_newline)
    after = package_inputs.compute(stage)
    updates = []
    for family, variants in families.items():
        versions = ", ".join(f"{before[family][variant]['version']} -> {after[family][variant]['version']}"
                             for variant in after[family]
                             if before[family][variant]["version"] != after[family][variant]["version"])
        updates.append(Update("bump", f"{family}: {what[family]} ({versions}); its inputs changed", [], None))
    return updates


# --- apply: planners -----------------------------------------------------------------------------

class Update:
    """One planned change. `name` and `version` make up the pull request title, `target` the deterministic
    description of what the update moves to; `skippable` updates may fail without aborting under --keep-going.
    `new_strings[i]` replaces `old_strings[i]` in the package documentation (DOC_FILES)."""

    def __init__(self, kind, description, old_strings, edit, name=None, version=None, target=None, skippable=False,
                 new_strings=()):
        self.kind = kind
        self.description = description
        self.old_strings = old_strings
        self.new_strings = new_strings
        self.edit = edit
        self.name = name
        self.version = version
        self.target = target
        self.skippable = skippable


class Run:
    """State of one apply run: what was looked up, and the notes and errors for the report."""

    def __init__(self, client, root, dry_run=False, keep_going=False):
        self.client = client
        self.root = Path(root)
        self.dry_run = dry_run
        self.keep_going = keep_going
        self.pinned = repo_versions.load(self.root)
        self.notes, self.group_notes, self.errors = [], [], []
        self.build_changes = []

    def attempt(self, label, lookup):
        """lookup() for the item `label`. A failure aborts the run, or with --keep-going is recorded in errors and
        returns None so the item is skipped."""
        try:
            return lookup()
        except Exception as error:  # noqa: BLE001 - upstream data is untrusted; the item reports what failed
            if not self.keep_going:
                raise LookupFailed(f"{label}: {_reason(error)}") from None
            self.errors.append(f"{label}: {_reason(error)}")
            return None


def dotnet_target(run, item):
    """(version, VMR source) of the newest newer release of a dotnet_report item whose VMR source is published, or
    None; the newer releases passed over are noted. A failed lookup raises.

    A preview pin is report only: a newer preview is no patch release, and the release itself would move the
    version of ChsBuffer.AppHost.Static (<runtime>-alpha.N) back."""
    if not is_stable(item["pinned"]):
        if item["newer"]:
            run.notes.append(f"{item['framework']} {item['latest']} not applied: {item['pinned']} is a preview; "
                             "move a preview pin by hand")
        return None
    releases = None
    for version in reversed(item["newer"]):
        if version == item["latest"]:
            source = item["latest-source"]
        else:
            releases = releases or channel_releases(run.client, item["releases.json"])
            source = vmr_source(run.client, version, releases[version], item["channel"])
        if source["lookup-errors"]:
            raise UpstreamError(f".NET {item['framework']} {version}: " + "; ".join(source["lookup-errors"]))
        if not source["problems"] and source["commit"]:
            return version, source
        run.notes.append(f"{item['framework']} {version} not applied: "
                         + "; ".join(source["problems"] or ["no VMR commit"]))
    return None


def plan_dotnet(run):
    """The newest patch release of each pinned .NET channel whose Ref nuspec and VMR tag are published; a lookup
    failure always aborts (no --keep-going)."""
    pinned, updates = run.pinned, []
    for item in dotnet_report(run.client, pinned):
        found = dotnet_target(run, item)
        if found is None:
            continue
        old, (new, source) = item["pinned"], found
        _checked(new, _VERSION, f".NET {item['framework']} runtime")
        _checked(source["tag"], _TAG, f".NET {new} VMR tag")
        _checked(source["commit"], _COMMIT, f".NET {new} VMR commit")
        number = item["framework"][3:].split(".")[0]
        old_source = pinned["hostlibs-sources"][old]
        entry = {"tag": source["tag"], "commit": source["commit"],
                 "containers": build_containers(run.client, source["commit"])}

        def edit(root, old=old, new=new, entry=entry, number=number):
            _edit(root, package_inputs.PROPS, lambda text: _set_property(text, f"HostLibsNet{number}Version", old, new))
            _edit(root, "native/hostlibs/versions.json", lambda text: _replace_entry(text, old, new, entry))

        cves = sorted({cve for release in item["security"] if version_key(release["version"]) <= version_key(new)
                       for cve in release["cves"] if cve})
        description = (f".NET {item['framework']}: {old} -> {new} ({source['tag']} {source['commit']})"
                       + (f"; security: {', '.join(cves)}" if cves else ""))
        updates.append(Update("dotnet", description, [old, old_source["tag"][1:], old_source["commit"]], edit,
                              name=".NET", version=new, new_strings=[new, source["tag"][1:], source["commit"]],
                              target=f".NET {item['framework']} {new} ({source['tag']} {source['commit']})"))
    return updates


def _sha256(chunks):
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def avalonia_update(run, candidate):
    """The edits for a checked Avalonia candidate: AvaloniaVersion, AvaloniaNativeVersion with its revision reset,
    and the source archive in native/avalonianative/conandata.yml."""
    pinned = run.pinned
    current, native, version, commit = pinned["avalonia"], pinned["avalonia-native"], candidate["version"], candidate["commit"]
    if candidate["problems"]:
        run.notes.append(f"Avalonia {version} not applied (report only): " + "; ".join(candidate["problems"]))
        return []
    url = AVALONIA_ARCHIVE.format(commit=commit)
    existing = pinned["avalonia-sources"].get(version)
    if existing and existing.get("commit") != commit:
        run.notes.append(f"Avalonia {version} not applied: conandata.yml pins commit {existing.get('commit')}, "
                         f"tag is {commit}")
        return []
    if existing:
        sha256 = existing["sha256"]
    elif run.dry_run:
        sha256 = "<sha256 of the archive, downloaded by apply>"
    else:
        sha256 = run.attempt(f"Avalonia {version} source archive", lambda: _sha256(run.client.stream(url)))
        if sha256 is None:
            return []

    def edit(root):
        def props(text):
            text = _set_property(text, "AvaloniaVersion", current, version)
            text = _set_property(text, "AvaloniaNativeVersion", native, version)
            if _revision(text, "AvaloniaNativePackageVersion", "$(AvaloniaNativeVersion)") > 1:
                text = _set_revision(text, "AvaloniaNativePackageVersion", "$(AvaloniaNativeVersion)", 1)
            return text

        _edit(root, package_inputs.PROPS, props)
        if not existing:
            _edit(root, "native/avalonianative/conandata.yml",
                  lambda text: append_avalonia_source(text, version, url, sha256, commit))

    versions = list(dict.fromkeys([current, native]))
    # The package documentation names the source commit of the archives next to their version.
    old_commit = pinned["avalonia-sources"].get(native, {}).get("commit")
    return [Update("avalonia", f"Avalonia {', '.join(versions)} -> {version} ({commit}, sha256 {sha256})",
                   [*versions, old_commit], edit, name="Avalonia", version=version,
                   target=f"Avalonia {version} ({commit})", new_strings=[*[version] * len(versions), commit])]


def plan_avalonia(run):
    """The newest patch release of the pinned Avalonia minor."""
    pinned = run.pinned

    def lookup():
        patch, _, _ = avalonia_targets(avalonia_versions(run.client), pinned["avalonia"])
        if patch is None:
            return {}
        return avalonia_candidate(run.client, patch, avalonia_dependencies(run.client, pinned["avalonia"]), _ours(pinned))

    candidate = run.attempt("Avalonia", lookup)
    return avalonia_update(run, candidate) if candidate else []


def plan_avalonia_minor(run):
    """The newest release of the newest minor of the pinned Avalonia major; newer majors are report only."""
    pinned = run.pinned

    def lookup():
        _, minors, majors = avalonia_targets(avalonia_versions(run.client), pinned["avalonia"])
        candidate = None
        if minors:
            candidate = avalonia_candidate(run.client, minors[-1], avalonia_dependencies(run.client, pinned["avalonia"]),
                                           _ours(pinned))
        return {"candidate": candidate, "majors": majors}

    found = run.attempt("Avalonia", lookup)
    if found is None:
        return []
    if found["majors"]:
        run.notes.append("Avalonia newer major releases (report only): " + ", ".join(found["majors"]))
    return avalonia_update(run, found["candidate"]) if found["candidate"] else []


def skiasharp_update(run, line, source, build):
    """The edits that move the SkiaSharp line `line` to the checked release `source`. The build files recorded for
    `line` carry over to `new` when unchanged; otherwise the recipes need a review, so `new` gets no record."""
    pinned, new = run.pinned, source["version"]
    if source["problems"]:
        run.notes.append(f"SkiaSharp {new} not applied: " + "; ".join(source["problems"]))
        return []
    if build["changed"]:
        run.group_notes.append(f"SkiaSharp {new}: the upstream build files "
                               f"{', '.join(path for path, _ in build['changed'])} changed since {line}; "
                               "see Upstream build changes")
        run.build_changes.append({"line": line, "version": new, "files": build["files"], "changed": build["changed"]})

    def record(text):
        data = json.loads(text)
        data.pop(line, None)
        if not build["changed"]:
            data[new] = build["files"]
        return json.dumps(dict(sorted(data.items(), key=lambda item: version_key(item[0]))), indent=2) + "\n"
    old_harfbuzz, major = pinned["skiasharp-sources"][line]["harfbuzz"], line.split(".")[0]
    old_commit = pinned["skiasharp-sources"][line]["skia-commit"]
    condition = rf"StartsWith\('{major}\.'\)"

    def edit(root):
        def props(text):
            if pinned["skiasharp"] == line:
                text = _set_property(text, "SkiaSharpVersion", line, new)
            text = _substitute(
                text, rf"(<HarfBuzzVersion\b[^>]*'\$\(SkiaSharpVersion\)'\s*==\s*'){re.escape(line)}('[^>]*>)"
                      rf"{re.escape(old_harfbuzz)}(</HarfBuzzVersion>)",
                rf"\g<1>{new}\g<2>{source['harfbuzz']}\g<3>", package_inputs.PROPS)
            if _revision(text, "SkiaSharpPackageVersion", "$(SkiaSharpVersion)", condition) > 1:
                text = _set_revision(text, "SkiaSharpPackageVersion", "$(SkiaSharpVersion)", 1, condition=condition)
            return text

        _edit(root, package_inputs.PROPS, props)
        _edit(root, "native/skiasharp/versions.json",
              lambda text: _replace_entry(text, line, new, [source["skia-commit"], source["harfbuzz"]]))
        _edit(root, SKIASHARP_BUILD, record)

    return [Update("skiasharp", f"SkiaSharp {line} -> {new} (skia {source['skia-commit']}, HarfBuzzSharp "
                                f"{old_harfbuzz} -> {source['harfbuzz']})", [line, old_harfbuzz, old_commit], edit,
                   name="SkiaSharp", version=new, new_strings=[new, source["harfbuzz"], source["skia-commit"]],
                   target=f"SkiaSharp {new} (skia {source['skia-commit']}, HarfBuzzSharp {source['harfbuzz']})")]


def _skiasharp_line(run, line, version):
    source = run.attempt(f"SkiaSharp {version}", lambda: skiasharp_source(run.client, version))
    if not source:
        return []
    build = run.attempt(f"SkiaSharp {version} build files",
                        lambda: skiasharp_build_files(run.client, run.root, line, version))
    return skiasharp_update(run, line, source, build) if build else []


def plan_skiasharp(run):
    """The newest patch release of each pinned SkiaSharp milestone."""
    lines = run.pinned["skiasharp-versions"]
    targets = run.attempt("SkiaSharp", lambda: {line: skiasharp_targets(skiasharp_versions(run.client), line)[0]
                                                 for line in lines})
    updates = []
    for line, patch in (targets or {}).items():
        if patch:
            updates += _skiasharp_line(run, line, patch)
    return updates


def plan_skiasharp_major(run, major):
    """The newest release of a newer milestone of the pinned SkiaSharp `major`.x line; newer majors are report only."""
    line = next(line for line in run.pinned["skiasharp-versions"] if _numbers(line, 1)[0] == major)

    def lookup():
        stable = skiasharp_versions(run.client)
        return {"milestone": skiasharp_targets(stable, line)[1],
                "majors": skiasharp_new_majors(stable, run.pinned["skiasharp-versions"]).get(major, [])}

    found = run.attempt("SkiaSharp", lookup)
    if found is None:
        return []
    if found["majors"]:
        run.notes.append("SkiaSharp newer major releases (report only): " + ", ".join(found["majors"]))
    return _skiasharp_line(run, line, found["milestone"]) if found["milestone"] else []


def angle_candidate(client, root):
    """The pinned ANGLE source, the head of its branch and, when the head moved, the depot_tools revision of the
    DEPS file at the head."""
    text = (Path(root) / ANGLE_DATA).read_text(encoding="utf-8")
    source, depot_tools = _yaml_section(text, "sources"), _yaml_section(text, "depot_tools")
    url, branch = source.get("url", ""), source.get("branch", "")
    head = client.ls_remote(url, f"refs/heads/{branch}").get(f"refs/heads/{branch}")
    if head is None:
        raise UpstreamError(f"{url} has no branch {branch}")
    result = {"url": url, "branch": branch, "pinned": source.get("commit", ""),
              "head": _checked(head, _COMMIT, f"{url} {branch} head"),
              "depot-tools": depot_tools.get("commit", ""), "new-depot-tools": None, "deps": None}
    if result["pinned"] and head != result["pinned"]:
        result["deps"] = angle_deps(client, url, head)
        result["new-depot-tools"] = depot_tools_revision(result["deps"], f"ANGLE {head}")
    return result


def _deps_patches(root):
    """{patch file: text} of the ANGLE patches that change DEPS."""
    text = (Path(root) / ANGLE_DATA).read_text(encoding="utf-8")
    found = {}
    for name in re.findall(r'^\s*-?\s*patch_file:\s*"?([^"\n]+)"?\s*$', text, re.M):
        path = Path(root) / "native/angle" / name
        if path.is_file():
            patch = path.read_text(encoding="utf-8")
            if re.search(r"^\+\+\+ (b/)?DEPS\b", patch, re.M):
                found[f"native/angle/{name}"] = patch
    return found


def plan_angle(run):
    """Move the ANGLE pin to the head of its branch, with the depot_tools revision of the DEPS file there."""
    found = run.attempt("ANGLE", lambda: angle_candidate(run.client, run.root))
    if not found or found["head"] == found["pinned"]:
        return []
    if not found["pinned"]:
        run.notes.append(f"ANGLE {found['branch']}: {ANGLE_DATA} pins no commit; pin one by hand")
        return []
    branch, old, head = found["branch"], found["pinned"], found["head"]
    old_tools, new_tools = found["depot-tools"], found["new-depot-tools"]
    for path, patch in _deps_patches(run.root).items():
        problems = deps_patch_problems(patch, found["deps"])
        if problems:
            run.group_notes.append(f"{path} no longer applies to DEPS at {head}: hunks {', '.join(problems)}; "
                                   "update the patch in this pull request")

    def edit(root):
        _edit(root, ANGLE_DATA, lambda text: set_angle_commits(text, head, new_tools))

    tools = (f"depot_tools {old_tools} -> {new_tools}" if new_tools != old_tools
             else f"depot_tools {new_tools} (unchanged)")
    return [Update("angle", f"ANGLE {branch}: {old} -> {head}; {tools} from DEPS",
                   [old] + ([old_tools] if old_tools and new_tools != old_tools else []), edit,
                   name="ANGLE", version=f"{branch}@{head[:12]}",
                   new_strings=[head] + ([new_tools] if old_tools and new_tools != old_tools else []),
                   target=f"ANGLE {branch} {head} (depot_tools {new_tools})")]


def plan_inputs(run, selection):
    """.NET, Avalonia, SkiaSharp and ANGLE; without --keep-going any failed lookup aborts the whole run."""
    planners = {"dotnet": plan_dotnet, "avalonia": plan_avalonia, "skiasharp": plan_skiasharp, "angle": plan_angle}
    updates = []
    for kind in INPUT_KINDS:
        if kind not in selection:
            continue
        try:
            updates += planners[kind](run)
        except Exception as error:  # noqa: BLE001 - reported with the narrower selections that avoid it
            reason = str(error) if isinstance(error, LookupFailed) else f"{kind}: {_reason(error)}"
            others = [f"--{name}" for name in INPUT_KINDS if name in selection and name != kind]
            hint = f" To apply the other updates first, select them: {' '.join(others)}." if others else ""
            if kind != "dotnet" and not run.keep_going:
                hint += " --keep-going skips failed lookups other than .NET."
            raise UpstreamError(f"{reason}\nNothing was written.{hint}") from None
    return updates


def _tool_update(run, tool, pinned, latest):
    def edit(root):
        _edit(root, tool.path, lambda text: tool.set(text, latest))

    return Update("tool", f"{tool.name}: {pinned} -> {latest} ({tool.path})", [pinned], edit, name=tool.name,
                  version=latest, target=f"{tool.name} {latest}", skippable=True)


def plan_tools(run):
    """The newest stable release of each tool; tools are independent, so --keep-going skips only the failed one."""
    updates = []
    for tool in TOOLS:
        def lookup(tool=tool):
            return tool.pin(run.root), _checked(tool.latest(run.client), _VERSION, f"{tool.name} latest version")

        found = run.attempt(tool.name, lookup)
        if found and version_key(found[1]) > version_key(found[0]):
            updates.append(_tool_update(run, tool, *found))
    return updates


def plan_llvm(run):
    """The newest LLVM release, when it publishes the Windows installer the action downloads."""
    pinned = LLVM.pin(run.root)
    if LLVM_CACHE_KEY.pin(run.root) != pinned:
        raise UpstreamError(f"{LLVM_ACTION}: the cache key and $Version pin different LLVM versions")
    release = run.attempt("LLVM", lambda: llvm_release(run.client))
    if release is None or version_key(release["version"]) <= version_key(pinned):
        return []
    version = _checked(release["version"], _VERSION, "LLVM version")
    # The installer type the action downloads, e.g. "msi" in LLVM-${Version}-win64.msi.
    installer = re.search(r"LLVM-\$\{Version\}-win64\.(\w+)", (run.root / LLVM_ACTION).read_text(encoding="utf-8"))
    if installer is None:
        raise UpstreamError(f"{LLVM_ACTION}: no LLVM-${{Version}}-win64.<ext> download URL")
    asset = f"LLVM-{version}-win64.{installer.group(1)}"
    if asset not in release["assets"]:
        run.notes.append(f"LLVM {version} not applied: the release has no {asset} (yet)")
        return []

    def edit(root):
        _edit(root, LLVM_ACTION, lambda text: LLVM_CACHE_KEY.set(LLVM.set(text, version), version))

    return [Update("llvm", f"LLVM: {pinned} -> {version} ({LLVM_ACTION})", [pinned], edit, name="LLVM",
                   version=version, target=f"LLVM {version}")]


# --- apply: groups -------------------------------------------------------------------------------

def group_names(root=ROOT):
    """The apply groups in the order the workflow runs them."""
    majors = sorted({_numbers(line, 1)[0] for line in repo_versions.load(root)["skiasharp-versions"]})
    return ["inputs", "avalonia-minor", *(f"skiasharp-{major}" for major in majors), "tools", "llvm"]


def changes_package_inputs(group):
    return group in ("inputs", "avalonia-minor") or group.startswith("skiasharp-")


def staged_files(group, root):
    """Every file the group may edit, relative to the repository root."""
    if group == "tools":
        return list(dict.fromkeys(tool.path for tool in TOOLS))
    if group == "llvm":
        return [LLVM_ACTION]
    return [*package_inputs.SOURCES, package_inputs.LOCK, SKIASHARP_BUILD, *DOC_FILES]


# Package documentation that names the packaged versions; the package input groups keep it up to date.
DOC_FILES = ("README.md", "src/package-angle-static/README.md", "src/package-apphost-static/README.md",
             "src/package-avalonia-apphost/README.md", "src/package-avalonia-native-static/README.md",
             "src/package-skiasharp-static/README.md", "src/package-static-graphics/README.md")


def _mention_pattern(strings):
    """Whole-word matches of `strings`: 1.2.3 matches neither 1.2.3.4 nor 11.2.3."""
    return re.compile("|".join(rf"(?<![\w.]){re.escape(item)}(?![\w]|\.\d)"
                               for item in sorted(strings, key=len, reverse=True)))


def update_docs(stage, pairs):
    """Replace every old string of `pairs` [(old, new)] in the staged DOC_FILES by its new string."""
    mapping = {old: new for old, new in pairs if old and new and old != new}
    if not mapping:
        return
    pattern = _mention_pattern(mapping)
    for relative in DOC_FILES:
        if (Path(stage) / relative).exists():
            _edit(stage, relative, lambda text: pattern.sub(lambda match: mapping[match.group()], text))


def plan_group(run, group, selection):
    if group == "inputs":
        return plan_inputs(run, selection)
    if group.startswith("skiasharp-"):
        planner = lambda run: plan_skiasharp_major(run, int(group.split("-", 1)[1]))  # noqa: E731
    else:
        planner = {"avalonia-minor": plan_avalonia_minor, "tools": plan_tools, "llvm": plan_llvm}[group]
    try:
        return planner(run)
    except LookupFailed as error:
        raise UpstreamError(f"{error}\nNothing was written. --keep-going records failed lookups and skips them.") from None


def security_state(client, pinned):
    """Findings of the check security verdict for `pinned`; any failure aborts the run."""
    try:
        return security_findings(dotnet_report(client, pinned), vulnerability_report(client, pinned))
    except Exception as error:  # noqa: BLE001 - the verdict must be known; malformed data counts as unknown
        raise UpstreamError(f"cannot evaluate the security state: {_reason(error)}\nNothing was written.") from None


def apply_group(client, root, group="inputs", selection=INPUT_KINDS, dry_run=False, keep_going=False,
                security=False, out=None):
    """Plan, stage and (unless dry_run) write the updates of `group`; returns the metadata of the run.

    security: evaluate the security verdict before and after (inputs group only)."""
    out = out or sys.stdout
    root = Path(root)
    names = group_names(root)
    if group not in names:
        raise UpstreamError(f"unknown group {group}; the groups are: {', '.join(names)}")
    lock = before = None
    if changes_package_inputs(group):
        lock = package_inputs.read_lock(root)
        before = package_inputs.compute(root)
        pending = package_inputs.guard(lock, before) + package_inputs.stale(lock, before)
        if pending:
            raise UpstreamError("the package lock does not match the repository; fix it first (see "
                                "`python scripts/upstream.py lock --check`):\n" + "\n".join(pending))
    run = Run(CachingClient(client), root, dry_run=dry_run, keep_going=keep_going)
    evaluate = security and group == "inputs"
    findings_before = security_state(run.client, run.pinned) if evaluate else None
    updates = plan_group(run, group, selection)
    if group == "llvm" and updates:
        run.group_notes.append(LLVM_NOTE)

    files = staged_files(group, root)
    versions, findings_after = [], None
    with tempfile.TemporaryDirectory(prefix="hostforge-apply-") as staging:
        stage = Path(staging)
        for relative in files:
            if (root / relative).exists():
                (stage / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / relative, stage / relative)
        applied = []
        for update in updates:
            try:
                update.edit(stage)
                applied.append(update)
            except Exception as error:  # noqa: BLE001 - only skippable updates survive, under --keep-going
                if keep_going and update.skippable:
                    run.errors.append(f"{update.name}: {_reason(error)}")
                elif isinstance(error, UpstreamError):
                    raise UpstreamError(f"{update.name or update.kind}: {error}\nNothing was written.") from None
                else:
                    raise
        updates = applied
        if changes_package_inputs(group) and updates:
            updates += bump_package_versions(stage, lock)
            after = package_inputs.compute(stage)
            problems = repo_versions.problems(repo_versions.load(stage)) + package_inputs.guard(lock, after)
            if problems:
                raise UpstreamError("the planned changes leave the repository inconsistent; nothing was written:\n"
                                    + "\n".join(problems))
            history = package_inputs.append(package_inputs.history(lock), after)
            (stage / package_inputs.LOCK).write_bytes(package_inputs.render(history).encode("utf-8"))
            versions = version_changes(before, after)
            update_docs(stage, [pair for update in updates for pair in zip(update.old_strings, update.new_strings)]
                        + version_pairs(before, after))
        if evaluate:
            findings_after = security_state(run.client, repo_versions.load(stage))

        contents, diffs = {}, []
        for relative in files:
            old = (root / relative).read_bytes() if (root / relative).exists() else b""
            new = (stage / relative).read_bytes() if (stage / relative).exists() else b""
            if old != new:
                contents[relative] = new
                diffs += difflib.unified_diff(old.decode("utf-8").splitlines(True), new.decode("utf-8").splitlines(True),
                                              f"a/{relative}", f"b/{relative}")
    if not dry_run:
        _write_files(root, contents)
    changed = list(contents)
    olds = list(dict.fromkeys(item for update in updates for item in update.old_strings if item))
    mentions = remaining_mentions(root, olds, set(files))
    metadata = build_metadata(group, changed, updates, versions, run, findings_before, findings_after, olds, mentions)
    _print_apply(out, metadata, updates, versions, run, diffs, dry_run, olds, mentions)
    return metadata


def tracked_files(root):
    """Files git tracks or would track (read-only `git ls-files`); a directory walk outside a work tree."""
    try:
        result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
                                capture_output=True, check=True, timeout=60)
        names = dict.fromkeys(result.stdout.decode("utf-8").split("\0"))
        return [Path(root) / name for name in names if name]
    except (OSError, subprocess.SubprocessError):
        skip = {".git", "artifacts", "build", "bin", "obj", "node_modules", "__pycache__"}
        return [path for path in Path(root).rglob("*") if path.is_file() and not skip & set(path.parts)]


def remaining_mentions(root, strings, exclude):
    """[(path, line number, line)] of tracked text files still mentioning any of `strings`."""
    if not strings:
        return []
    pattern = _mention_pattern(strings)
    found = []
    for path in tracked_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in exclude or not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                found.append((relative, number, line.strip()))
    return found


# --- apply: report and metadata ------------------------------------------------------------------

def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def pull_request_title(group, updates, security):
    """One Conventional Commits line; shortened to the names, then to a count, when it gets long."""
    named = {}
    for update in updates:
        if update.name:
            named.setdefault(update.name, []).append(update.version)
    prefix, suffix = ("fix(deps): ", " (security)") if security else ("build(deps): ", "")
    if not named:
        return f"{prefix}no updates for {group}{suffix}"
    if len(named) == 1 and len(next(iter(named.values()))) == 1:
        (name, (version,)), = named.items()
        subjects = [f"update {name} to {version}"]
    else:
        subjects = ["update " + _join([f"{name} {' and '.join(versions)}" for name, versions in named.items()])]
    count = sum(map(len, named.values()))
    subjects += ["update " + _join(list(named)), f"update {count} {'tools' if group == 'tools' else 'upstream dependencies'}"]
    return next((prefix + subject + suffix for subject in subjects if len(prefix + subject + suffix) <= TITLE_LENGTH),
                prefix + subjects[-1] + suffix)


def _markdown(text):
    """Plain text as one Markdown list item: no raw HTML (so no HTML comments) and no line breaks."""
    return " ".join(text.replace("<", "&lt;").split())


def _code(text):
    fence = "``" if "`" in text else "`"
    return f"{fence} {text} {fence}" if fence == "``" else f"`{text}`"


def _fenced(text, info=""):
    """`text` as a code block whose fence no line of it can close."""
    longest = max((len(run) for run in re.findall(r"~+", text)), default=0)
    fence = "~" * max(3, longest + 1)
    return [fence + info, text.rstrip("\n"), fence]


def build_changes_section(item):
    line, version = item["line"], item["version"]
    entry = json.dumps({version: item["files"]}, indent=2)[1:-1].strip("\n")
    out = ["", f"### Upstream build changes ({line} -> {version})", "",
           f"The recipes in `native/skiasharp` follow these mono/SkiaSharp files, which changed between v{line} and "
           f"v{version} ([compare](https://github.com/mono/SkiaSharp/compare/v{line}...v{version})). Port the "
           f"changes to the recipes, then add this entry to `{SKIASHARP_BUILD}`; the native tests fail until then:",
           ""]
    out += _fenced(entry, "json")
    for path, diff in item["changed"]:
        out += ["", f"`{path}`" + ("" if item["files"][path] else " (removed)"), ""] + _fenced(diff, "diff")
    return out


def pull_request_body(group, changed, updates, versions, run, security, olds, mentions):
    out = [f"Updates of the `{group}` group, computed by `python scripts/upstream.py apply --group {group}`.", ""]
    out += ["### Applied", ""] + ([f"- {_markdown(update.description)}" for update in updates] or ["- Nothing."])
    if versions:
        out += ["", "### Package versions", ""] + [f"- {_markdown(line)}" for line in versions]
    if security is not None:
        out += ["", "### Security", ""]
        for label, findings in (("Before", security["findings-before"]), ("After", security["findings-after"])):
            described = describe_findings(findings)
            out.append(f"- {label} this change: " + ("ACTION NEEDED: " + "; ".join(map(_markdown, described))
                                                     if described else "no finding"))
        if security["cves"]:
            out.append("- Resolves: " + ", ".join(security["cves"]))
        for item in security["advisories"]:
            out.append(f"- Resolves the {_markdown(item['severity'])} advisory {_markdown(item['url'])} for "
                       f"{_markdown(item['package'])} {_markdown(item['version'])}")
    if run.group_notes:
        out += ["", "### Notes", ""] + [f"- {_markdown(note)}" for note in run.group_notes]
    if run.notes:
        out += ["", "### Not applied", ""] + [f"- {_markdown(note)}" for note in run.notes]
    for item in run.build_changes if changed else []:
        out += build_changes_section(item)
    if mentions:
        shown = mentions[:50]
        out += ["", "### Old versions still mentioned", "",
                f"These tracked files still mention {', '.join(map(_code, olds))}; update them by hand:", ""]
        out += [f"- {_code(f'{path}:{number}')}: {_code(line[:160])}" for path, number, line in shown]
        if len(mentions) > len(shown):
            out.append(f"- … and {len(mentions) - len(shown)} more")
    if run.errors:
        out += ["", "### Lookup errors", "", "These items were skipped (--keep-going):", ""]
        out += [f"- {_markdown(error)}" for error in run.errors]
    if not changed:
        out += ["", "Nothing to change."]
    # A tracked line may itself contain an HTML comment opener; the pull request tool appends its own marker.
    return "\n".join(out).replace("<!--", "<! --") + "\n"


def build_metadata(group, changed, updates, versions, run, findings_before, findings_after, olds, mentions):
    security = details = None
    resolves = False
    if findings_before is not None:
        releases, advisories = resolved_findings(findings_before, findings_after)
        cves = sorted({cve for item in releases for cve in item["cves"] if cve})
        resolves = bool(changed and (releases or advisories))
        security = {"before": insecure(findings_before), "after": insecure(findings_after), "cves": cves}
        details = dict(security, **{"findings-before": findings_before, "findings-after": findings_after,
                                    "advisories": advisories})
    return {
        "group": group,
        "changed": changed,
        "target": "; ".join(update.target for update in updates if update.target) if changed else "",
        "title": pull_request_title(group, updates if changed else [], resolves),
        "body": pull_request_body(group, changed, updates, versions, run, details, olds, mentions),
        "updates": [update.description for update in updates] if changed else [],
        "notes": run.group_notes + run.notes,
        "errors": list(run.errors),
        "security": security,
    }


def _print_apply(out, metadata, updates, versions, run, diffs, dry_run, olds, mentions):
    print("Planned changes:" if dry_run else "Applied changes:", file=out)
    for update in updates:
        print(f"  - {update.description}", file=out)
    if not updates:
        print("  (none: nothing to update)", file=out)
    for line in versions:
        print(f"  package version {line}", file=out)
    if dry_run:
        out.writelines(diffs)
        print("Files that would change: " + (", ".join(metadata["changed"]) or "none"), file=out)
    else:
        print("Changed files: " + (", ".join(metadata["changed"]) or "none"), file=out)
    if metadata["security"] is not None:
        security = metadata["security"]
        print(f"Security finding before: {'yes' if security['before'] else 'no'}, after: "
              f"{'yes' if security['after'] else 'no'}" + (f"; resolves {', '.join(security['cves'])}"
                                                           if security["cves"] else ""), file=out)
    for title, items in (("Notes:", run.group_notes), ("Not applied:", run.notes),
                         ("Lookup errors (skipped with --keep-going):", run.errors)):
        if items:
            print(title, file=out)
            for item in items:
                print(f"  - {item}", file=out)
    if mentions:
        print(f"Tracked files that still mention {', '.join(olds)} (update the text by hand):", file=out)
        for relative, number, line in mentions:
            print(f"  {relative}:{number}: {line[:160]}", file=out)


def write_metadata(path, metadata):
    path = Path(path)
    _replace_file(path, (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def command_apply(args, client):
    selection = {name for name in INPUT_KINDS if getattr(args, name)} or set(INPUT_KINDS)
    metadata = apply_group(client, args.root, args.group, selection, dry_run=args.dry_run, keep_going=args.keep_going,
                           security=bool(args.metadata))
    if args.metadata:
        write_metadata(args.metadata, metadata)
    return 0


def command_groups(args, client=None):
    print("\n".join(group_names(args.root)))
    return 0


def build_args(root=ROOT):
    """The docker build arguments of the tools that the Linux build images install."""
    return [f"--build-arg {tool.build_arg}={_checked(tool.pin(root), _VERSION, f'{tool.name} pin')}"
            for tool in TOOLS if tool.build_arg]


def command_build_args(args, client=None):
    print(" ".join(build_args(args.root)))
    return 0


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    lock = commands.add_parser("lock", help="append the computed package versions to the lock (offline)")
    lock.add_argument("--check", action="store_true", help="only report whether the lock is current")
    lock.add_argument("--force", action="store_true",
                      help="replace or remove entries that kept their version with other inputs or lie ahead of a "
                           "version that moved back; only entries not committed in HEAD")
    lock.add_argument("--base", metavar="GIT_REF",
                      help="with --check: require every entry of the lock at GIT_REF to be present unchanged "
                           "(default: HEAD, skipped silently when unavailable)")
    check = commands.add_parser("check", help="report newer upstream releases (read-only)")
    check.add_argument("--json", action="store_true", help="print the report as JSON")
    check.add_argument("--fail-on-security", action="store_true",
                       help="exit 1 when a newer .NET security release, an end-of-life .NET channel or a NuGet "
                            "advisory for a pinned package exists")
    apply = commands.add_parser("apply", help="update the pinned files of one group")
    apply.add_argument("--group", default="inputs", metavar="NAME",
                       help="inputs (default), avalonia-minor, skiasharp-<major>, tools or llvm; see `groups`")
    apply.add_argument("--dotnet", action="store_true", help="inputs group: .NET runtime patch releases")
    apply.add_argument("--avalonia", action="store_true", help="inputs group: Avalonia patch release of the pinned minor")
    apply.add_argument("--skiasharp", action="store_true",
                       help="inputs group: SkiaSharp patch releases of the pinned milestones")
    apply.add_argument("--angle", action="store_true", help="inputs group: head of the pinned ANGLE branch")
    apply.add_argument("--keep-going", action="store_true",
                       help="record a failed lookup other than .NET and skip that item instead of aborting")
    apply.add_argument("--metadata", metavar="FILE",
                       help="write a JSON summary (changed files, title, body, security) for a pull request")
    apply.add_argument("--dry-run", action="store_true", help="print the planned changes without writing files")
    commands.add_parser("groups", help="print the apply groups, one per line")
    commands.add_parser("build-args", help="print the docker build arguments of the Linux build images (offline)")
    args = parser.parse_args(argv)
    if args.command == "lock" and args.base and not args.check:
        parser.error("lock --base requires --check")
    if args.command == "apply" and args.group != "inputs" and any(getattr(args, name) for name in INPUT_KINDS):
        parser.error("--dotnet, --avalonia, --skiasharp and --angle select parts of the inputs group only")
    client = client or Client(token=os.environ.get("GITHUB_TOKEN") or None)
    handlers = {"lock": command_lock, "check": command_check, "groups": command_groups,
                "apply": command_apply, "build-args": command_build_args}
    # Exit codes: 0 success, 1 a finding (the lock rules, or the security verdict of check --fail-on-security),
    # 2 the command could not complete. Unexpected exceptions are failures to complete, never findings.
    try:
        return handlers[args.command](args, client)
    except (UpstreamError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        print(f"error: unexpected {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
