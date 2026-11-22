"""Package versions change whenever their inputs change; run with python -m unittest discover -s tests/native.

nuget.org keeps the first upload of a version and CI pushes with --skip-duplicate, so a package rebuilt from new
inputs under an old version would be skipped while CI stays green. package-inputs.lock.json is the append-only
history of the inputs of every package version; these tests fail until the version is changed and the new
version is appended with `python scripts/upstream.py lock`.
"""

import contextlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
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

MAJOR = r"(<_Major\b[^>]*>)(\d+)(</_Major>)"


class PackageLockTests(unittest.TestCase):
    """The guard: runs in CI (plan-all) against the repository."""

    def test_versions_change_with_their_inputs(self):
        records = package_inputs.compute(ROOT)
        found = package_inputs.guard(package_inputs.read_lock(ROOT), records)
        if found:
            self.fail("\nA package version cannot be published as it is; nuget.org keeps the first upload of a "
                      "version and --skip-duplicate would silently skip the new one.\n\n" + "\n\n".join(found))

    def test_lock_is_current(self):
        records = package_inputs.compute(ROOT)
        lock = package_inputs.read_lock(ROOT)
        if package_inputs.guard(lock, records):
            self.skipTest("test_versions_change_with_their_inputs reports the version to change first")
        differences = package_inputs.stale(lock, records)
        if differences:
            self.fail(f"\n{package_inputs.LOCK} is out of date:\n  " + "\n  ".join(differences)
                      + "\nRun `python scripts/upstream.py lock` (task upstream-lock) and commit the result.")


def copy_sources(destination):
    for relative in [*package_inputs.SOURCES, package_inputs.LOCK]:
        if (ROOT / relative).exists():
            (destination / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination / relative)


def replace(path, pattern, replacement):
    text = path.read_text(encoding="utf-8")
    changed, count = re.subn(pattern, replacement, text, flags=re.M)
    if count != 1:
        raise AssertionError(f"{path.name}: {pattern} matched {count} times")
    path.write_text(changed, encoding="utf-8")


def increment(path, pattern, by=1):
    """Add `by` to the number captured by the second group of `pattern` (groups: prefix, number, suffix)."""
    replace(path, pattern, lambda match: f"{match.group(1)}{int(match.group(2)) + by}{match.group(3)}")


def change_vmr_commit(root, commit):
    """Point the first runtime at another VMR commit; returns its target framework."""
    hostlibs = root / "native/hostlibs/versions.json"
    sources = json.loads(hostlibs.read_text(encoding="utf-8"))
    runtime = next(iter(sources))
    sources[runtime]["commit"] = commit
    hostlibs.write_text(json.dumps(sources), encoding="utf-8")
    return next(framework for framework, record in package_inputs.compute(root)["ChsBuffer.AppHost.Static"].items()
                if record["inputs"]["version"] == runtime)


def entry(variant, **inputs):
    return {"variant": variant, "inputs": inputs}


def record(version, **inputs):
    return {"version": version, "bump": "increment X", "inputs": inputs}


