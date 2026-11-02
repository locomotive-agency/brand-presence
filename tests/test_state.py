import subprocess
import sys

import pytest

from brand_presence.state import locked
from project import ROOT

ACQUIRE = """
import sys
from pathlib import Path
from brand_presence.errors import UserError
from brand_presence.state import locked

try:
    with locked(Path(sys.argv[1])):
        print("acquired", flush=True)
        if len(sys.argv) > 2:
            sys.stdin.read()
except UserError as error:
    print(error)
    raise SystemExit(2)
"""


def acquire(directory):
    return subprocess.run([sys.executable, "-c", ACQUIRE, str(directory)], cwd=ROOT,
                          capture_output=True, text=True, timeout=10)


def test_local_lock_excludes_another_process_and_releases_after_error(tmp_path):
    with pytest.raises(RuntimeError, match="interrupted"):
        with locked(tmp_path):
            refused = acquire(tmp_path)
            assert refused.returncode == 2, refused.stderr
            assert "another command holds the state directory" in refused.stdout
            assert acquire(tmp_path / "another").returncode == 0
            raise RuntimeError("interrupted")
    assert acquire(tmp_path).returncode == 0


def test_local_lock_is_released_when_its_process_is_killed(tmp_path):
    with subprocess.Popen([sys.executable, "-c", ACQUIRE, str(tmp_path), "wait"], cwd=ROOT,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as process:
        try:
            import select

            ready, _, _ = select.select([process.stdout], [], [], 10)
            assert ready, "lock holder did not start"
            assert process.stdout.readline().strip() == "acquired"
            assert acquire(tmp_path).returncode == 2
        finally:
            process.kill()
            process.communicate(timeout=10)
    assert acquire(tmp_path).returncode == 0
