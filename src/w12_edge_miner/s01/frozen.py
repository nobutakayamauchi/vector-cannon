"""Read-only verification of the original W12 lock and its W11 file inventory."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def beneath(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("path outside evidence root")
    return path


def verify_frozen(pins: list[dict[str, str]], roots: dict[str, Path]) -> dict[str, Any]:
    failures: list[str] = []
    unavailable: list[str] = []
    checked = 0

    def check(root: str, relative: str, expected: str) -> bool:
        nonlocal checked
        if root not in roots:
            unavailable.append(root + "/" + relative)
            return False
        path = beneath(roots[root], relative)
        checked += 1
        if not path.is_file() or sha256(path) != expected:
            failures.append(root + "/" + relative)
            return False
        return True

    lock = None
    for pin in pins:
        valid = check(pin["root"], pin["path"], pin["sha256"])
        if valid and pin["root"] == "w12" and pin["path"] == "W12_BASELINE_LOCK.json":
            lock = json.loads(beneath(roots["w12"], pin["path"]).read_text())
    if lock is not None:
        groups = [("reports/w11/" + name, value) for name, value in lock["run_trees"].items()]
        groups += [("src", lock["workspace_src_tree"]), ("tests", lock["workspace_tests_tree"])]
        for prefix, group in groups:
            for relative, expected in group["files"].items():
                check("w11", prefix + "/" + relative, expected)
    else:
        unavailable.append("W11 inventory: original W12 baseline lock not verified")
    return {"status": "FAIL" if failures else "UNVERIFIED" if unavailable else "PASS",
            "checked_files": checked, "changed_or_missing": sorted(failures),
            "unavailable": sorted(unavailable), "scope": "Pinned files and original lock inventory only"}
