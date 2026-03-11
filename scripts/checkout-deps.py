#!/usr/bin/env python3
import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path


def color_print(code, text):
    if no_color:
        print(text)
    else:
        text = text.replace("\n", f"\033[0m\n{code}")
        print(f"{code}{text}\033[0m")


def error(text):
    color_print("\033[41;39m", f"\n! {text}\n")
    sys.exit(1)


def header(text):
    color_print("\033[44;39m", f"\n* {text}\n")


def vprint(text):
    if args.verbose > 0:
        print(text)


# OS detection
os_name = platform.system().lower()
is_windows = os_name not in ("linux", "darwin")

no_color = False
if is_windows:
    try:
        import colorama

        colorama.init()
    except ImportError:
        no_color = True

if not sys.version_info >= (3, 10):
    error("Requires Python 3.10+")

# Global vars
args: argparse.Namespace


###################
# Helper functions
###################


def execv(cmds, cwd: Path | None = None, env=None):
    if args.verbose > 0:
        print(" ".join(str(x) for x in cmds))
    proc = subprocess.run(cmds, cwd=cwd, env=env, shell=False)
    if proc.returncode != 0:
        error(f"Command failed ({proc.returncode}): {' '.join(str(x) for x in cmds)}")
    return proc


def parse_file_to_dict(path: Path) -> dict:
    dictionary = {}
    with path.open(encoding="utf-8") as file:
        exec(file.read(), dictionary)
    return dictionary


#################
# Checkout deps
#################


SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parent
DEPS_FILE = REPO_ROOT / "DEPS"
CHECKOUT_ROOT = REPO_ROOT / "repo"
MIRROR_ROOT = CHECKOUT_ROOT / "deps-mirror"


def is_ci() -> bool:
    return bool(os.environ.get("CI"))


def resolve_checkouts() -> list[tuple[str, str, str, Path]]:
    deps = parse_file_to_dict(DEPS_FILE)
    urls = deps["urls"]
    available_targets = deps["targets"]

    if args.target not in available_targets:
        error(f"Unknown target: {args.target}")

    versions = deps[args.target]
    if args.version not in versions:
        error(f"Unknown version for {args.target}: {args.version}")

    checkouts = []
    for name, commit in versions[args.version].items():
        directory = CHECKOUT_ROOT / f"{name}-{args.version}"
        checkouts.append((name, urls[name], commit, directory))
    return checkouts


def ensure_mirror(repo: str, mirror_dir: Path):
    if not mirror_dir.exists():
        mirror_dir.parent.mkdir(parents=True, exist_ok=True)
        execv(["git", "init", "--bare", mirror_dir])
        execv(["git", "remote", "add", "origin", repo], cwd=mirror_dir)
        execv(["git", "config", "remote.origin.promisor", "true"], cwd=mirror_dir)
        execv(
            ["git", "config", "remote.origin.partialclonefilter", "blob:none"],
            cwd=mirror_dir,
        )

    execv(["git", "remote", "set-url", "origin", repo], cwd=mirror_dir)
    execv(["git", "config", "remote.origin.promisor", "true"], cwd=mirror_dir)
    execv(
        ["git", "config", "remote.origin.partialclonefilter", "blob:none"],
        cwd=mirror_dir,
    )
    execv(
        [
            "git",
            "fetch",
            "--prune",
            "--filter=blob:none",
            "origin",
            "+refs/heads/*:refs/heads/*",
            "+refs/tags/*:refs/tags/*",
        ],
        cwd=mirror_dir,
    )


def ensure_worktree(mirror_dir: Path, commit: str, directory: Path):
    if directory.exists():
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
        if proc.returncode != 0:
            error(f"Existing directory is not a git worktree: {directory}")
        head = proc.stdout.strip()
        if head == commit:
            return
        error(f"Existing worktree has different commit: {directory}")

    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=mirror_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    if proc.returncode != 0:
        error(f"Commit not found in mirror: {commit}")

    execv(["git", "update-ref", f"refs/keep/{commit}", commit], cwd=mirror_dir)
    execv(["git", "worktree", "add", "--detach", directory, commit], cwd=mirror_dir)


def checkout_repo(name: str, repo: str, commit: str, directory: Path):
    if not directory.exists():
        if is_ci():
            execv(["git", "init", directory])
            execv(["git", "remote", "add", "origin", repo], cwd=directory)
            execv(["git", "fetch", "--depth=1", "origin", commit], cwd=directory)
            execv(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=directory)
            return

        mirror_dir = MIRROR_ROOT / f"{name}.git"
        ensure_mirror(repo, mirror_dir)
        ensure_worktree(mirror_dir, commit, directory)
        return

    if is_ci():
        execv(["git", "remote", "set-url", "origin", repo], cwd=directory)
        execv(["git", "fetch", "--depth=1", "origin", commit], cwd=directory)
        execv(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=directory)
        return

    mirror_dir = MIRROR_ROOT / f"{name}.git"
    ensure_mirror(repo, mirror_dir)
    ensure_worktree(mirror_dir, commit, directory)


def checkout_deps():
    checkouts = resolve_checkouts()
    header(f"Checkout {args.target} {args.version}")
    for name, repo, commit, directory in checkouts:
        vprint(f"{directory} @ {commit}")
        checkout_repo(name, repo, commit, directory)
    print("\nDone.")


##########################
# Parse arguments
##########################


def parse_args():
    parser = argparse.ArgumentParser(description="Checkout dependency repos from DEPS")
    parser.set_defaults(func=checkout_deps)
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="verbose output"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list available target/version combinations",
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="checkout target in DEPS, such as runtime or skiasharp",
    )
    parser.add_argument(
        "version", nargs="?", help="version key under the target in DEPS"
    )
    return parser.parse_args()


def main():
    global args
    args = parse_args()
    if args.list:
        deps = parse_file_to_dict(DEPS_FILE)
        targets = deps.get("targets", [])
        for target in targets:
            versions = deps.get(target, {})
            print(f"{target}: {', '.join(versions.keys())}")
        return
    if not args.target or not args.version:
        error("target and version are required unless --list is specified")
    args.func()


if __name__ == "__main__":
    main()
