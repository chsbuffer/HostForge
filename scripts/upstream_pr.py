#!/usr/bin/env python3
"""Open, update or close the upstream update pull requests from the plan of the upstream workflow.

  upstream_pr.py PLAN_DIR [--repo-dir DIR]

PLAN_DIR is the plan artifact of the upstream workflow: status.json ({"check": rc, "commit": planned commit,
"groups": {group: rc}}; "commit" may be missing in older plans) and, per group, <group>.json (apply metadata,
written only when apply exited 0) and <group>.patch (git diff --binary of the apply run against the planned
commit). The plan is untrusted data: it is validated, never executed, and reaches git and gh only as files and
argument lists, never through a shell. Each group may only change its fixed files (GROUP_FILES), and the patch
must change exactly the files its metadata lists.

Every group has one fixed branch upstream/<group> and at most one pull request into main. Only pull requests
opened by the bot (github-actions[bot]) from this repository's branch count; "newer versions" comments count only
when the bot wrote them. The pull request body ends with a marker
<!-- hostforge-upstream group=<group> target=<sha256 of target> patch=<sha256 of patch> -->. A commit in
main..upstream/<group> whose author or committer is not the bot is a maintainer commit; a branch with maintainer
commits is never pushed over or deleted. A branch whose head is the head of a merged bot pull request from it has
landed (e.g. a pull request taken over by a maintainer and squash- or rebase-merged without deleting its branch):
its commits do not count as maintainer commits, so it is deleted or rebuilt like a bot branch, with
--force-with-lease against that head; any later commit protects it again. Per group:
  - apply failed (rc != 0): the group is skipped and reported.
  - a pull request from upstream/<group> that the bot did not open: left alone and reported.
  - lookup errors (metadata "errors"): an existing branch or pull request is kept as it is (no close, delete,
    push or edit) and the errors are reported; without either, a pull request is opened for what changed.
  - no open pull request and a closed, unmerged one carries the same target: suppressed (declined by the
    maintainer; a newer target opens a new pull request).
  - maintainer commits: left alone; on an open pull request one comment per newer target.
  - nothing changed: the open pull request is closed with a comment and the branch deleted.
  - open pull request with the same patch and no conflict: no push (so no CI run to approve); its title and body
    are edited when they differ from the plan.
  - otherwise the branch is rebuilt on origin/main, pushed with --force-with-lease and the pull request created
    or updated. When the patch no longer applies because main moved since the planned commit, the group is
    reported as a stale plan and the next run recomputes it.
Open bot pull requests from upstream/<group> branches of groups the plan no longer lists (e.g. a SkiaSharp line
moved to another major) are closed with a comment and their branches deleted, unless they have maintainer commits.

The bot maintains the title and body of pull requests whose branch has only bot commits; edits by hand are
overwritten. To take a pull request over, push a commit to its branch: from then on the bot never pushes, edits
or closes it and only comments when newer versions appear.

Exit code 0; 1 when the security verdict fails (the upstream check or the inputs group could not complete, a
security problem remains after the automatic updates, a security update has no open pull request for the current
target) or a git or gh operation failed; 2 on internal errors. A Markdown summary is appended to
$GITHUB_STEP_SUMMARY. Environment: GITHUB_REPOSITORY, GITHUB_SERVER_URL, GITHUB_RUN_ID, GH_TOKEN (read by gh).
Standard library only.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
import urllib.parse
from pathlib import Path

BASE_BRANCH = "main"
BRANCH_PREFIX = "upstream/"
SECURITY_GROUP = "inputs"
BOT_NAME = "github-actions[bot]"
BOT_LOGIN = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
WORKFLOW_FILE = "upstream.yml"
GROUP_RE = re.compile(r"[a-z0-9][a-z0-9.-]{0,63}\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?\Z")
MARKER_RE = re.compile(r"<!-- hostforge-upstream group=([a-z0-9.-]+) target=([0-9a-f]{64}) patch=([0-9a-f]{64}) -->")
NEWER_MARKER = "<!-- hostforge-upstream-newer target={target} -->"
MAX_TITLE = 200
MAX_BODY = 60000
COMMAND_TIMEOUT = 300
SETTING_HINT = ("If GitHub refused because GitHub Actions is not permitted to create pull requests, enable "
                "Settings > Actions > General > Workflow permissions > \"Allow GitHub Actions to create and "
                "approve pull requests\".")

# The only files each group may change: what scripts/upstream.py stages for it (staged_files). Fixed here so that
# a plan cannot reach other files; tests/native/test_upstream_pr.py keeps the two in step.
PACKAGE_INPUT_FILES = frozenset({
    "Directory.Build.props",
    "src/package-apphost-static/AppHostStatic.csproj",
    "native/hostlibs/versions.json",
    "native/skiasharp/versions.json",
    "native/avalonianative/conandata.yml",
    "native/angle/conandata.yml",
    "package-inputs.lock.json",
    "README.md",
    "src/package-angle-static/README.md",
    "src/package-apphost-static/README.md",
    "src/package-avalonia-apphost/README.md",
    "src/package-avalonia-native-static/README.md",
    "src/package-skiasharp-static/README.md",
    "src/package-static-graphics/README.md",
})
GROUP_FILES = {
    "inputs": PACKAGE_INPUT_FILES,
    "avalonia-minor": PACKAGE_INPUT_FILES,
    "tools": frozenset({
        ".github/actions/install-task/action.yml",
        "pyproject.toml",
        "tests/Directory.Build.targets",
        "tests/HostForge.TestInfra/HostForge.TestInfra.csproj",
    }),
    "llvm": frozenset({".github/actions/install-llvm/action.yml"}),
}
SKIASHARP_GROUP_RE = re.compile(r"skiasharp-(0|[1-9][0-9]{0,3})\Z")
REGULAR_MODES = ("100644", "100755")

OUTCOMES = {
    "created": "created",
    "updated": "updated",
    "edited": "title and body updated",
    "unchanged": "unchanged",
    "maintainer": "has maintainer commits",
    "foreign": "pull request not opened by the workflow",
    "kept": "kept: lookup errors",
    "suppressed": "suppressed (declined target)",
    "closed": "closed (main is up to date)",
    "retired": "closed (group no longer planned)",
    "stale": "stale plan (main moved; retried next run)",
    "nothing": "nothing to do",
    "failed": "failed",
}


class InternalError(Exception):
    """The plan or the environment is unusable as a whole (exit 2)."""


class PlanError(Exception):
    """The plan of one group is invalid; the group is skipped."""


class OperationError(Exception):
    """A git or gh operation failed."""


class PatchError(Exception):
    """The patch does not apply to main, or changes nothing there; `failure` is what it counts as on the plan's
    own commit (an OperationError or a PlanError)."""

    def __init__(self, failure):
        super().__init__(str(failure))
        self.failure = failure


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def group_files(group):
    """The repository paths `group` may change; empty for an unknown group."""
    if SKIASHARP_GROUP_RE.match(group):
        return PACKAGE_INPUT_FILES
    return GROUP_FILES.get(group, frozenset())


def format_marker(group, target_hash, patch_hash):
    return f"<!-- hostforge-upstream group={group} target={target_hash} patch={patch_hash} -->"


def parse_marker(body, group):
    """(target hash, patch hash) of the last marker of the group in a pull request body, or None."""
    found = None
    for match in MARKER_RE.finditer(body or ""):
        if match.group(1) == group:
            found = (match.group(2), match.group(3))
    return found


def by_bot(item):
    """Whether a pull request or comment of the REST API was created by the bot."""
    return isinstance(item, dict) and (item.get("user") or {}).get("login") == BOT_LOGIN


def one_line(text, limit=None):
    text = " ".join(str(text).split())
    if limit and len(text) > limit:
        text = text[:limit - 3].rstrip() + "..."
    return text


def neutralize_comments(text):
    # A body from the plan must not carry markers of its own: the marker is appended by this script only.
    return text.replace("<!--", "&lt;!--").replace("-->", "--&gt;")


def cell(text):
    return one_line(text).replace("|", "\\|")


def same_text(left, right):
    # GitHub stores bodies edited in the browser with CRLF line breaks.
    def normal(text):
        return (text or "").replace("\r\n", "\n").strip()
    return normal(left) == normal(right)


# --- plan -----------------------------------------------------------------------------------------

def load_status(plan_dir):
    """(check rc, {group: rc}, planned commit or None)."""
    path = plan_dir / "status.json"
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InternalError(f"cannot read {path.name}: {error}") from error
    groups = status.get("groups") if isinstance(status, dict) else None
    check = status.get("check") if isinstance(status, dict) else None
    if type(check) is not int or not isinstance(groups, dict):
        raise InternalError("status.json must be {\"check\": int, \"commit\": sha, \"groups\": {group: int}}")
    for group, rc in groups.items():
        if not GROUP_RE.match(group) or type(rc) is not int:
            raise InternalError(f"status.json: invalid group entry {group!r}: {rc!r}")
    commit = status.get("commit")
    if commit is not None and not (isinstance(commit, str) and SHA_RE.match(commit)):
        raise InternalError(f"status.json: invalid commit {commit!r}")
    return check, groups, commit


def _string_list(value, name):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PlanError(f"metadata field {name} must be a list of strings")
    return value


def load_metadata(plan_dir, group):
    path = plan_dir / f"{group}.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PlanError(f"cannot read {path.name}: {error}") from error
    if not isinstance(meta, dict) or meta.get("group") != group:
        raise PlanError(f"{path.name} does not describe group {group}")
    changed = _string_list(meta.get("changed"), "changed")
    for item in changed:
        parts = item.replace("\\", "/").split("/")
        if not item or item.startswith(("/", "\\")) or ":" in item or ".." in parts or "" in parts:
            raise PlanError(f"metadata path is not repository-relative: {item!r}")
    outside = sorted(set(changed) - group_files(group))
    if outside:
        raise PlanError(f"the plan changes files outside the fixed files of the {group} group: " + ", ".join(outside))
    for name in ("target", "title", "body"):
        if not isinstance(meta.get(name, ""), str):
            raise PlanError(f"metadata field {name} must be a string")
    security = meta.get("security")
    if security is not None and not (isinstance(security, dict)
                                     and all(isinstance(security.get(key, False), bool)
                                             for key in ("before", "after"))):
        raise PlanError("metadata field security must be null or {\"before\": bool, \"after\": bool, ...}")
    result = {
        "changed": changed,
        "target": meta.get("target", ""),
        "title": one_line(meta.get("title", ""), MAX_TITLE),
        "body": meta.get("body", ""),
        "updates": [one_line(item) for item in _string_list(meta.get("updates"), "updates") if one_line(item)],
        "notes": _string_list(meta.get("notes"), "notes"),
        "errors": _string_list(meta.get("errors"), "errors"),
        "security": security,
    }
    if changed:
        if not result["target"].strip():
            raise PlanError("metadata lists changed files but no target")
        if not result["title"]:
            raise PlanError("metadata lists changed files but no title")
    return result


# --- git and gh -----------------------------------------------------------------------------------

def _run(command, cwd=None, env=None, what=None):
    try:
        process = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=COMMAND_TIMEOUT,
                                 check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise OperationError(f"{what or command[0]}: {error}") from error
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", "replace").strip() or process.stdout.decode("utf-8", "replace")
        raise OperationError(f"{what or command[0]} exited with {process.returncode}: {detail.strip()[:2000]}")
    return process.stdout.decode("utf-8", "replace")


class Git:
    def __init__(self, repo_dir):
        self.repo_dir = repo_dir

    def __call__(self, *arguments, cwd=None, env=None):
        return _run(["git", *arguments], cwd=cwd or self.repo_dir, env=env,
                    what=f"git {' '.join(arguments[:2])}")

    def fetch_main(self):
        self("fetch", "--no-tags", "--quiet", "origin", f"+refs/heads/{BASE_BRANCH}:refs/remotes/origin/{BASE_BRANCH}")
        return self("rev-parse", "--verify", f"refs/remotes/origin/{BASE_BRANCH}^{{commit}}").strip()

    def branch_head(self, branch):
        """Fetch the branch and return its commit, or None when the branch does not exist."""
        ref = f"refs/heads/{branch}"
        listed = [line.split("\t") for line in self("ls-remote", "origin", ref).splitlines() if "\t" in line]
        if not any(name == ref for _, name in listed):
            return None
        self("fetch", "--no-tags", "--quiet", "origin", f"+{ref}:refs/remotes/origin/{branch}")
        sha = self("rev-parse", "--verify", f"refs/remotes/origin/{branch}^{{commit}}").strip()
        if not SHA_RE.match(sha):
            raise OperationError(f"unexpected commit id for {branch}: {sha!r}")
        return sha

    def maintainer_emails(self, main_sha, head_sha):
        """Author and committer emails other than the bot's of the commits in main..head (maintainer commits)."""
        lines = self("log", "--format=a%ae%nc%ce", f"{main_sha}..{head_sha}").splitlines()
        return sorted({line[1:] or "(no email)" for line in lines if line[:1] in ("a", "c") and line[1:] != BOT_EMAIL})

    def push(self, source, branch, expected):
        # An empty expected value requires that the branch does not exist yet.
        self("push", "--quiet", "--no-verify", f"--force-with-lease=refs/heads/{branch}:{expected or ''}",
             "origin", f"{source}:refs/heads/{branch}")

    def delete_branch(self, branch, expected):
        self("push", "--quiet", "--no-verify", f"--force-with-lease=refs/heads/{branch}:{expected}",
             "origin", f":refs/heads/{branch}")

    @contextlib.contextmanager
    def _patched(self, base_sha, patch_path, group, changed):
        """A temporary worktree of base_sha with the checked patch applied to it and its index: (temp, worktree)."""
        with tempfile.TemporaryDirectory(prefix="upstream-pr-") as temp:
            worktree = os.path.join(temp, "worktree")
            self("worktree", "add", "--quiet", "--detach", worktree, base_sha)
            try:
                try:
                    self("apply", "--index", "--whitespace=nowarn", str(patch_path), cwd=worktree)
                except OperationError as error:
                    raise PatchError(error) from error
                self._check_staged(worktree, group, changed)
                yield temp, worktree
            finally:
                try:
                    self("worktree", "remove", "--force", worktree)
                except OperationError:
                    pass

    def build_commit(self, base_sha, patch_path, group, changed, message):
        """Commit the patch on base_sha in a temporary worktree; returns the commit id."""
        with self._patched(base_sha, patch_path, group, changed) as (temp, worktree):
            message_path = os.path.join(temp, "message.txt")
            with open(message_path, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(message)
            env = dict(os.environ, GIT_AUTHOR_NAME=BOT_NAME, GIT_AUTHOR_EMAIL=BOT_EMAIL,
                       GIT_COMMITTER_NAME=BOT_NAME, GIT_COMMITTER_EMAIL=BOT_EMAIL)
            self("-c", "commit.gpgsign=false", "commit", "--quiet", "--no-verify", "--cleanup=verbatim",
                 "-F", message_path, cwd=worktree, env=env)
            return self("rev-parse", "--verify", "HEAD^{commit}", cwd=worktree).strip()

    def carries(self, main_sha, head_sha, patch_path, group, changed):
        """Whether the branch makes exactly the changes of the patch: the patch applied to the merge base of main
        and the branch gives the branch's tree. False when it does not apply there."""
        base = self("merge-base", main_sha, head_sha).strip()
        try:
            with self._patched(base, patch_path, group, changed) as (_, worktree):
                tree = self("write-tree", cwd=worktree).strip()
        except PatchError:
            return False
        return tree == self("rev-parse", "--verify", f"{head_sha}^{{tree}}").strip()

    def _check_staged(self, worktree, group, changed):
        """The applied patch adds or modifies regular files: exactly `changed`, all of them fixed files of the group."""
        fields = self("diff", "--cached", "--raw", "--no-renames", "-z", cwd=worktree).split("\0")
        staged = {}
        for header, path in zip(fields[0::2], fields[1::2]):
            old_mode, new_mode, _, _, status = header.lstrip(":").split(" ")
            if status not in ("A", "M") or new_mode not in REGULAR_MODES or (status == "M" and new_mode != old_mode):
                raise PlanError(f"the patch may only add or modify regular files: {path} ({status} {new_mode})")
            staged[path] = status
        if not staged:
            raise PatchError(PlanError("the patch changes nothing on main"))
        listed = {path.replace("\\", "/") for path in changed}
        problems = []
        outside = sorted(set(staged) - group_files(group))
        if outside:
            problems.append(f"the patch changes files outside the fixed files of the {group} group: "
                            + ", ".join(outside))
        unlisted = sorted(set(staged) - listed - set(outside))
        if unlisted:
            problems.append("the patch changes files the metadata does not list: " + ", ".join(unlisted))
        missing = sorted(listed - set(staged))
        if missing:
            problems.append("the patch does not change files the metadata lists: " + ", ".join(missing))
        if problems:
            raise PlanError("; ".join(problems))


class GitHub:
    """REST calls through `gh api`; request bodies are passed as files."""

    def __init__(self, command, repository):
        self.command = list(command)
        self.repository = repository
        self.owner = repository.split("/")[0]

    def api(self, method, path, payload=None, paginate=False):
        arguments = [*self.command, "api", "--method", method]
        if paginate:
            arguments += ["--paginate", "--slurp"]
        with tempfile.TemporaryDirectory(prefix="upstream-pr-gh-") as temp:
            if payload is not None:
                input_path = os.path.join(temp, "request.json")
                with open(input_path, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream)
                arguments += ["--input", input_path]
            output = _run([*arguments, f"repos/{self.repository}/{path}"], what=f"gh api {method} {path}")
        try:
            data = json.loads(output) if output.strip() else None
        except ValueError as error:
            raise OperationError(f"gh api {method} {path}: response is not JSON") from error
        if paginate:
            if not isinstance(data, list) or not all(isinstance(page, list) for page in data):
                raise OperationError(f"gh api {method} {path}: unexpected paginated response")
            data = [item for page in data for item in page]
        return data

    def _own(self, pulls):
        # Keep only pull requests from a branch of this repository into main; never trust a fork, whatever its
        # owner, that reuses the branch name.
        return [pull for pull in pulls if isinstance(pull, dict)
                and ((pull.get("head") or {}).get("repo") or {}).get("full_name") == self.repository
                and isinstance((pull.get("head") or {}).get("ref"), str)
                and (pull.get("base") or {}).get("ref") == BASE_BRANCH]

    def pulls(self, branch):
        """Pull requests from this repository's branch into main, in any state and by anyone."""
        query = urllib.parse.urlencode({"state": "all", "head": f"{self.owner}:{branch}", "base": BASE_BRANCH,
                                        "per_page": 100})
        return [pull for pull in self._own(self.api("GET", f"pulls?{query}", paginate=True))
                if pull["head"]["ref"] == branch]

    def open_pulls(self):
        """Open pull requests from this repository's branches into main, by anyone."""
        query = urllib.parse.urlencode({"state": "open", "base": BASE_BRANCH, "per_page": 100})
        return [pull for pull in self._own(self.api("GET", f"pulls?{query}", paginate=True))
                if pull.get("state") == "open"]

    def pull(self, number):
        return self.api("GET", f"pulls/{number}")

    def create_pull(self, branch, title, body):
        try:
            return self.api("POST", "pulls", {"title": title, "head": branch, "base": BASE_BRANCH, "body": body})
        except OperationError as error:
            raise OperationError(f"{error} {SETTING_HINT}") from error

    def update_pull(self, number, **fields):
        return self.api("PATCH", f"pulls/{number}", fields)

    def comments(self, number):
        return self.api("GET", f"issues/{number}/comments?per_page=100", paginate=True)

    def comment(self, number, body):
        return self.api("POST", f"issues/{number}/comments", {"body": body})


# --- pull requests --------------------------------------------------------------------------------

class Result:
    def __init__(self, group):
        self.group = group
        self.outcome = None
        self.url = None
        self.target = ""
        self.open_target = None  # marker target of the open pull request after this run
        self.has_open_pull = False  # the bot has an open pull request for the group before this run
        self.errors = []
        self.notes = []
        self.operation_failed = False
        self.plan_failed = False
        self.security = None


class Processor:
    def __init__(self, plan_dir, git, github, run_url, workflow_url=None, planned_commit=None):
        self.plan_dir = plan_dir
        self.git = git
        self.github = github
        self.run_url = run_url
        self.workflow_url = workflow_url or run_url
        self.planned_commit = planned_commit
        self.main_sha = None

    @property
    def stale(self):
        """Whether main moved since the planned commit (unknown for plans without one: not stale)."""
        return self.planned_commit is not None and self.planned_commit != self.main_sha

    def process(self, group, rc):
        result = Result(group)
        if rc != 0:
            result.outcome = "failed"
            result.plan_failed = True
            result.errors.append(f"apply exited with {rc}; the group was skipped")
            return result
        try:
            meta = load_metadata(self.plan_dir, group)
            result.security = meta["security"]
            result.target = meta["target"]
            result.errors += [f"apply: {one_line(error)}" for error in meta["errors"]]
            result.notes += [one_line(note) for note in meta["notes"]]
            patch_path = self.plan_dir / f"{group}.patch"
            try:
                patch = patch_path.read_bytes() if meta["changed"] else b""
            except OSError as error:
                raise PlanError(f"cannot read {patch_path.name}: {error}") from error
            if meta["changed"] and not patch.strip():
                raise PlanError("metadata lists changed files but the patch is empty")
            self._process(result, group, meta, patch_path, patch)
        except PlanError as error:
            result.outcome = "failed"
            result.plan_failed = True
            result.errors.append(str(error))
        except OperationError as error:
            result.outcome = "failed"
            result.operation_failed = True
            result.errors.append(str(error))
        return result

    def _process(self, result, group, meta, patch_path, patch):
        branch = f"{BRANCH_PREFIX}{group}"
        pulls = self.github.pulls(branch)
        foreign = next((pull for pull in pulls if pull.get("state") == "open" and not by_bot(pull)), None)
        ours = [pull for pull in pulls if by_bot(pull)]
        open_pull = next((pull for pull in ours if pull.get("state") == "open"), None)
        marker = parse_marker(open_pull.get("body"), group) if open_pull else None
        if open_pull:
            result.url = open_pull.get("html_url")
            result.open_target = marker[0] if marker else None
            result.has_open_pull = True
        if foreign:
            result.outcome = "foreign"
            result.url = foreign.get("html_url")
            result.notes.append(f"pull request #{foreign.get('number')} from {branch} was opened by "
                                f"{(foreign.get('user') or {}).get('login')}; the workflow leaves {branch} alone")
            return
        head = self.git.branch_head(branch)
        if meta["errors"] and (open_pull or head):
            # Partial results must not close, shrink or replace what an earlier complete run opened.
            result.outcome = "kept"
            return

        target_hash = sha256(meta["target"]) if meta["changed"] else None
        if meta["changed"] and open_pull is None:
            declined = [pull for pull in ours if pull.get("state") == "closed" and not pull.get("merged_at")
                        and (parse_marker(pull.get("body"), group) or (None,))[0] == target_hash]
            if declined:
                result.outcome = "suppressed"
                result.url = declined[0].get("html_url")
                return

        maintainers = self.git.maintainer_emails(self.main_sha, head) if head else []
        landed = next((pull for pull in ours if pull.get("merged_at") and (pull.get("head") or {}).get("sha") == head),
                      None) if maintainers else None
        if landed:
            # Merged as it is (squash or rebase leave its commits outside main): nothing on it is unmerged work.
            maintainers = []
            result.notes.append(f"{branch} is the head of the merged pull request #{landed.get('number')}; its "
                                "commits are in main, so the workflow deletes or reuses the branch")
        if maintainers:
            result.outcome = "maintainer"
            result.notes.append(f"commits by {', '.join(maintainers)} on {branch} are never overwritten or deleted")
            if not meta["changed"]:
                result.notes.append("main is already up to date with the upstream versions of this group")
            elif open_pull is None:
                result.notes.append(f"no pull request is opened while {branch} has maintainer commits; delete the "
                                    "branch to let the workflow open one")
            elif result.open_target != target_hash:
                self._comment_newer(open_pull["number"], target_hash, meta["target"])
            return

        if not meta["changed"]:
            if open_pull:
                self.github.comment(open_pull["number"], "main is already up to date with the upstream versions "
                                    f"of this group; closing. ([upstream run]({self.run_url}))")
                self.github.update_pull(open_pull["number"], state="closed")
                result.open_target = None
            if head:
                self.git.delete_branch(branch, head)
            result.outcome = "closed" if open_pull else "nothing"
            return

        patch_hash = sha256(patch)
        title, body = self._title(meta), self._body(group, meta, target_hash, patch_hash)
        if open_pull is None:
            if not self._rebuild(result, branch, head, group, meta, patch_path):
                return
            created = self.github.create_pull(branch, title, body)
            result.url = (created or {}).get("html_url")
            result.outcome = "created"
            result.open_target = target_hash
            return

        number = open_pull["number"]
        if head and not self._conflicting(number) and (
                (marker and marker[1] == patch_hash)
                or self.git.carries(self.main_sha, head, patch_path.resolve(), group, meta["changed"])):
            # Same changes (by the marker, or by content when the marker is gone or the patch was computed against
            # another main): no push. Keep the title and body in step with the plan (e.g. a new security verdict).
            fields = {}
            if open_pull.get("title") != title:
                fields["title"] = title
            if not same_text(open_pull.get("body"), body):
                fields["body"] = body
            if fields:
                self.github.update_pull(number, **fields)
            result.outcome = "edited" if fields else "unchanged"
            result.open_target = target_hash
            return
        if not self._rebuild(result, branch, head, group, meta, patch_path):
            return
        self.github.update_pull(number, title=title, body=body)
        result.outcome = "updated"
        result.open_target = target_hash

    def _conflicting(self, number):
        # REST reports mergeable false for conflicts and null while GitHub computes it (treated as no conflict).
        return (self.github.pull(number) or {}).get("mergeable") is False

    def _comment_newer(self, number, target_hash, target):
        tag = NEWER_MARKER.format(target=target_hash)
        if any(tag in (comment.get("body") or "") for comment in self.github.comments(number) if by_bot(comment)):
            return
        self.github.comment(number, f"{tag}\nNewer upstream versions are available: "
                            f"{neutralize_comments(one_line(target, 1000))}.\n\nThis pull request has commits that "
                            "were not made by the upstream workflow, so it is not updated automatically. Update it "
                            "by hand, or close it and delete its branch: the next run then opens a new pull request "
                            f"for the newer versions. ([upstream run]({self.run_url}))")

    @staticmethod
    def _title(meta):
        return meta["title"]

    def _body(self, group, meta, target_hash, patch_hash):
        body = neutralize_comments(meta["body"]).rstrip()
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY].rstrip() + "\n\n(truncated)"
        # Only stable text: the body is compared with the plan on every run.
        footer = (f"Opened by the [upstream workflow]({self.workflow_url}). It never merges automatically; CI runs "
                  "after \"Approve workflows to run\" on this pull request. The workflow keeps the title and body "
                  "of this pull request up to date until someone pushes a commit to its branch.")
        return f"{body}\n\n{footer}\n\n{format_marker(group, target_hash, patch_hash)}\n"

    def _rebuild(self, result, branch, head, group, meta, patch_path):
        """Commit the patch on main and push it over `head`; False when the plan is stale."""
        message = meta["title"] + "\n"
        if meta["updates"]:
            message += "\n" + "\n".join(meta["updates"]) + "\n"
        try:
            commit = self.git.build_commit(self.main_sha, patch_path.resolve(), group, meta["changed"], message)
        except PatchError as error:
            if not self.stale:
                raise error.failure from error
            result.outcome = "stale"
            result.notes.append(f"main moved from the planned commit {self.planned_commit[:12]} to "
                                f"{self.main_sha[:12]} and the patch no longer applies ({one_line(error, 300)}); the "
                                "next run plans the group again")
            return False
        self.git.push(commit, branch, head)
        return True

    def retire(self, groups):
        """Close the open bot pull requests of upstream/<group> branches whose group the plan no longer lists."""
        results = []
        for pull in self.github.open_pulls():
            ref = pull["head"]["ref"]
            group = ref[len(BRANCH_PREFIX):] if ref.startswith(BRANCH_PREFIX) else ""
            if not by_bot(pull) or not GROUP_RE.match(group) or group in groups:
                continue
            result = Result(group)
            result.url = pull.get("html_url")
            try:
                head = self.git.branch_head(ref)
                maintainers = self.git.maintainer_emails(self.main_sha, head) if head else []
                if maintainers:
                    result.outcome = "maintainer"
                    result.notes.append(f"the plan no longer has the {group} group; commits by "
                                        f"{', '.join(maintainers)} on {ref} are never overwritten or deleted, so the "
                                        "pull request stays open")
                else:
                    self.github.comment(pull["number"], f"The upstream workflow no longer has the `{group}` group "
                                        "(e.g. its version line moved to another major); closing. "
                                        f"([upstream run]({self.run_url}))")
                    self.github.update_pull(pull["number"], state="closed")
                    if head:
                        self.git.delete_branch(ref, head)
                    result.outcome = "retired"
            except OperationError as error:
                result.outcome = "failed"
                result.operation_failed = True
                result.errors.append(str(error))
            results.append(result)
        return results


