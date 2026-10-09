"""Run a build, persist compiler-cache statistics, and stop its private daemon."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

tool = os.environ.get("HOSTFORGE_COMPILER_CACHE")
is_sccache = tool and Path(tool).stem == "sccache"
if is_sccache:
    # A local build must not join or stop another build's sccache server.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        os.environ["SCCACHE_SERVER_PORT"] = str(listener.getsockname()[1])
elif tool:
    # ccache persists counters with its files; report only this build's work.
    subprocess.run([tool, "--zero-stats"], check=True)
start = time.monotonic()
result = 1
try:
    result = subprocess.call(sys.argv[1:])
finally:
    output = Path("build/cache-stats")
    output.mkdir(parents=True, exist_ok=True)
    (output / "build.json").write_text(json.dumps({"seconds": time.monotonic() - start, "exitCode": result}), encoding="utf-8")
    if tool:
        try:
            for name, args in (("stats.txt", ["--show-stats"]),
                               ("stats.json", ["--show-stats", "--stats-format=json"] if is_sccache else ["--print-stats"])):
                stats = subprocess.run([tool, *args], encoding="utf-8", errors="replace",
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                (output / name).write_text(stats.stdout, encoding="utf-8")
                print(stats.stdout)
        finally:
            if is_sccache:
                subprocess.run([tool, "--stop-server"], check=False)
sys.exit(result)