class RuleTests(unittest.TestCase):
    """The four rules and the comparison of two locks, on synthetic records."""

    def lock(self, packages):
        return package_inputs.document(packages)

    def test_variants_of_a_family_need_distinct_versions(self):
        records = {"F": {"a": record("1.0.0", x=1), "b": record("1.0.0", x=2), "c": record("1.0.0-c", x=3)},
                   "G": {"a": record("1.0.0", x=1)}}
        found = package_inputs.conflicts(records)
        self.assertEqual([("F", "1.0.0", ["a", "b"])],
                         [(item["family"], item["version"], item["variants"]) for item in found])
        self.assertIn("a, b", package_inputs.guard(self.lock({}), records)[0])

    def test_locked_version_keeps_its_variant_and_inputs(self):
        lock = self.lock({"F": {"1.0.0": entry("a", x=1)}})
        self.assertEqual([], package_inputs.violations(lock, {"F": {"a": record("1.0.0", x=1)}}))
        # A target framework removed and added back with another source, under its old version.
        changed = package_inputs.violations(lock, {"F": {"a": record("1.0.0", x=2)}})
        self.assertEqual([("inputs/x", 1, 2)], changed[0]["changes"])
        # Another variant claiming a version that an earlier variant already published.
        renamed = package_inputs.violations(lock, {"F": {"b": record("1.0.0", x=1)}})
        self.assertEqual([("variant", "a", "b")], renamed[0]["changes"])

    def test_history_keeps_versions_no_longer_built(self):
        lock = self.lock({"F": {"1.0.0": entry("a", x=1)}})
        records = {"F": {"a": record("1.0.1", x=2)}}
        self.assertEqual([], package_inputs.guard(lock, records))
        self.assertEqual(["F (a) 1.0.1: not in the lock"], package_inputs.stale(lock, records))
        appended = package_inputs.append(package_inputs.history(lock), records)
        self.assertEqual({"F": {"1.0.0": entry("a", x=1), "1.0.1": entry("a", x=2)}}, appended)
        self.assertEqual([], package_inputs.stale(self.lock(appended), records))

    def test_versions_only_move_forward_per_variant(self):
        lock = self.lock({"F": {"1.0.0": entry("a", x=1), "2.0.0-b": entry("b", x=1), "1.1.0": entry("a", x=2)}})
        found = package_inputs.regressions(lock, {"F": {"a": record("1.0.0", x=1), "b": record("2.0.0-b", x=1)}})
        self.assertEqual([("a", "1.1.0", ["1.1.0"])],
                         [(item["variant"], item["locked"], item["newer"]) for item in found])

    def test_changed_entries_of_a_base_lock(self):
        base = self.lock({"F": {"1.0.0": entry("a", x=1), "1.0.1": entry("a", x=2)}})
        self.assertEqual([], package_inputs.changed_entries(
            base, self.lock({"F": {"1.0.0": entry("a", x=1), "1.0.1": entry("a", x=2), "1.0.2": entry("a", x=3)}})))
        found = package_inputs.changed_entries(base, self.lock({"F": {"1.0.0": entry("a", x=9)}}))
        self.assertEqual([("1.0.0", [("inputs/x", 1, 9)]), ("1.0.1", None)],
                         [(item["version"], item["changes"]) for item in found])
        self.assertEqual(2, len(package_inputs.changed_entries(base, None)))

    def test_rejects_a_lock_in_another_format(self):
        with self.assertRaisesRegex(ValueError, r'expected \{"variant", "inputs"\}'):
            package_inputs.history({"packages": {"F": {"a": {"version": "1.0.0", "inputs": {}}}}})

    def test_render_orders_versions(self):
        text = package_inputs.render({"F": {"1.0.10": entry("a", x=1), "1.0.9": entry("a", x=1),
                                            "1.0.9-b": entry("b", x=1)}})
        self.assertLess(text.index('"1.0.9-b"'), text.index('"1.0.9"'))
        self.assertLess(text.index('"1.0.9"'), text.index('"1.0.10"'))
        self.assertIn('"inputs": {"x": 1}', text)


