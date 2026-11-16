"""scripts/upstream_pr.py against a temporary bare repository as origin and a fake gh; no network access. Also the
plan step of .github/workflows/upstream.yml with a stub scripts/upstream.py, when bash is available.

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
# and --paginate --slurp returns the pages of state["page_size"] items each.
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
            pull.update(payload)
        finish(pull)
    if parts[0] == "issues" and parts[2:] == ["comments"]:
        comments = state["comments"].setdefault(parts[1], [])
        if method == "POST":
            comments.append({"body": payload["body"], "user": {"login": "github-actions[bot]"}})
            finish(comments[-1])
        finish(comments)
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
        self.gh_state({"repo": REPOSITORY, "pulls": [], "comments": {}, "calls": [], "fail": [], "page_size": 1})
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

    def move_main(self, files):
        self.git(self.work, "fetch", "--quiet", "origin")
        self.git(self.work, "checkout", "--quiet", "--detach", "origin/main")
        self.write(self.work, files)
        self.commit(self.work, "Move main")
        self.git(self.work, "push", "--quiet", "origin", "HEAD:refs/heads/main")

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

    def test_marker_round_trip_and_parsing(self):
        target, patch = "a" * 64, "b" * 64
        marker = upstream_pr.format_marker("skiasharp-3", target, patch)
        self.assertEqual(marker, f"<!-- hostforge-upstream group=skiasharp-3 target={target} patch={patch} -->")
        self.assertEqual(upstream_pr.parse_marker(f"text\n{marker}\n", "skiasharp-3"), (target, patch))
        self.assertIsNone(upstream_pr.parse_marker(marker, "skiasharp-4"))
        self.assertIsNone(upstream_pr.parse_marker(None, "inputs"))
        self.assertIsNone(upstream_pr.parse_marker(marker.replace("a" * 64, "a" * 63), "skiasharp-3"))
        self.assertIsNone(upstream_pr.parse_marker(marker.replace("-->", "->"), "skiasharp-3"))
        later = upstream_pr.format_marker("skiasharp-3", "c" * 64, "d" * 64)
        self.assertEqual(upstream_pr.parse_marker(f"{marker}\n{later}", "skiasharp-3"), ("c" * 64, "d" * 64))

    def test_body_from_the_plan_cannot_carry_a_marker(self):
        _, (rc, meta, patch) = self.group("tools", "2.0")
        meta["body"] = upstream_pr.format_marker("tools", "0" * 64, "0" * 64)
        self.set_plan(groups={"tools": (rc, meta, patch)})
        self.run_pr()
        body = self.pulls("tools")[0]["body"]
        self.assertEqual(len(upstream_pr.MARKER_RE.findall(body)), 1)
        self.assertNotEqual(upstream_pr.parse_marker(body, "tools")[0], "0" * 64)

    # --- rules ----------------------------------------------------------------------------------

    def test_creates_branch_commit_and_pull_request(self):
        self.plan_groups(self.group("inputs", "1.0.1"), self.group("tools", "2.0"))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        for group, path, content in (("inputs", INPUTS_FILE, "dotnet 1.0.1"), ("tools", TOOLS_FILE, "task 2.0")):
            ref = f"refs/heads/upstream/{group}"
            self.assertEqual(self.show(ref, path), content)
            self.assertEqual(self.git(self.origin, "rev-parse", f"{ref}~1"), self.main_sha())
            self.assertEqual(self.git(self.origin, "log", "-1", "--format=%an <%ae> %cn <%ce>", ref),
                             f"{BOT} <{upstream_pr.BOT_EMAIL}> {BOT} <{upstream_pr.BOT_EMAIL}>")
            pull = self.pulls(group)[0]
            meta = json.loads((self.plan / f"{group}.json").read_text(encoding="utf-8"))
            self.assertEqual(pull["title"], meta["title"])
            self.assertEqual(self.git(self.origin, "log", "-1", "--format=%B", ref),
                             f"{meta['title']}\n\n{meta['updates'][0]}")
            patch = (self.plan / f"{group}.patch").read_bytes()
            self.assertEqual(upstream_pr.parse_marker(pull["body"], group),
                             (upstream_pr.sha256(meta["target"]), upstream_pr.sha256(patch)))
            self.assertIn(meta["body"], pull["body"])
            # The body links the workflow, not the run: it must stay equal from run to run.
            self.assertIn("https://github.example/owner/repo/actions/workflows/upstream.yml", pull["body"])
            self.assertNotIn("/runs/77", pull["body"])
        self.assertIn("| inputs | created | [link](https://github.example/owner/repo/pull/1)", summary)
        self.assertIn("### Verdict: passed", summary)
        self.assertEqual(summary, (self.temp / "summary.md").read_text(encoding="utf-8"))

    def test_patch_adding_a_new_file(self):
        self.plan_groups(self.group("tools", "2.0", extra={TOOLS_EXTRA: "<Project />\n"}), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.show("refs/heads/upstream/tools", TOOLS_EXTRA), "<Project />")
        self.assertEqual(self.show("refs/heads/upstream/tools", TOOLS_FILE), "task 2.0")
        self.assertIn("| tools | created |", summary)

    def test_apply_failure_skips_group_and_only_inputs_fail_the_verdict(self):
        self.plan_groups(self.group("inputs", "1.0.1"), ("tools", (2, None, b"")))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIsNone(self.branch("tools"))
        self.assertEqual(self.pulls("tools"), [])
        self.assertIn("| tools | failed |", summary)
        self.assertIn("apply exited with 2", summary)

        self.plan_groups(("inputs", (2, None, b"")), self.group("tools", "2.0"))
        code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertIn("the inputs group could not be planned", summary)

    def test_nothing_changed_closes_pull_request_and_deletes_branch(self):
        self.open_tools_pull()
        self.plan_groups(self.group("tools", None), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        pull = self.pulls("tools")[0]
        self.assertEqual(pull["state"], "closed")
        self.assertIn("main is already up to date", self.gh_state()["comments"][str(pull["number"])][0]["body"])
        self.assertIsNone(self.branch("tools"))
        self.assertIn("| tools | closed (main is up to date) |", summary)
        self.assertIn("| inputs | nothing to do |", summary)
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.writes(), [])

    def test_declined_target_is_suppressed_until_a_newer_target(self):
        before = self.open_tools_pull()
        self.edit_gh(lambda state: state["pulls"][0].update(state="closed"))
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.branch("tools"), before)
        self.assertEqual(self.writes(), [])
        self.assertIn("| tools | suppressed (declined target) |", summary)

        self.plan_groups(self.group("tools", "2.1"), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual([pull["state"] for pull in self.pulls("tools")], ["closed", "open"])
        self.assertEqual(self.show("refs/heads/upstream/tools", TOOLS_FILE), "task 2.1")

    def test_merged_pull_request_does_not_suppress(self):
        self.open_tools_pull()
        self.edit_gh(lambda state: state["pulls"][0].update(state="closed", merged_at="2026-01-01T00:00:00Z"))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual([pull["state"] for pull in self.pulls("tools")], ["closed", "open"])

    def test_listings_span_several_pages(self):
        self.open_tools_pull()
        declined = upstream_pr.format_marker("tools", upstream_pr.sha256("task 2.0"), "0" * 64)
        self.edit_gh(lambda state: state["pulls"][0].update(state="closed"))
        # The declined pull request comes last, after other closed ones: one item per page puts it on page 4.
        self.edit_gh(lambda state: state["pulls"].reverse())
        self.add_pull("upstream/tools", BOT, state="closed",
                      body=upstream_pr.format_marker("tools", upstream_pr.sha256("task 1.9"), "0" * 64))
        self.add_pull("upstream/tools", "someone", state="closed", body=declined)
        self.add_pull("upstream/tools", BOT, state="closed", merged_at="2026-01-01T00:00:00Z", body=declined)
        self.edit_gh(lambda state: state["pulls"].reverse())
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | suppressed (declined target) |", summary)
        self.assertEqual(self.writes(), [])

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
        self.assertIn("main is already up to date", summary)

    def land_taken_over_tools_pull(self):
        """The bot's tools pull request, taken over by a maintainer and squash-merged without deleting its branch;
        returns the branch head (the head of the merged pull request)."""
        self.open_tools_pull()
        head = self.maintainer_commit("tools", {"other.txt": "fixed\n"})
        self.move_main({TOOLS_FILE: "task 2.0\n", "other.txt": "fixed\n"})  # the squash commit
        self.edit_gh(lambda state: state["pulls"][0].update(state="closed", merged_at="2026-01-01T00:00:00Z"))
        self.edit_gh(lambda state: state["pulls"][0]["head"].update(sha=head))
        self.assertNotEqual(self.git(self.origin, "rev-list", "--count", f"main..{head}"), "0")
        return head

    def test_landed_maintainer_branch_is_rebuilt_for_a_new_target(self):
        head = self.land_taken_over_tools_pull()
        self.plan_groups(self.group("tools", "2.1"), self.group("inputs", None))
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | created |", summary)
        self.assertIn("is the head of the merged pull request #1", summary)
        self.assertNotEqual(self.branch("tools"), head)
        self.assertEqual(self.git(self.origin, "rev-parse", "refs/heads/upstream/tools~1"), self.main_sha())
        self.assertEqual(self.show("refs/heads/upstream/tools", TOOLS_FILE), "task 2.1")
        self.assertEqual([pull["state"] for pull in self.pulls("tools")], ["closed", "open"])
        self.assertEqual(self.pushes, [("push", "upstream/tools")])

    def test_landed_maintainer_branch_is_deleted_when_main_is_up_to_date(self):
        self.land_taken_over_tools_pull()
        self.plan_groups(self.group("tools", None), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | nothing to do |", summary)
        self.assertIsNone(self.branch("tools"))
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.writes(), [])

    def test_commit_pushed_after_the_merge_protects_the_branch_again(self):
        self.land_taken_over_tools_pull()
        head = self.maintainer_commit("tools", {"other.txt": "after the merge\n"})
        for plan in (self.group("tools", "2.1"), self.group("tools", None)):
            self.plan_groups(plan, self.group("inputs", None))
            self.reset_calls()
            code, summary = self.run_pr()
            self.assertEqual(code, 0, summary)
            self.assertEqual(self.branch("tools"), head)
            self.assertEqual(self.writes(), [])
            self.assertIn("| tools | has maintainer commits |", summary)
            self.assertNotIn("merged pull request", summary)

    def test_merged_pull_request_from_a_fork_or_by_someone_else_does_not_land_the_branch(self):
        head = self.land_taken_over_tools_pull()
        self.edit_gh(lambda state: state["pulls"][0].update(user={"login": "someone"}))
        self.add_pull("upstream/tools", BOT, state="closed", merged_at="2026-01-01T00:00:00Z", repo="owner/fork")
        self.edit_gh(lambda state: state["pulls"][-1]["head"].update(sha=head))
        self.plan_groups(self.group("tools", "2.1"), self.group("inputs", None))
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.branch("tools"), head)
        self.assertEqual(self.writes(), [])
        self.assertIn("| tools | has maintainer commits |", summary)

    def test_newer_comment_planted_by_another_user_does_not_count(self):
        self.open_tools_pull()
        self.maintainer_commit("tools", {"other.txt": "fixed\n"})
        tag = upstream_pr.NEWER_MARKER.format(target=upstream_pr.sha256("task 2.1"))
        self.edit_gh(lambda state: state["comments"].setdefault("1", []).extend(
            [{"body": "unrelated", "user": {"login": BOT}}, {"body": tag, "user": {"login": "someone"}}]))
        self.plan_groups(self.group("tools", "2.1"), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        comments = self.gh_state()["comments"]["1"]
        self.assertEqual(len(comments), 3)
        self.assertEqual(comments[-1]["user"]["login"], BOT)
        self.assertIn(tag, comments[-1]["body"])
        self.run_pr()  # the bot's own comment counts: page 3 of the comments
        self.assertEqual(len(self.gh_state()["comments"]["1"]), 3)

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

    def test_unchanged_patch_is_not_pushed_again(self):
        head = self.open_tools_pull()
        for run_id, mergeable in (("78", None), ("79", True)):
            self.edit_gh(lambda state: state["pulls"][0].update(mergeable=mergeable))
            self.reset_calls()
            code, summary = self.run_pr(run_id)
            self.assertEqual(code, 0, summary)
            self.assertEqual(self.branch("tools"), head)
            self.assertEqual(self.writes(), [])
            self.assertIn("| tools | unchanged |", summary)

    def test_same_patch_with_new_target_only_edits_the_pull_request(self):
        head = self.open_tools_pull()
        _, (rc, meta, patch) = self.group("tools", "2.0")
        meta["target"] = "task 2.0 (rebuilt)"
        self.set_plan(groups={"tools": (rc, meta, patch), "inputs": self.group("inputs", None)[1]})
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.branch("tools"), head)
        self.assertEqual([call["payload"].keys() for call in self.gh_calls("PATCH")], [{"body"}])
        self.assertEqual(self.gh_calls("POST") + self.pushes, [])
        self.assertEqual(upstream_pr.parse_marker(self.pulls("tools")[0]["body"], "tools")[0],
                         upstream_pr.sha256("task 2.0 (rebuilt)"))
        self.assertIn("| tools | title and body updated |", summary)

    def test_title_and_body_drift_is_edited_without_a_push(self):
        head = self.open_tools_pull()
        expected = self.pulls("tools")[0]
        # A security verdict appears while target and patch stay the same: only the title changes.
        _, (rc, meta, patch) = self.group("tools", "2.0", title="fix(deps): update task to 2.0 (security)")
        self.set_plan(groups={"tools": (rc, meta, patch), "inputs": self.group("inputs", None)[1]})
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual([call["payload"] for call in self.gh_calls("PATCH")],
                         [{"title": "fix(deps): update task to 2.0 (security)"}])
        self.assertEqual(self.branch("tools"), head)
        self.assertEqual(self.pushes, [])
        # A body edited by hand on a bot-only pull request (marker gone) is restored, still without a push: the
        # branch content is compared with the patch. CRLF line breaks alone are no difference.
        self.edit_gh(lambda state: state["pulls"][0].update(body="edited by hand"))
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertIn("| tools | title and body updated |", summary)
        self.assertEqual(self.pulls("tools")[0]["body"], expected["body"])
        self.assertEqual([list(call["payload"]) for call in self.gh_calls("PATCH")], [["body"]])
        self.assertEqual(self.pushes, [])
        self.edit_gh(lambda state: state["pulls"][0].update(body=expected["body"].replace("\n", "\r\n")))
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(self.writes(), [])
        self.assertIn("| tools | unchanged |", summary)
        self.assertEqual(self.branch("tools"), head)

    def test_main_moved_within_the_same_file_only_edits_the_body(self):
        lines = "".join(f"line {number}\n" for number in range(20))
        self.move_main({TOOLS_FILE: "task 1.0\n" + lines})

        def plan():
            _, (rc, meta, _) = self.group("tools", "2.0")
            current = self.show("refs/heads/main", TOOLS_FILE) + "\n"
            patch = self.make_patch({TOOLS_FILE: current.replace("task 1.0", "task 2.0")})
            self.set_plan(groups={"tools": (rc, meta, patch), "inputs": self.group("inputs", None)[1]})
            return patch

        old_patch = plan()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        head = self.branch("tools")
        # Main changes the end of the file, outside the context of the patch: no conflict, but another patch.
        self.move_main({TOOLS_FILE: "task 1.0\n" + lines.replace("line 19", "line 19 on main")})
        self.assertNotEqual(plan(), old_patch)
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | title and body updated |", summary)
        self.assertEqual([list(call["payload"]) for call in self.gh_calls("PATCH")], [["body"]])
        self.assertEqual(self.gh_calls("POST") + self.pushes, [])
        self.assertEqual(self.branch("tools"), head)
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | unchanged |", summary)
        self.assertEqual(self.writes(), [])

    def test_branch_with_a_binary_new_file_is_compared_by_content(self):
        binary = b"\0\1\2\3binary\xff\xfe"
        self.plan_groups(self.group("tools", "2.0", extra={TOOLS_EXTRA: binary}), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn(b"GIT binary patch", (self.plan / "tools.patch").read_bytes())
        head = self.branch("tools")
        expected = self.pulls("tools")[0]["body"]
        # The marker is gone, so the branch content decides: the same changes, only the body is restored.
        self.edit_gh(lambda state: state["pulls"][0].update(body="edited by hand"))
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | title and body updated |", summary)
        self.assertEqual([call["payload"] for call in self.gh_calls("PATCH")], [{"body": expected}])
        self.assertEqual(self.gh_calls("POST") + self.pushes, [])
        self.assertEqual(self.branch("tools"), head)
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | unchanged |", summary)
        self.assertEqual(self.writes(), [])

    def test_conflicting_pull_request_is_rebuilt_on_main(self):
        head = self.open_tools_pull()
        self.move_main({"other.txt": "main moved\n"})
        self.edit_gh(lambda state: state["pulls"][0].update(mergeable=False))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertNotEqual(self.branch("tools"), head)
        self.assertEqual(self.git(self.origin, "rev-parse", "refs/heads/upstream/tools~1"), self.main_sha())
        self.assertIn("| tools | updated |", summary)
        self.assertEqual(len(self.pulls("tools")), 1)

    def test_new_patch_updates_branch_and_pull_request(self):
        self.open_tools_pull()
        self.plan_groups(self.group("tools", "2.1", title="build(deps): update task to 2.1"),
                         self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.show("refs/heads/upstream/tools", TOOLS_FILE), "task 2.1")
        self.assertEqual(self.git(self.origin, "rev-list", "--count", "main..refs/heads/upstream/tools"), "1")
        pull = self.pulls("tools")[0]
        self.assertEqual(pull["title"], "build(deps): update task to 2.1")
        self.assertEqual(upstream_pr.parse_marker(pull["body"], "tools")[0], upstream_pr.sha256("task 2.1"))
        self.assertIn("| tools | updated |", summary)

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

    def test_create_refused_points_to_the_repository_setting(self):
        self.edit_gh(lambda state: state["fail"].append({
            "method": "POST", "route": "pulls",
            "message": "GitHub Actions is not permitted to create or approve pull requests. (HTTP 403)"}))
        self.plan_groups(self.group("tools", "2.0"), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertIn("Allow GitHub Actions to create and approve pull requests", summary)
        self.assertIn("not permitted to create", summary)

    # --- lookup errors --------------------------------------------------------------------------

    def test_lookup_errors_keep_the_open_pull_request(self):
        self.plan_groups(self.group("tools", "2.0", extra={TOOLS_EXTRA: "<Project />\n"}), self.group("inputs", None))
        self.run_pr()
        head = self.branch("tools")
        error = "TUnit: GET https://api.nuget.org/v3-flatcontainer/tunit/index.json: HTTP 503 Service Unavailable"
        # Nothing left, or only part of the changes: neither closes, deletes, rebuilds nor edits.
        for plan in (self.group("tools", None, errors=[error]), self.group("tools", "2.0", errors=[error]),
                     self.group("tools", "2.1", errors=[error])):
            self.plan_groups(plan, self.group("inputs", None))
            self.reset_calls()
            code, summary = self.run_pr()
            self.assertEqual(code, 0, summary)
            self.assertEqual(self.branch("tools"), head)
            self.assertEqual(self.pulls("tools")[0]["state"], "open")
            self.assertEqual(self.writes(), [])
            self.assertIn("| tools | kept: lookup errors | [link](https://github.example/owner/repo/pull/1)", summary)
            self.assertIn(error, summary)

    def test_lookup_errors_keep_a_branch_without_open_pull_request(self):
        head = self.open_tools_pull()
        self.edit_gh(lambda state: state["pulls"][0].update(state="closed"))
        for plan in (self.group("tools", None, errors=["conan: timeout"]),
                     self.group("tools", "2.1", errors=["conan: timeout"])):
            self.plan_groups(plan, self.group("inputs", None))
            self.reset_calls()
            code, summary = self.run_pr()
            self.assertEqual(code, 0, summary)
            self.assertEqual(self.branch("tools"), head)
            self.assertEqual(self.writes(), [])
            self.assertIn("| tools | kept: lookup errors |", summary)

    def test_lookup_errors_without_branch_or_pull_request_open_one(self):
        self.plan_groups(self.group("tools", "2.0", errors=["conan: timeout"]), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.show("refs/heads/upstream/tools", TOOLS_FILE), "task 2.0")
        self.assertIn("| tools | created |", summary)
        self.assertIn("conan: timeout", summary)
        self.plan_groups(self.group("tools", None, errors=["conan: timeout"]), self.group("inputs", None))
        self.edit_gh(lambda state: state["pulls"].clear())
        self.git(self.work, "push", "--quiet", "origin", ":refs/heads/upstream/tools")
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | nothing to do |", summary)

    def test_verdict_security_update_kept_by_lookup_errors(self):
        security = {"before": True, "after": False, "cves": []}
        self.plan_groups(self.group("inputs", "1.0.1", security=security))
        self.run_pr()
        self.plan_groups(self.group("inputs", "1.0.1", security=security, errors=["Avalonia: HTTP 503"]))
        code, summary = self.run_pr()  # the kept pull request has the current target
        self.assertEqual(code, 0, summary)
        self.plan_groups(self.group("inputs", "1.0.2", security=security, errors=["Avalonia: HTTP 503"]))
        code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertIn("lookup errors kept the pull request on upstream/inputs", summary)

    def test_verdict_security_update_with_only_a_branch_kept_by_lookup_errors(self):
        security = {"before": True, "after": False, "cves": []}
        self.plan_groups(self.group("inputs", "1.0.1", security=security))
        self.run_pr()
        self.edit_gh(lambda state: state["pulls"][0].update(state="closed"))
        self.plan_groups(self.group("inputs", "1.0.2", security=security, errors=["Avalonia: HTTP 503"]))
        code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertIn("| inputs | kept: lookup errors |", summary)
        self.assertIn("lookup errors kept the branch upstream/inputs as it was; it has no open pull request",
                      summary)
        self.assertNotIn("kept the pull request", summary)

    def test_verdict_security_update_with_a_foreign_pull_request(self):
        security = {"before": True, "after": False, "cves": []}
        self.add_pull("upstream/inputs", "someone")
        self.plan_groups(self.group("inputs", "1.0.1", security=security))
        code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertIn("| inputs | pull request not opened by the workflow |", summary)
        self.assertIn("the open pull request on upstream/inputs was not opened by the workflow", summary)
        self.assertNotIn("no open pull request", summary)

    # --- stale plans ----------------------------------------------------------------------------

    def test_stale_plan_is_retried_next_run(self):
        security = {"before": True, "after": False, "cves": []}
        planned = self.main_sha()
        entries = (self.group("tools", "2.0"), self.group("inputs", "1.0.1", security=security))
        self.move_main({TOOLS_FILE: "task 1.5\n", INPUTS_FILE: "dotnet 1.0.1\n"})
        self.plan_groups(*entries, commit=planned)
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | stale plan (main moved; retried next run) |", summary)
        self.assertIn("| inputs | stale plan (main moved; retried next run) |", summary)
        self.assertIn(f"main moved from the planned commit {planned[:12]}", summary)
        self.assertEqual((self.branch("tools"), self.branch("inputs")), (None, None))
        self.assertEqual(self.writes(), [])
        # Main unchanged since the plan, or an older plan without the commit: the patch is broken.
        for commit in ("main", None):
            self.plan_groups(*entries, commit=commit)
            code, summary = self.run_pr()
            self.assertEqual(code, 1)
            self.assertIn("| tools | failed |", summary)
            self.assertIn("git or gh operations failed for tools, inputs", summary)
            self.assertIn("git apply", summary)

    def test_internal_error_for_invalid_planned_commit(self):
        self.plan_groups(self.group("inputs", None), commit="HEAD")
        code, summary = self.run_pr()
        self.assertEqual(code, 2)
        self.assertIn("invalid commit", summary)

    # --- groups that disappeared ----------------------------------------------------------------

    def test_pull_requests_of_groups_no_longer_planned_are_closed(self):
        self.plan_groups(self.group("skiasharp-3", "3.1"), self.group("skiasharp-4", "4.1"),
                         self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        head = self.maintainer_commit("skiasharp-4", {"other.txt": "fixed\n"})
        self.add_pull("upstream/old", "someone")
        self.plan_groups(self.group("skiasharp-5", "5.1"), self.group("inputs", None))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        old, kept = self.pulls("skiasharp-3")[0], self.pulls("skiasharp-4")[0]
        self.assertEqual(old["state"], "closed")
        self.assertIn("no longer has the `skiasharp-3` group", self.gh_state()["comments"][str(old["number"])][0]["body"])
        self.assertIsNone(self.branch("skiasharp-3"))
        self.assertIn("| skiasharp-3 | closed (group no longer planned) |", summary)
        self.assertEqual(kept["state"], "open")
        self.assertEqual(self.branch("skiasharp-4"), head)
        self.assertNotIn(str(kept["number"]), self.gh_state()["comments"])
        self.assertIn("| skiasharp-4 | has maintainer commits |", summary)
        self.assertEqual(self.pulls("old")[0]["state"], "open")
        self.assertIn("| skiasharp-5 | created |", summary)
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertEqual(self.writes(), [])

    def test_group_whose_plan_failed_is_not_retired(self):
        head = self.open_tools_pull()
        self.plan_groups(("tools", (2, None, b"")), self.group("inputs", None))
        self.reset_calls()
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("| tools | failed |", summary)
        self.assertNotIn("closed (group no longer planned)", summary)
        self.assertEqual(self.pulls("tools")[0]["state"], "open")
        self.assertEqual(self.branch("tools"), head)
        self.assertEqual(self.writes(), [])

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

    def test_invalid_metadata_fails_the_group(self):
        _, (rc, meta, patch) = self.group("tools", "2.0")
        meta["changed"] = ["../outside.txt"]
        self.set_plan(groups={"tools": (rc, meta, patch), "inputs": self.group("inputs", None)[1]})
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        self.assertIn("not repository-relative", summary)
        self.assertIsNone(self.branch("tools"))

    # --- verdict --------------------------------------------------------------------------------

    def test_verdict_upstream_check_failure(self):
        self.plan_groups(self.group("inputs", None), check=2)
        code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertIn("the upstream check exited with 2", summary)

    def test_verdict_security_problem_remaining_after_updates(self):
        security = {"before": True, "after": True, "cves": ["CVE-2026-0001"]}
        self.plan_groups(self.group("inputs", "1.0.1", security=security))
        code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertIn("a security problem remains after the automatic updates (CVE-2026-0001)", summary)

    def test_verdict_security_update_with_open_pull_request_passes(self):
        security = {"before": True, "after": False, "cves": ["CVE-2026-0001"]}
        self.plan_groups(self.group("inputs", "1.0.1", security=security))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)
        code, summary = self.run_pr()  # unchanged keeps the open pull request for the target
        self.assertEqual(code, 0, summary)

    def test_verdict_security_update_declined_fails(self):
        security = {"before": True, "after": False, "cves": []}
        self.plan_groups(self.group("inputs", "1.0.1", security=security))
        self.run_pr()
        self.edit_gh(lambda state: state["pulls"][0].update(state="closed"))
        code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertIn("| inputs | suppressed (declined target) |", summary)
        self.assertIn("no open pull request on upstream/inputs for the current target", summary)

    def test_verdict_security_update_outdated_by_maintainer_commits_fails(self):
        security = {"before": True, "after": False, "cves": []}
        self.plan_groups(self.group("inputs", "1.0.1", security=security))
        self.run_pr()
        self.maintainer_commit("inputs", {"other.txt": "fixed\n"})
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)  # the open pull request still has the current target
        self.plan_groups(self.group("inputs", "1.0.2", security=security))
        code, summary = self.run_pr()
        self.assertEqual(code, 1)
        self.assertIn("| inputs | has maintainer commits |", summary)
        self.assertIn("no open pull request on upstream/inputs", summary)

    def test_verdict_without_security_problem_ignores_declined_inputs(self):
        self.plan_groups(self.group("inputs", "1.0.1", security={"before": False, "after": False, "cves": []}))
        self.run_pr()
        self.edit_gh(lambda state: state["pulls"][0].update(state="closed"))
        code, summary = self.run_pr()
        self.assertEqual(code, 0, summary)

    def test_internal_error_without_status(self):
        code, summary = self.run_pr()
        self.assertEqual(code, 2)
        self.assertIn("Internal error", summary)

    def test_internal_error_for_invalid_group_name(self):
        (self.plan / "status.json").write_text(json.dumps({"check": 0, "groups": {"../x": 0}}), encoding="utf-8")
        code, _ = self.run_pr()
        self.assertEqual(code, 2)


# --- the plan step of the workflow ------------------------------------------------------------------

PLAN_STEP = "Check upstream releases and plan the updates"

# Stands in for scripts/upstream.py in the plan step: `check` and `groups` from STUB_CONFIG; `apply` modifies the
# tracked <group>.txt, adds a binary file and an ignored file, writes the metadata and exits with the group's code.
STUB_UPSTREAM = textwrap.dedent(r'''
    import json, os, sys
    from pathlib import Path
    config = json.loads(os.environ["STUB_CONFIG"])
    args = sys.argv[1:]
    root = Path.cwd()
    if args[:1] == ["--root"]:
        root, args = Path(args[1]), args[2:]
    if args == ["check"]:
        sys.exit(config["check"])
    if args == ["groups"]:
        sys.stdout.buffer.write(("\n".join(config["groups"]) + "\n").encode())  # LF, also on Windows
        sys.exit(0)
    assert args[:2] == ["apply", "--group"] and args[3:5] == ["--keep-going", "--metadata"] and len(args) == 6, args
    group = args[2]
    sys.stdin.read()  # a `while read` loop over the group list would lose the remaining groups here
    (root / f"{group}.txt").write_text(f"{group} updated\n", encoding="utf-8")
    (root / "new").mkdir(exist_ok=True)
    (root / "new" / f"{group}.bin").write_bytes(b"\0\1\2" + group.encode())
    (root / "ignored.log").write_text("ignored\n", encoding="utf-8")
    Path(args[5]).write_text(json.dumps({"group": group, "changed": [f"{group}.txt", f"new/{group}.bin"]}),
                             encoding="utf-8")
    sys.exit(config["rc"].get(group, 0))
''')


def workflow_run_script(step_name):
    """The `run: |` block of the named step of .github/workflows/upstream.yml, dedented."""
    lines = (ROOT / ".github/workflows/upstream.yml").read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == f"- name: {step_name}")
    step_indent = len(lines[start]) - len(lines[start].lstrip())
    block, run_indent = [], None
    for line in lines[start + 1:]:
        indent = len(line) - len(line.lstrip())
        if run_indent is not None:
            if line.strip() and indent <= run_indent:
                break
            block.append(line)
        elif line.strip() and indent <= step_indent:
            break
        elif line.strip() == "run: |":
            run_indent = indent
    if not block:
        raise AssertionError(f"no run block in step {step_name!r}")
    return textwrap.dedent("\n".join(block)).strip("\n") + "\n"


def find_bash():
    """Git's bash on Windows (never the WSL launcher), bash from PATH elsewhere; None when there is none."""
    if os.name != "nt":
        return shutil.which("bash")
    try:
        exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    for parent in Path(exec_path).parents:
        candidate = parent / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


