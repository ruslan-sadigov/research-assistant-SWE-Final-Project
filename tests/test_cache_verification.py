"""Keep the documented cache hit-rate verification reproducible."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).with_name("cache_verification.py")


def test_cache_verification_reports_the_documented_hit_rates(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(HARNESS), "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
    report = json.loads(completed.stdout)
    assert report["headline"] == {
        "cold": {"hits": 0, "lookups": 15, "hit_rate": 0.0},
        "warm": {"hits": 15, "lookups": 15, "hit_rate": 1.0},
        "combined": {"hits": 15, "lookups": 30, "hit_rate": 0.5},
    }
    assert [check["check"] for check in report["checks"] if not check["passed"]] == []
    assert report["schema"]["user_version"] == 1
