"""scripts/upstream_pr.py against a temporary bare repository as origin and a fake gh; no network access.

Run with python -m unittest discover -s tests/native.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
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


upstream_pr = _module("upstream_pr")

REPOSITORY = "owner/repo"
BOT = "github-actions[bot]"
MAINTAINER = ("Maintainer", "maintainer@example.com")
# Fixed files of the groups the tests use (see upstream_pr.GROUP_FILES).
INPUTS_FILE = "Directory.Build.props"
TOOLS_FILE = "pyproject.toml"
TOOLS_EXTRA = "tests/Directory.Build.targets"
SKIASHARP_FILE = "native/skiasharp/versions.json"

# A stand-in for `gh api` that serves the REST calls of upstream_pr.py from a JSON state file. Like GitHub, the
# pull request list filters head=owner:branch by the owner of the head repository (so a same-owner fork matches),
# closing a pull request records a "closed" issue event by the token's user (the bot), and --paginate --slurp
# returns the pages of state["page_size"] items each.
FAKE_GH = textwrap.dedent(r'''
    import json, os, sys, urllib.parse
    state_path = os.environ["FAKE_GH_STATE"]
    with open(state_path, encoding="utf-8") as stream:
        state = json.load(stream)
    args = sys.argv[1:]
    assert args[0] == "api", args
    method, payload, paginate, path = "GET", None, False, None
    i = 1
    while i < len(args):
        if args[i] == "--method":
            method = args[i + 1]; i += 2
        elif args[i] == "--input":
            with open(args[i + 1], encoding="utf-8") as stream:
                payload = json.load(stream)
            i += 2
        elif args[i] in ("--paginate", "--slurp"):
            paginate = True; i += 1
        else:
            assert path is None and not args[i].startswith("-"), args
            path = args[i]; i += 1
    state["calls"].append({"method": method, "path": path, "payload": payload})
    prefix = "repos/" + state["repo"] + "/"
    assert path.startswith(prefix), path
    route, _, query = path[len(prefix):].partition("?")
    query = dict(urllib.parse.parse_qsl(query))
    parts = route.split("/")

    def finish(data, code=0, error=None):
        with open(state_path, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=1)
        if error:
            sys.stderr.write(error + "\n")
        elif paginate:
            size = state["page_size"]
            sys.stdout.write(json.dumps([data[start:start + size] for start in range(0, len(data), size)] or [[]]))
        else:
            sys.stdout.write(json.dumps(data))
        sys.exit(code)

    def find(number):
        return next(pull for pull in state["pulls"] if pull["number"] == number)

    for rule in state["fail"]:
        if rule["method"] == method and rule["route"] == route:
            finish(None, 1, "gh: " + rule["message"])
    if parts == ["pulls"] and method == "GET":
        pulls = [pull for pull in state["pulls"] if pull["base"]["ref"] == query["base"]
                 and query["state"] in ("all", pull["state"])]
        if "head" in query:
            owner, _, ref = query["head"].partition(":")
            pulls = [pull for pull in pulls
                     if pull["head"]["ref"] == ref and pull["head"]["repo"]["full_name"].split("/")[0] == owner]
        finish(pulls)
    if parts == ["pulls"] and method == "POST":
        if any(pull["state"] == "open" and pull["head"]["ref"] == payload["head"]
               and pull["head"]["repo"]["full_name"] == state["repo"] for pull in state["pulls"]):
            finish(None, 1, "gh: A pull request already exists (HTTP 422)")
        number = 1 + max([pull["number"] for pull in state["pulls"]] + [0])
        pull = {"number": number, "state": "open", "merged_at": None, "title": payload["title"],
                "body": payload["body"], "html_url": "https://github.example/owner/repo/pull/%d" % number,
                "user": {"login": "github-actions[bot]"},
                "head": {"ref": payload["head"], "repo": {"full_name": state["repo"]}},
                "base": {"ref": payload["base"], "repo": {"full_name": state["repo"]}}, "mergeable": None}
        state["pulls"].append(pull)
        finish(pull)
    if parts[0] == "pulls" and len(parts) == 2:
        pull = find(int(parts[1]))
        if method == "PATCH":
            if payload.get("state") == "closed" and pull["state"] != "closed":
                state["clock"] += 1
                state["events"].setdefault(parts[1], []).append({
                    "event": "closed", "actor": {"login": "github-actions[bot]"},
                    "created_at": "2026-01-01T00:%02d:%02dZ" % divmod(state["clock"], 60)})
            pull.update(payload)
        finish(pull)
    if parts[0] == "issues" and parts[2:] == ["comments"]:
        comments = state["comments"].setdefault(parts[1], [])
        if method == "POST":
            comments.append({"body": payload["body"], "user": {"login": "github-actions[bot]"}})
            finish(comments[-1])
        finish(comments)
    if parts[0] == "issues" and parts[2:] == ["events"] and method == "GET":
        finish(state["events"].get(parts[1], []))
    finish(None, 1, "gh: unexpected call %s %s" % (method, path))
''')


def _environment(temp):
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "GITHUB_", "GH_"))}
    environment.update({
        "GIT_CONFIG_GLOBAL": str(temp / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_SERVER_URL": "https://github.example", "GITHUB_RUN_ID": "77",
        "GITHUB_STEP_SUMMARY": str(temp / "summary.md"), "FAKE_GH_STATE": str(temp / "gh.json"),
    })
    return environment


class UpstreamPrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # origin.git with the initial main, and the clones work (for maintainer pushes) and ci (the checkout of
        # the pr job), built once and copied for every test. The clones reach origin by a relative URL.
        cls._template = tempfile.TemporaryDirectory(prefix="upstream-pr-template-")
        cls.addClassCleanup(cls._template.cleanup)
        template = Path(cls._template.name)
        (template / "gitconfig").write_text("", encoding="utf-8")

        def git(cwd, *arguments):
            subprocess.run(["git", *arguments], cwd=cwd, env=_environment(template), capture_output=True,
                           check=True)

        git(template, "init", "--quiet", "--bare", "--initial-branch=main", "origin.git")
        git(template, "init", "--quiet", "--initial-branch=main", "work")
        cls.write(template / "work", {INPUTS_FILE: "dotnet 1.0.0\n", TOOLS_FILE: "task 1.0\n",
                                      SKIASHARP_FILE: "skia 1.0\n", "other.txt": "x\n"})
        git(template / "work", "add", "-A")
        git(template / "work", "-c", f"user.name={MAINTAINER[0]}", "-c", f"user.email={MAINTAINER[1]}",
            "commit", "--quiet", "-m", "Initial commit")
        git(template / "work", "remote", "add", "origin", "../origin.git")
        git(template / "work", "push", "--quiet", "origin", "HEAD:refs/heads/main")
        git(template / "work", "fetch", "--quiet", "origin")
        git(template, "clone", "--quiet", "origin.git", "ci")
        git(template / "ci", "remote", "set-url", "origin", "../origin.git")

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="upstream-pr-test-")
        self.temp = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        (self.temp / "gitconfig").write_text("", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, _environment(self.temp), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        template = Path(self._template.name)
        for name in ("origin.git", "work", "ci"):
            shutil.copytree(template / name, self.temp / name, symlinks=True)
        self.origin, self.work, self.ci = self.temp / "origin.git", self.temp / "work", self.temp / "ci"
        self.plan = self.temp / "plan"
        self.plan.mkdir()
        fake = self.temp / "fake_gh.py"
        fake.write_text(FAKE_GH, encoding="utf-8")
        self.gh_command = [sys.executable, str(fake)]
        # One item per page: every listing goes through the pagination of gh api --paginate --slurp.
        self.gh_state({"repo": REPOSITORY, "pulls": [], "comments": {}, "events": {}, "clock": 0, "calls": [],
                       "fail": [], "page_size": 1})
        # Every push and branch deletion, so that "no push" holds even when a rebuilt commit has the same id.
        self.pushes = []
        for name in ("push", "delete_branch"):
            def record(git, *arguments, real=getattr(upstream_pr.Git, name), name=name):
                self.pushes.append((name, arguments[-2]))
                return real(git, *arguments)
            patcher = mock.patch.object(upstream_pr.Git, name, record)
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- helpers --------------------------------------------------------------------------------

    def git(self, cwd, *arguments, env=None):
        process = subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, env=env,
                                 check=False)
        self.assertEqual(process.returncode, 0, f"git {arguments}: {process.stderr}")
        return process.stdout.strip()

    @staticmethod
    def write(directory, files):
        for name, content in files.items():
            path = Path(directory) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def commit(self, directory, message, author=MAINTAINER):
        env = dict(os.environ, GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1],
                   GIT_COMMITTER_NAME=author[0], GIT_COMMITTER_EMAIL=author[1])
        self.git(directory, "add", "-A")
        self.git(directory, "commit", "--quiet", "-m", message, env=env)

    def gh_state(self, state=None):
        path = self.temp / "gh.json"
        if state is not None:
            path.write_text(json.dumps(state), encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8"))

    def gh_calls(self, method=None):
        return [call for call in self.gh_state()["calls"] if method is None or call["method"] == method]

    def writes(self):
        """gh POST and PATCH calls and git pushes and deletions since the last reset_calls."""
        return self.gh_calls("POST") + self.gh_calls("PATCH") + self.pushes

    def reset_calls(self):
        self.pushes.clear()
        state = self.gh_state()
        state["calls"] = []
        self.gh_state(state)

    def edit_gh(self, edit):
        state = self.gh_state()
        edit(state)
        self.gh_state(state)

    def add_pull(self, ref, user, state="open", body="", repo=REPOSITORY, merged_at=None):
        """A pull request that the workflow did not create (number appended to the fake's list)."""
        def edit(gh):
            number = 1 + max([pull["number"] for pull in gh["pulls"]] + [0])
            gh["pulls"].append({
                "number": number, "state": state, "merged_at": merged_at, "title": "by hand", "body": body,
                "html_url": f"https://github.example/{repo}/pull/{number}", "user": {"login": user},
                "head": {"ref": ref, "repo": {"full_name": repo}},
                "base": {"ref": "main", "repo": {"full_name": REPOSITORY}}, "mergeable": None})
        self.edit_gh(edit)
        return self.gh_state()["pulls"][-1]["number"]

    def main_sha(self):
        return self.git(self.origin, "rev-parse", "refs/heads/main")

    def make_patch(self, files):
        """git diff --binary of the given changes against origin/main, as the plan job records it."""
        scratch = self.work  # left clean on the current main
        self.git(scratch, "fetch", "--quiet", "origin")
        self.git(scratch, "checkout", "--quiet", "--detach", "origin/main")
        self.write(scratch, files)
        self.git(scratch, "add", "-A")
        patch = subprocess.run(["git", "diff", "--cached", "--binary"], cwd=scratch, capture_output=True,
                               check=True).stdout
        self.git(scratch, "reset", "--quiet", "--hard")
        return patch

    def make_index_patch(self, *commands):
        """git diff --binary of the index after the git commands (argument lists) on origin/main."""
        scratch = self.work
        self.git(scratch, "fetch", "--quiet", "origin")
        self.git(scratch, "checkout", "--quiet", "--detach", "origin/main")
        for command in commands:
            self.git(scratch, *command)
        patch = subprocess.run(["git", "diff", "--cached", "--binary"], cwd=scratch, capture_output=True,
                               check=True).stdout
        self.git(scratch, "reset", "--quiet", "--hard")
        return patch

    def set_plan(self, check=0, groups=None, commit="main"):
        """groups: {group: (rc, metadata or None, patch bytes)}; commit: the planned commit ("main": the current
        main, None: an older plan without one)."""
        groups = groups or {}
        for path in self.plan.iterdir():
            path.unlink()
        status = {"check": check, "groups": {group: rc for group, (rc, _, _) in groups.items()}}
        if commit is not None:
            status["commit"] = self.main_sha() if commit == "main" else commit
        (self.plan / "status.json").write_text(json.dumps(status), encoding="utf-8")
        for group, (_, meta, patch) in groups.items():
            if meta is not None:
                (self.plan / f"{group}.json").write_text(json.dumps(meta), encoding="utf-8")
                (self.plan / f"{group}.patch").write_bytes(patch)

    def group(self, group, version, path=None, security=None, rc=0, title=None, errors=(), extra=None):
        """Plan entry updating <path> (the group's file in this repository) to version; `extra` adds more
        {path: content} changes to the patch and the changed files."""
        if path is None:
            path = {"inputs": INPUTS_FILE, "tools": TOOLS_FILE}.get(group, SKIASHARP_FILE)
        if version is None:
            meta = {"group": group, "changed": [], "target": "", "title": "", "body": "", "updates": [],
                    "notes": [], "errors": list(errors), "security": security}
            return group, (rc, meta, b"")
        name = {"inputs": "dotnet", "tools": "task"}.get(group, "skia")
        files = {path: f"{name} {version}\n", **(extra or {})}
        meta = {"group": group, "changed": sorted(files), "target": f"{name} {version}",
                "title": title or f"build(deps): update {name} to {version}",
                "body": f"Updates {name} to {version}.", "updates": [f"{name} 1.0 -> {version}"], "notes": [],
                "errors": list(errors), "security": security}
        return group, (rc, meta, self.make_patch(files))

    def plan_groups(self, *entries, check=0, commit="main"):
        self.set_plan(check, dict(entries), commit=commit)

    def run_pr(self, run_id="77"):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": run_id}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = upstream_pr.main([str(self.plan), "--repo-dir", str(self.ci)], gh_command=self.gh_command)
        return code, out.getvalue()

    def branch(self, group):
        output = self.git(self.temp, "ls-remote", str(self.origin), f"refs/heads/upstream/{group}")
        return output.split("\t")[0] if output else None

    def pulls(self, group=None):
        return [pull for pull in self.gh_state()["pulls"]
                if group is None or pull["head"]["ref"] == f"upstream/{group}"]

    def show(self, ref, path):
        return self.git(self.origin, "show", f"{ref}:{path}")

    def maintainer_commit(self, group, files):
        self.git(self.work, "fetch", "--quiet", "origin")
        self.git(self.work, "checkout", "--quiet", "--detach", f"origin/upstream/{group}")
        self.write(self.work, files)
        self.commit(self.work, "Maintainer fix")
        self.git(self.work, "push", "--quiet", "origin", f"HEAD:refs/heads/upstream/{group}")
        return self.branch(group)

    def open_tools_pull(self):
        """The bot's pull request for tools 2.0, with inputs up to date; returns the branch head."""
        self.plan_groups(self.group("tools", "2.0"), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        return self.branch("tools")

    # --- marker ---------------------------------------------------------------------------------

    def test_body_from_the_plan_cannot_carry_a_marker(self):
        _, (rc, meta, patch) = self.group("tools", "2.0")
        meta["body"] = upstream_pr.format_marker("tools", "0" * 64, "0" * 64)
        self.set_plan(groups={"tools": (rc, meta, patch)})
        self.run_pr()
        body = self.pulls("tools")[0]["body"]
        self.assertEqual(len(upstream_pr.MARKER_RE.findall(body)), 1)
        self.assertNotEqual(upstream_pr.parse_marker(body, "tools")[0], "0" * 64)

    # --- rules ----------------------------------------------------------------------------------

    def test_maintainer_commits_are_never_overwritten_and_newer_target_is_commented_once(self):
        self.open_tools_pull()
        head = self.maintainer_commit("tools", {"other.txt": "fixed\n"})
        self.reset_calls()
        code, summary = self.run_pr()  # same target: no comment, no push
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.branch("tools"), head)
        self.assertEqual(self.writes(), [])
        self.assertIn("| tools | has maintainer commits |", summary)
        self.assertIn("maintainer@example.com", summary)

        self.plan_groups(self.group("tools", "2.1"), self.group("inputs", None))
        for _ in range(2):
            code, summary = self.run_pr()
            self.assertEqual(code, 0, summary)
        self.assertEqual(self.branch("tools"), head)
        comments = self.gh_state()["comments"]["1"]
        self.assertEqual(len(comments), 1)
        self.assertIn("task 2.1", comments[0]["body"])
        self.assertIn(upstream_pr.sha256("task 2.1"), comments[0]["body"])

        self.plan_groups(self.group("tools", "2.2"), self.group("inputs", None))
        self.run_pr()
        self.assertEqual(len(self.gh_state()["comments"]["1"]), 2)
        self.assertEqual(self.branch("tools"), head)

    def test_amended_bot_commit_with_maintainer_committer_is_not_overwritten(self):
        self.open_tools_pull()
        self.git(self.work, "fetch", "--quiet", "origin")
        self.git(self.work, "checkout", "--quiet", "--detach", "origin/upstream/tools")
        self.write(self.work, {TOOLS_FILE: "task 2.0\n# reviewed\n"})
        self.git(self.work, "add", "-A")
        env = dict(os.environ, GIT_COMMITTER_NAME=MAINTAINER[0], GIT_COMMITTER_EMAIL=MAINTAINER[1])
        self.git(self.work, "commit", "--quiet", "--amend", "--no-edit", env=env)
        self.git(self.work, "push", "--quiet", "--force", "origin", "HEAD:refs/heads/upstream/tools")
        head = self.branch("tools")
        self.assertEqual(self.git(self.origin, "log", "-1", "--format=%ae", head), upstream_pr.BOT_EMAIL)

        self.plan_groups(self.group("tools", "2.1"), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.branch("tools"), head)
        self.assertIn("| tools | has maintainer commits |", summary)
        self.assertEqual(self.gh_calls("PATCH"), [])
        self.assertEqual(len(self.gh_state()["comments"]["1"]), 1)

    def test_maintainer_branch_without_open_pull_request_is_neither_pushed_nor_deleted(self):
        self.open_tools_pull()
        self.edit_gh(lambda state: state["pulls"][0].update(state="closed"))
        head = self.maintainer_commit("tools", {"other.txt": "fixed\n"})
        self.plan_groups(self.group("tools", "2.1"), self.group("inputs", None))  # not the declined target
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.branch("tools"), head)
        self.assertEqual(self.writes(), [])
        self.assertIn("| tools | has maintainer commits |", summary)
        self.assertIn("delete the branch", summary)

        self.plan_groups(self.group("tools", None), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.branch("tools"), head)

    def test_nothing_changed_keeps_pull_request_with_maintainer_commits(self):
        self.open_tools_pull()
        head = self.maintainer_commit("tools", {"other.txt": "fixed\n"})
        self.plan_groups(self.group("tools", None), self.group("inputs", None))
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.branch("tools"), head)
        self.assertEqual(self.pulls("tools")[0]["state"], "open")
        self.assertEqual(self.writes(), [])
        self.assertIn("the plan has no changes for this group", summary)

    def test_pull_requests_not_opened_by_the_bot(self):
        # A closed pull request with the bot's marker does not suppress when someone else opened it.
        marker = upstream_pr.format_marker("tools", upstream_pr.sha256("task 2.0"), "0" * 64)
        self.add_pull("upstream/tools", "someone", state="closed", body=marker)
        head = self.open_tools_pull()
        self.assertEqual([pull["state"] for pull in self.pulls("tools")], ["closed", "open"])
        # An open one is someone else's pull request on the branch: never pushed, edited or closed.
        self.edit_gh(lambda state: state["pulls"][1].update(state="closed"))
        number = self.add_pull("upstream/tools", "someone", body=marker)
        for plan in (self.group("tools", "2.1"), self.group("tools", None)):
            self.plan_groups(plan, self.group("inputs", None))
            self.reset_calls()
            code, summary = self.run_pr()
            self.assertEqual(code, 0, summary)
            self.assertEqual(self.branch("tools"), head)
            self.assertEqual(self.writes(), [])
            self.assertIn(f"| tools | pull request not opened by the workflow | [link](https://github.example/owner/"
                          f"repo/pull/{number})", summary)

    def test_pull_requests_from_forks_are_ignored(self):
        # Same owner (returned for head=owner:branch) and another owner (not returned); both look like the bot's.
        _, (_, meta, patch) = self.group("tools", "2.0")
        marker = upstream_pr.format_marker("tools", upstream_pr.sha256(meta["target"]), upstream_pr.sha256(patch))
        self.add_pull("upstream/tools", BOT, body=marker, repo="owner/fork")
        self.add_pull("upstream/tools", BOT, body=marker, repo="other/repo")
        self.add_pull("upstream/gone", BOT, body=marker, repo="owner/fork")
        self.set_plan(groups={"tools": (0, meta, patch), "inputs": self.group("inputs", None)[1]})
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | created | [link](https://github.example/owner/repo/pull/4)", summary)
        self.assertEqual(self.show("refs/heads/upstream/tools", TOOLS_FILE), "task 2.0")
        self.assertEqual([pull["state"] for pull in self.gh_state()["pulls"]], ["open"] * 4)
        self.assertNotIn("gone", summary)

    def test_push_is_refused_when_branch_moved_since_it_was_read(self):
        self.open_tools_pull()
        self.plan_groups(self.group("tools", "2.1"), self.group("inputs", None))
        real_head = upstream_pr.Git.branch_head
        moved = {}

        def branch_head(git, branch):
            sha = real_head(git, branch)
            if not moved:  # someone pushes between the read and the push
                moved["sha"] = self.maintainer_commit("tools", {"other.txt": "racing\n"})
            return sha

        with mock.patch.object(upstream_pr.Git, "branch_head", branch_head):
            code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertEqual(self.branch("tools"), moved["sha"])
        self.assertIn("git or gh operations failed for tools", summary)

    # --- fixed files ----------------------------------------------------------------------------

    def test_allowlist_covers_exactly_what_upstream_py_stages(self):
        upstream = _module("upstream")
        groups = [*upstream.group_names(ROOT), "skiasharp-1", "skiasharp-99"]
        self.assertIn("skiasharp-3", groups)
        for group in groups:
            self.assertEqual(set(upstream.staged_files(group, ROOT)), set(upstream_pr.group_files(group)), group)
        for group in ("unknown", "skiasharp-", "skiasharp-03", "tools-2"):
            self.assertEqual(upstream_pr.group_files(group), frozenset(), group)

    def test_metadata_listing_files_outside_the_group_fails(self):
        _, (rc, meta, _) = self.group("tools", "2.0")
        meta["changed"] = [TOOLS_FILE, "other.txt"]
        patch = self.make_patch({TOOLS_FILE: "task 2.0\n", "other.txt": "sneaky\n"})
        self.set_plan(groups={"tools": (rc, meta, patch), "inputs": self.group("inputs", None)[1]})
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | failed |", summary)
        # Rejected from the metadata, before the patch is applied anywhere.
        self.assertIn("the plan changes files outside the fixed files of the tools group: other.txt", summary)
        self.assertIsNone(self.branch("tools"))

    def test_patch_outside_the_group_or_the_metadata_fails(self):
        cases = (
            # A file of another group, not listed in the metadata.
            ({TOOLS_FILE: "task 2.0\n", INPUTS_FILE: "dotnet 9\n"}, [TOOLS_FILE],
             f"outside the fixed files of the tools group: {INPUTS_FILE}"),
            # A fixed file of the group, not listed in the metadata.
            ({TOOLS_FILE: "task 2.0\n", TOOLS_EXTRA: "<Project />\n"}, [TOOLS_FILE],
             f"the patch changes files the metadata does not list: {TOOLS_EXTRA}"),
            # A listed file the patch does not change: the patch must change exactly the listed files.
            ({TOOLS_FILE: "task 2.0\n"}, [TOOLS_EXTRA, TOOLS_FILE],
             f"the patch does not change files the metadata lists: {TOOLS_EXTRA}"),
        )
        for files, changed, message in cases:
            _, (rc, meta, _) = self.group("tools", "2.0")
            meta["changed"] = changed
            self.set_plan(groups={"tools": (rc, meta, self.make_patch(files)),
                                  "inputs": self.group("inputs", None)[1]})
            code, summary = self.run_pr()
            self.assertEqual(code, 0, summary)
            self.assertIn(message, summary)
            self.assertIn("| tools | failed |", summary)
            self.assertIsNone(self.branch("tools"))

    def assert_only_regular_files(self, path, detail, *commands):
        """A patch made by the git commands on the index, changing the fixed file `path`, fails the group."""
        patch = self.make_index_patch(*commands)
        self.assertTrue(patch.strip())
        _, (rc, meta, _) = self.group("tools", "2.0")
        meta["changed"] = [path]
        self.set_plan(groups={"tools": (rc, meta, patch), "inputs": self.group("inputs", None)[1]})
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | failed |", summary)
        self.assertIn(f"the patch may only add or modify regular files: {path} ({detail})", summary)
        self.assertIsNone(self.branch("tools"))
        self.assertEqual(self.writes(), [])

    def test_patch_deleting_a_file_fails(self):
        self.assert_only_regular_files(TOOLS_FILE, "D 000000", ["rm", "--quiet", "--cached", TOOLS_FILE])

    def test_patch_changing_only_a_file_mode_fails(self):
        self.assert_only_regular_files(TOOLS_FILE, "M 100755", ["update-index", "--chmod=+x", TOOLS_FILE])

    def test_patch_adding_a_symbolic_link_fails(self):
        target = self.temp / "link-target.txt"
        target.write_bytes(b"../pyproject.toml")
        blob = self.git(self.work, "hash-object", "-w", str(target))
        self.assert_only_regular_files(TOOLS_EXTRA, "A 120000",
                                       ["update-index", "--add", "--cacheinfo", f"120000,{blob},{TOOLS_EXTRA}"])

    def test_internal_error_for_invalid_group_name(self):
        (self.plan / "status.json").write_text(json.dumps({"check": 0, "groups": {"../x": 0}}), encoding="utf-8")
        code, _ = self.run_pr()
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
