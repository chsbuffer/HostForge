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


SCRIPT_ROOT = Path(__file__).resolve().parent


def main():
    if is_windows:
        execv("cl.exe /nologo /c dll.cpp")
        execv("lib.exe /nologo dll.obj")
    else:
        execv("clang++ -c dll.cpp")
        execv("llvm-ar rcs dll.a dll.o")


if __name__ == "__main__":
    main()