# --- verdict and summary ----------------------------------------------------------------------------

def verdict(check_rc, groups, results):
    reasons = []
    if check_rc != 0:
        reasons.append(f"the upstream check exited with {check_rc}: upstream or advisory data could not be read")
    inputs = next((result for result in results if result.group == SECURITY_GROUP), None)
    if inputs is None:
        reasons.append(f"the plan has no {SECURITY_GROUP} group, so its security data was not checked")
    else:
        if groups.get(SECURITY_GROUP) != 0 or inputs.plan_failed:
            reasons.append(f"the {SECURITY_GROUP} group could not be planned, so its security data is incomplete")
        security = inputs.security or {}
        if security.get("after"):
            cves = ", ".join(one_line(cve) for cve in security.get("cves") or [] if isinstance(cve, str))
            reasons.append("a security problem remains after the automatic updates"
                           + (f" ({cves})" if cves else ""))
        # A stale plan is recomputed by the next run, against the main that made it stale.
        if (security.get("before") and inputs.outcome != "stale"
                and (inputs.open_target is None or inputs.open_target != sha256(inputs.target))):
            if inputs.outcome == "kept" and inputs.has_open_pull:
                reasons.append(f"lookup errors kept the pull request on upstream/{SECURITY_GROUP} as it was, so it "
                               "is unknown whether it has the security update for the current target")
            elif inputs.outcome == "kept":
                reasons.append(f"lookup errors kept the branch upstream/{SECURITY_GROUP} as it was; it has no open "
                               "pull request, so the security update has no open pull request for the current target")
            elif inputs.outcome == "foreign":
                reasons.append(f"the open pull request on upstream/{SECURITY_GROUP} was not opened by the workflow, "
                               "so the workflow does not provide the security update for the current target")
            else:
                reasons.append(f"a security update has no open pull request on upstream/{SECURITY_GROUP} for the "
                               "current target")
    failed = [result.group for result in results if result.operation_failed]
    if failed:
        reasons.append("git or gh operations failed for " + ", ".join(failed))
    return reasons