class LockMechanicsTests(unittest.TestCase):
    """The guard's behaviour on a temporary copy of the version sources (no git: nothing counts as committed)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hostforge-lock-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_sources(self.root)
        self.write_lock()
        self.props = self.root / package_inputs.PROPS

    def write_lock(self):
        records = package_inputs.compute(self.root)
        (self.root / package_inputs.LOCK).write_text(package_inputs.render(package_inputs.entries(records)),
                                                     encoding="utf-8")

    def lock_text(self):
        return (self.root / package_inputs.LOCK).read_text(encoding="utf-8")

    def violated(self):
        records = package_inputs.compute(self.root)
        return sorted((item["family"], item["variant"]) for item in
                      package_inputs.violations(package_inputs.read_lock(self.root), records))

    def lock_command(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = upstream.main(["--root", str(self.root), "lock", *arguments])
        return code, stdout.getvalue() + stderr.getvalue()

    def test_versions_follow_msbuild(self):
        records = package_inputs.compute(self.root)
        values = package_inputs.evaluate([self.props])
        apphost = records["ChsBuffer.Avalonia.AppHost"]
        base = f"{values['TargetAvaloniaVersion']}.{values['_Major']}"
        for variant, item in apphost.items():
            self.assertEqual(base if variant == "skia3" else f"{base}-{variant}", item["version"])
        for item in records["ChsBuffer.SkiaSharp.Static"].values():
            self.assertRegex(item["version"], rf"^{re.escape(item['inputs']['version'])}\.\d+$")
        angle = records["ChsBuffer.Angle.Static"]["default"]
        self.assertTrue(angle["version"].startswith(".".join(angle["inputs"]["version"].split(".")[:3]) + "."))
        for framework, item in records["ChsBuffer.AppHost.Static"].items():
            self.assertTrue(item["version"].startswith(item["inputs"]["version"] + "-"), framework)

    def test_unsupported_group_condition_is_an_error(self):
        replace(self.props, r"^    <_Major>(\d+)</_Major>\n",
                r"""  </PropertyGroup>\n  <PropertyGroup Condition="Exists('x.props')">\n    <_Major>\1</_Major>\n"""
                r"""  </PropertyGroup>\n  <PropertyGroup>\n""")
        with self.assertRaises(ValueError) as raised:
            package_inputs.compute(self.root)
        self.assertRegex(str(raised.exception), r"AvaloniaAppHostPackageVersion.*_Major.*unsupported condition.*Exists")

    def test_vmr_commit_change_requires_new_versions(self):
        before = package_inputs.read_lock(self.root)
        framework = change_vmr_commit(self.root, "0" * 40)
        expected = [("ChsBuffer.AppHost.Static", framework)] + \
                   [("ChsBuffer.Avalonia.AppHost", variant) for variant in
                    package_inputs.compute(self.root)["ChsBuffer.Avalonia.AppHost"]]
        self.assertEqual(sorted(expected), self.violated())

        code, output = self.lock_command()
        self.assertEqual(1, code)
        self.assertIn(f"runtimes/{framework}/vmr-commit", output)
        self.assertIn("<_Major>", output)
        self.assertIn("AppHostStatic.csproj", output)
        self.assertEqual(1, self.lock_command("--check")[0])

        increment(self.props, MAJOR)
        self.assertEqual([("ChsBuffer.AppHost.Static", framework)], self.violated())
        increment(self.root / package_inputs.APPHOST_STATIC, r"(<Version>\$\(HostLibsVersion\)-alpha\.)(\d+)(</Version>)")
        self.assertEqual([], self.violated())
        self.assertEqual(0, self.lock_command()[0])
        after = package_inputs.read_lock(self.root)
        self.assertEqual([], package_inputs.stale(after, package_inputs.compute(self.root)))
        self.assertEqual([], package_inputs.changed_entries(before, after), "lock only appends")
        self.assertEqual(0, self.lock_command("--check")[0])

    def test_angle_commit_change_requires_new_versions(self):
        angle = self.root / "native/angle/conandata.yml"
        text = angle.read_text(encoding="utf-8")
        if re.search(r"^sources:\n(?:  .*\n)*  commit:", text, re.M):
            replace(angle, r'^(sources:\n(?:  .*\n)*  commit: *")[0-9a-f]*(")', r"\g<1>" + "1" * 40 + r"\g<2>")
        else:
            replace(angle, r'^(sources:\n(?:  .*\n)*?  branch: .*\n)', r'\g<1>  commit: "' + "1" * 40 + '"\n')
        apphost = [("ChsBuffer.Avalonia.AppHost", variant) for variant in
                   package_inputs.compute(self.root)["ChsBuffer.Avalonia.AppHost"]]
        self.assertEqual(sorted([("ChsBuffer.Angle.Static", "default")] + apphost), self.violated())
        increment(self.props, r"(<AnglePackageVersion>.*\)\.)(\d+)(</AnglePackageVersion>)")
        self.assertEqual(sorted(apphost), self.violated())
        increment(self.props, MAJOR)
        self.assertEqual([], self.violated())

    def test_variant_version_conflict_is_refused(self):
        # skia4 loses its prerelease suffix and claims the version of skia3.
        replace(self.props, r"(<_AvaloniaAppHostPreview Condition=\"[^\"]*'\$\(_Preview\)' == ''\">)skia4(<)", r"\1\2")
        increment(self.props, MAJOR)
        conflicts = package_inputs.conflicts(package_inputs.compute(self.root))
        self.assertEqual([("ChsBuffer.Avalonia.AppHost", ["skia3", "skia4"])],
                         [(item["family"], item["variants"]) for item in conflicts])
        before = self.lock_text()
        for arguments in ((), ("--force",), ("--check",)):
            code, output = self.lock_command(*arguments)
            self.assertEqual(1, code, arguments)
            self.assertIn("skia3, skia4", output)
        self.assertEqual(before, self.lock_text())

    def test_edited_existing_entry_fails_the_guard(self):
        lock = json.loads(self.lock_text())
        version, item = next(iter(lock["packages"]["ChsBuffer.Angle.Static"].items()))
        item["inputs"]["commit"] = "2" * 40
        (self.root / package_inputs.LOCK).write_text(json.dumps(lock), encoding="utf-8")
        found = package_inputs.guard(package_inputs.read_lock(self.root), package_inputs.compute(self.root))
        self.assertEqual(1, len(found))
        self.assertIn(f"ChsBuffer.Angle.Static {version} is already in the lock", found[0])
        self.assertEqual(1, self.lock_command("--check")[0])
        self.assertEqual(1, self.lock_command()[0])

    def test_lock_refuses_versions_moving_backwards(self):
        increment(self.props, MAJOR)
        self.assertEqual(0, self.lock_command()[0])
        ahead = package_inputs.compute(self.root)["ChsBuffer.Avalonia.AppHost"]["skia3"]["version"]
        increment(self.props, MAJOR, by=-1)
        before = self.lock_text()
        code, output = self.lock_command()
        self.assertEqual(1, code)
        self.assertIn("moved back", output)
        self.assertEqual(before, self.lock_text())
        self.assertEqual(1, self.lock_command("--check")[0])
        # Without git nothing is committed, so --force may drop the uncommitted versions ahead.
        self.assertEqual(0, self.lock_command("--force")[0])
        self.assertNotIn(ahead, package_inputs.history(package_inputs.read_lock(self.root))["ChsBuffer.Avalonia.AppHost"])
        self.assertEqual(0, self.lock_command("--check")[0])

    def test_lock_check_reports_a_stale_lock(self):
        increment(self.props, MAJOR)
        code, output = self.lock_command("--check")
        self.assertEqual(1, code)
        self.assertIn("out of date", output)
        self.assertEqual(0, self.lock_command()[0])
        self.assertEqual(0, self.lock_command("--check")[0])

    def test_base_requires_check(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            upstream.main(["--root", str(self.root), "lock", "--base", "HEAD"])

    def test_version_order_follows_nuget(self):
        ordered = ["1.0.0-alpha", "1.0.0-alpha.2", "1.0.0-alpha.10", "1.0.0-beta", "1.0.0", "1.0.0.1", "1.0.1-skia4",
                   "1.0.1"]
        self.assertEqual(ordered, sorted(reversed(ordered), key=package_inputs.version_key))


@unittest.skipUnless(shutil.which("git"), "git is required")
class CommittedLockTests(LockMechanicsTests):
    """--force and the lock history against a git repository whose HEAD holds the lock."""

    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        self.commit("lock")

    def git(self, *arguments, cwd=None):
        command = ["git", "-c", "core.autocrlf=false", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                   "-c", "commit.gpgsign=false", *arguments]
        return subprocess.run(command, cwd=cwd or self.root, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def rewrite_entries(self):
        """Change a VMR commit together with the lock entries of the versions it feeds, so that the lock agrees
        with the repository again."""
        change_vmr_commit(self.root, "0" * 40)
        lock = package_inputs.read_lock(self.root)
        records = package_inputs.compute(self.root)
        for family, versions in package_inputs.entries(records).items():
            lock["packages"][family].update(versions)
        (self.root / package_inputs.LOCK).write_text(package_inputs.render(lock["packages"]), encoding="utf-8")
        self.assertEqual([], package_inputs.guard(package_inputs.read_lock(self.root), records))

    def bump_apphost_versions(self):
        increment(self.props, MAJOR)
        increment(self.root / package_inputs.APPHOST_STATIC, r"(<Version>\$\(HostLibsVersion\)-alpha\.)(\d+)(</Version>)")

    def test_force_cannot_change_committed_entries(self):
        change_vmr_commit(self.root, "0" * 40)
        before = self.lock_text()
        code, output = self.lock_command("--force")
        self.assertEqual(1, code)
        self.assertIn("cannot change committed entries", output)
        self.assertEqual(before, self.lock_text())

    def test_force_replaces_uncommitted_entries_only(self):
        committed = package_inputs.read_lock(self.root)
        self.bump_apphost_versions()
        self.assertEqual(0, self.lock_command()[0])
        change_vmr_commit(self.root, "0" * 40)
        self.assertEqual(1, self.lock_command()[0])
        code, output = self.lock_command("--force")
        self.assertEqual(0, code, output)
        self.assertIn("replaced", output)
        lock = package_inputs.read_lock(self.root)
        self.assertEqual([], package_inputs.guard(lock, package_inputs.compute(self.root)))
        self.assertEqual([], package_inputs.changed_entries(committed, lock))
        self.assertEqual(0, self.lock_command("--check")[0])

    def test_edited_committed_entry_is_refused(self):
        lock = json.loads(self.lock_text())
        family = lock["packages"]["ChsBuffer.Angle.Static"]
        version = next(iter(family))
        del family[version]
        (self.root / package_inputs.LOCK).write_text(json.dumps(lock), encoding="utf-8")
        code, output = self.lock_command()
        self.assertEqual(1, code)
        self.assertIn(f"ChsBuffer.Angle.Static {version}: in the lock of {self.git('rev-parse', 'HEAD')} but removed",
                      output)
        code, output = self.lock_command("--check")
        self.assertEqual(1, code)
        self.assertIn("removed", output)

    def test_entries_rewritten_together_with_their_inputs_are_refused(self):
        base = self.git("rev-parse", "HEAD")
        self.rewrite_entries()
        self.commit("rewrite")
        for arguments in (("--check",), ("--check", "--base", "HEAD"), ("--check", "--base", base)):
            code, output = self.lock_command(*arguments)
            self.assertEqual(1, code, arguments)
            self.assertIn(f"changed since the lock of {base}", output)
            self.assertIn("vmr-commit", output)

    def test_commits_after_a_rewrite_keep_the_first_entries(self):
        # A push that rewrote entries fails its check but stays on main; the pushes after it must still fail.
        published = self.git("rev-parse", "HEAD")
        self.rewrite_entries()
        self.commit("rewrite")
        (self.root / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
        self.commit("unrelated")
        code, output = self.lock_command("--check")
        self.assertEqual(1, code)
        self.assertIn(f"changed since the lock of {published}", output)
        self.assertEqual(1, self.lock_command()[0], "lock refuses to build on the rewritten entries")
        # Restoring the entries and publishing the new inputs under new versions passes again.
        self.git("checkout", published, "--", package_inputs.LOCK)
        self.bump_apphost_versions()
        code, output = self.lock_command()
        self.assertEqual(0, code, output)
        self.commit("restore")
        code, output = self.lock_command("--check")
        self.assertEqual(0, code, output)
        self.assertIn("keep their first committed entries", output)

    def test_history_accepts_appended_entries(self):
        base = self.git("rev-parse", "HEAD")
        increment(self.props, MAJOR)
        self.assertEqual(0, self.lock_command()[0])
        self.commit("bump")
        count = sum(map(len, package_inputs.history(package_inputs.read_lock(self.root)).values()))
        code, output = self.lock_command("--check")
        self.assertEqual(0, code, output)
        self.assertIn(f"All {count} versions on the first-parent history of HEAD keep their first committed entries",
                      output)
        self.assertEqual(0, self.lock_command("--check", "--base", base)[0])

    def test_history_follows_the_first_parent(self):
        # A branch merged into main: the entries of its own commits are not on the first-parent history.
        main = self.git("branch", "--show-current")
        self.git("switch", "-q", "-c", "topic")
        increment(self.props, MAJOR)
        self.assertEqual(0, self.lock_command()[0])
        self.commit("topic: first attempt")
        self.git("switch", "-q", main)
        self.git("merge", "-q", "--no-ff", "--no-commit", "topic")
        change_vmr_commit(self.root, "0" * 40)
        self.git("checkout", main, "--", package_inputs.LOCK)
        self.bump_apphost_versions()
        self.assertEqual(0, self.lock_command()[0])
        self.commit("merge topic with other inputs")
        code, output = self.lock_command("--check")
        self.assertEqual(0, code, output)
        self.assertEqual(1, self.lock_command("--check", "--base", "topic")[0])

    def test_history_without_a_lock_is_skipped(self):
        self.git("checkout", "-q", "--orphan", "without-lock")
        (self.root / package_inputs.LOCK).unlink()
        base = self.commit("no lock")
        self.write_lock()
        code, output = self.lock_command("--check", "--base", base)
        self.assertEqual(0, code, output)
        self.assertIn(f"No commit on the first-parent history of {base} has {package_inputs.LOCK}; skipped", output)

    def test_shallow_clone_is_an_error(self):
        self.commit("second")
        clone = self.root / "shallow"
        self.git("clone", "-q", "--depth=1", self.root.as_uri(), str(clone))
        for arguments in (("--check",), ()):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = upstream.main(["--root", str(clone), "lock", *arguments])
            self.assertEqual(2, code, arguments)
            self.assertIn("shallow clone", stderr.getvalue())

    def test_unknown_base_is_an_error(self):
        code, output = self.lock_command("--check", "--base", "0" * 40)
        self.assertEqual(2, code)
        self.assertIn("cannot resolve", output)


if __name__ == "__main__":
    unittest.main()
