import platform
import subprocess
import sys
from pathlib import Path

# OS detection
os_name = platform.system().lower()
is_windows = os_name not in ("linux", "darwin")


def error(text):
    print(f"\n! {text}\n")
    sys.exit(1)


def execv(cmd: str, cwd: Path | None = None, env=None):
    out = None
    print(cmd)
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", cmd] if is_windows else ["bash", "-c", cmd],
        cwd=cwd,
        env=env,
        stdout=out,
        stderr=out,
        shell=False,
    )
    if proc.returncode != 0:
        error(f"Command failed ({proc.returncode}): {cmd}")

    return proc


def run_in_vs_env(cmd: str, cwd: Path, arch: str, env=None):
    full_cmd = f"call {INIT_VS_ENV_CMD} {arch} && cd /d {cwd} && {cmd}"
    execv(full_cmd)


SCRIPT_ROOT = Path(__file__).resolve().parent
INIT_VS_ENV_CMD = SCRIPT_ROOT / ".." / ".." / ".." / "scripts" / "init-vs-env.cmd"


def main():
    if is_windows:
        if not Path(SCRIPT_ROOT, "dll.lib").exists():
            run_in_vs_env(
                "cl.exe /nologo /c dll.cpp && lib.exe /nologo dll.obj",
                cwd=SCRIPT_ROOT,
                arch="x64",
            )
    else:
        if not Path(SCRIPT_ROOT, "dll.a").exists():
            execv("clang++ -c dll.cpp")
            execv("llvm-ar rcs dll.a dll.o")


if __name__ == "__main__":
    main()