def render_summary(check_rc, results, reasons, run_url):
    lines = ["## Upstream pull requests", "", "| Group | Outcome | Pull request | Target |", "| --- | --- | --- | --- |"]
    for result in results:
        link = f"[link]({result.url})" if result.url else ""
        lines.append(f"| {cell(result.group)} | {cell(OUTCOMES.get(result.outcome, result.outcome))} | {link} | "
                     f"{cell(neutralize_comments(one_line(result.target, 300)))} |")
    details = [(result.group, text) for result in results for text in result.errors + result.notes]
    if details:
        lines += ["", "### Details", ""]
        lines += [f"- **{result_group}**: {neutralize_comments(one_line(text, 2500))}" for result_group, text in details]
    lines += ["", f"Upstream check exit code: {check_rc}.", ""]
    if reasons:
        lines += ["### Verdict: failed", ""] + [f"- {reason}" for reason in reasons]
    else:
        lines += ["### Verdict: passed"]
    lines += ["", f"[Workflow run]({run_url})", ""]
    return "\n".join(lines)


def run(plan_dir, git, github, run_url, workflow_url=None):
    """Returns (exit code, Markdown summary)."""
    check_rc, groups, commit = load_status(plan_dir)
    processor = Processor(plan_dir, git, github, run_url, workflow_url, commit)
    results = []
    try:
        processor.main_sha = git.fetch_main()
    except OperationError as error:
        for group in groups:
            result = Result(group)
            result.outcome = "failed"
            result.operation_failed = True
            result.errors.append(str(error))
            results.append(result)
    else:
        results = [processor.process(group, rc) for group, rc in groups.items()]
        try:
            results += processor.retire(groups)
        except OperationError as error:
            result = Result("(no longer planned)")
            result.outcome = "failed"
            result.operation_failed = True
            result.errors.append(str(error))
            results.append(result)
    reasons = verdict(check_rc, groups, results)
    return (1 if reasons else 0), render_summary(check_rc, results, reasons, run_url)


def main(argv=None, gh_command=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("plan_dir", help="directory with status.json, <group>.json and <group>.patch")
    parser.add_argument("--repo-dir", default=".", help="git checkout whose origin receives the branches")
    args = parser.parse_args(argv)
    try:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise InternalError("GITHUB_REPOSITORY must be set to owner/name")
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        run_url = f"{server}/{repository}/actions/runs/{run_id}" if run_id else f"{server}/{repository}/actions"
        workflow_url = f"{server}/{repository}/actions/workflows/{WORKFLOW_FILE}"
        github = GitHub(gh_command or ["gh"], repository)
        code, summary = run(Path(args.plan_dir), Git(os.path.abspath(args.repo_dir)), github, run_url, workflow_url)
    except InternalError as error:
        print(f"error: {error}", file=sys.stderr)
        code, summary = 2, f"## Upstream pull requests\n\nInternal error: {neutralize_comments(one_line(error))}\n"
    except Exception:  # noqa: BLE001 - any other failure is an internal error with exit code 2
        traceback.print_exc()
        code, summary = 2, "## Upstream pull requests\n\nInternal error; see the job log.\n"
    sys.stdout.write(summary)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