class WorkflowPlanStepTests(unittest.TestCase):
    def test_plan_step_records_status_commit_and_patches(self):
        bash = find_bash()
        if not bash:
            self.skipTest("bash is not available")
        temporary = tempfile.TemporaryDirectory(prefix="upstream-plan-step-")
        self.addCleanup(temporary.cleanup)
        temp = Path(temporary.name)
        repo, plan, runner_temp, shims = temp / "repo", temp / "plan", temp / "runner", temp / "shims"
        for directory in (repo / "scripts", runner_temp, shims):
            directory.mkdir(parents=True)
        groups = ["inputs", "skiasharp-3", "tools"]
        (repo / "scripts/upstream.py").write_text(STUB_UPSTREAM, encoding="utf-8")
        (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
        for group in groups:
            (repo / f"{group}.txt").write_text(f"{group}\n", encoding="utf-8")
        # `python` in the step is this interpreter.
        (shims / "python").write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8")
        (shims / "python").chmod(0o755)
        (temp / "gitconfig").write_text("", encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "GITHUB_", "GH_"))}
        env.update({
            "GIT_CONFIG_GLOBAL": str(temp / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": str(shims) + os.pathsep + env.get("PATH", ""),
            "PLAN": plan.as_posix(), "RUNNER_TEMP": runner_temp.as_posix(), "GITHUB_TOKEN": "unused",
            "STUB_CONFIG": json.dumps({"check": 3, "groups": groups, "rc": {"skiasharp-3": 2}}),
        })

        def git(*arguments):
            return subprocess.run(["git", *arguments], cwd=repo, env=env, capture_output=True, text=True,
                                  check=True).stdout.strip()

        git("init", "--quiet", "--initial-branch=main")
        git("add", "-A")
        git("commit", "--quiet", "-m", "Initial commit")
        script = temp / "step.sh"
        script.write_bytes(workflow_run_script(PLAN_STEP).encode("utf-8"))
        # As GitHub runs `run` steps: bash --noprofile --norc -eo pipefail {0}.
        process = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()], cwd=repo,
                                 env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                                 check=False)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        head = git("rev-parse", "HEAD")
        status = json.loads((plan / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status, {"check": 3, "commit": head, "groups": {"inputs": 0, "skiasharp-3": 2, "tools": 0}})
        self.assertEqual(upstream_pr.load_status(plan), (3, status["groups"], head))
        for group in groups:
            patch = plan / f"{group}.patch"
            text = patch.read_bytes().decode("utf-8", "replace")
            self.assertIn(f"diff --git a/new/{group}.bin b/new/{group}.bin\nnew file mode 100644", text)
            self.assertIn("GIT binary patch", text)
            self.assertIn(f"+{group} updated", text)
            self.assertNotIn("ignored.log", text)
            self.assertEqual(json.loads((plan / f"{group}.json").read_text(encoding="utf-8"))["group"], group)
            git("apply", "--check", "--index", patch.as_posix())
            numstat = git("apply", "--numstat", patch.as_posix()).splitlines()
            self.assertEqual(sorted(line.split("\t")[2] for line in numstat),
                             sorted([f"{group}.txt", f"new/{group}.bin"]))
        self.assertEqual(git("status", "--porcelain"), "", "the checkout itself is left alone")


if __name__ == "__main__":
    unittest.main()
