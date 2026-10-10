"""Strict operator-owned contracts and bounded filesystem evidence."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any

SKIP = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
KINDS = {"text_equals", "text_contains", "sha256", "json_fields", "csv", "command"}
ROLES = ("worker", "jev", "sol", "astra")
MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024


class ControlError(ValueError):
    """An explicit stop, never a request to lower protections."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ControlError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def constant(_):
        raise ControlError("NONFINITE_JSON")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ControlError(f"INVALID_{name}")
    return value


def number(value: Any, name: str, low: float, high: float) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
        raise ControlError(f"INVALID_{name}")
    return float(value)


def money(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ControlError("INVALID_COST")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ControlError("INVALID_COST") from exc
    if not result.is_finite() or result < 0:
        raise ControlError("INVALID_COST")
    return result


def relative(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ControlError("INVALID_PATH")
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or not path.parts or
            any(p in {".", "..", *SKIP} for p in path.parts) or
            any(c in value for c in "\\\x00\n\r*?[]") or
            any(p == ".env" or p == ".ssh" or p.endswith((".pem", ".key")) for p in path.parts)):
        raise ControlError("UNSAFE_PATH")
    return value


def inside(root: Path, value: str) -> Path:
    path = root / relative(value)
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ControlError("SYMLINK_NOT_ALLOWED")
    return path


def manifest(root: Path, *, source_input: bool = False, verifier_cache: bool = False) -> dict[str, dict]:
    """Content identity; no symlinks, devices, unbounded files, or silent truncation."""
    if root.is_symlink() or not root.is_dir():
        raise ControlError("INVALID_ARTIFACT_ROOT")
    result, total = {}, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        # A symlink hidden behind an excluded folder name must not be followed.
        excluded = SKIP if source_input else ({".git", "__pycache__", ".pytest_cache"} if verifier_cache else {".git"})
        if not source_input and any(d in SKIP - excluded for d in dirs):
            raise ControlError("RESERVED_DIRECTORY_CHANGED")
        dirs[:] = sorted(d for d in dirs if d not in excluded)
        for name in [*dirs, *sorted(names)]:
            if source_input and name == ".git":
                continue
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ControlError("SYMLINK_NOT_ALLOWED")
            if path.is_dir():
                continue
            rel = path.relative_to(root).as_posix()
            relative(rel)
            if not stat.S_ISREG(info.st_mode):
                raise ControlError("SPECIAL_FILE_NOT_ALLOWED")
            total += info.st_size
            if info.st_size > MAX_FILE or total > MAX_TOTAL or len(result) >= 10000:
                raise ControlError("ARTIFACT_SIZE_LIMIT")
            data = path.read_bytes()
            if len(data) != info.st_size:
                raise ControlError("ARTIFACT_CHANGED_DURING_READ")
            result[rel] = {"sha256": hashlib.sha256(data).hexdigest(),
                           "bytes": len(data), "executable": bool(info.st_mode & 0o111)}
    return result


def copy_snapshot(source: Path, target: Path, *, source_input: bool = False) -> dict:
    before = manifest(source, source_input=source_input)
    target.mkdir(parents=True, exist_ok=False)
    for name, info in before.items():
        destination = inside(target, name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inside(source, name), destination)
        destination.chmod(0o700 if info["executable"] else 0o600)
    if manifest(source, source_input=source_input) != before or manifest(target) != before:
        raise ControlError("SNAPSHOT_CHANGED_DURING_COPY")
    return before


def changed(before: dict, after: dict) -> list[str]:
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


@dataclass(frozen=True)
class Contract:
    payload: dict

    @classmethod
    def parse(cls, raw: Any) -> "Contract":
        required = {"schema", "id", "goal", "allowed_files", "criteria", "limits"}
        optional = {"semantic_review", "human_final", "data_policy", "supervisor"}
        if not isinstance(raw, dict) or required - raw.keys() or raw.keys() - required - optional:
            raise ControlError("INVALID_CONTRACT_FIELDS")
        data = strict_json(canonical(raw))  # defensive copy
        if type(data["schema"]) is not int or data["schema"] != 1:
            raise ControlError("UNSUPPORTED_CONTRACT_SCHEMA")
        if not isinstance(data["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", data["id"]):
            raise ControlError("INVALID_MISSION_ID")
        if not isinstance(data["goal"], str) or not 1 <= len(data["goal"].strip()) <= 16000:
            raise ControlError("INVALID_GOAL")
        files = data["allowed_files"]
        if not isinstance(files, list) or not files or len(files) > 100:
            raise ControlError("INVALID_SCOPE")
        files = [relative(p) for p in files]
        if len(set(files)) != len(files):
            raise ControlError("DUPLICATE_SCOPE")
        checks = data["criteria"]
        if not isinstance(checks, list) or not 1 <= len(checks) <= 100:
            raise ControlError("EMPTY_OR_EXCESS_CRITERIA")
        ids = []
        for c in checks:
            if not isinstance(c, dict) or set(c) != {"id", "description", "kind", "path", "expect"}:
                raise ControlError("INVALID_CRITERION_FIELDS")
            if not isinstance(c["id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", c["id"]):
                raise ControlError("INVALID_CRITERION_ID")
            if not isinstance(c["description"], str) or not c["description"].strip():
                raise ControlError("INVALID_CRITERION_DESCRIPTION")
            ids.append(c["id"])
            if not isinstance(c["kind"], str) or c["kind"] not in KINDS:
                raise ControlError("UNSUPPORTED_VERIFIER")
            if c["kind"] == "command":
                argv = c["expect"]
                if c["path"] is not None or not isinstance(argv, list) or not argv or any(
                    not isinstance(a, str) or not a or "\x00" in a for a in argv
                ):
                    raise ControlError("INVALID_VERIFIER_ARGV")
            else:
                relative(c["path"])
            if c["kind"] in {"text_equals", "text_contains", "sha256"} and not isinstance(c["expect"], str):
                raise ControlError("INVALID_EXPECTATION")
            if c["kind"] == "sha256" and not re.fullmatch(r"[0-9a-f]{64}", c["expect"]):
                raise ControlError("INVALID_SHA256")
            if c["kind"] == "json_fields" and (not isinstance(c["expect"], dict) or not c["expect"]):
                raise ControlError("INVALID_JSON_EXPECTATION")
            if c["kind"] == "csv":
                e = c["expect"]
                if not isinstance(e, dict) or set(e) != {"columns", "min_rows", "unique"}:
                    raise ControlError("INVALID_CSV_EXPECTATION")
                integer(e["min_rows"], "CSV_ROWS", 1, 100000)
                if (not isinstance(e["columns"], list) or not e["columns"] or
                        any(not isinstance(x, str) or not x for x in e["columns"]) or
                        len(set(e["columns"])) != len(e["columns"]) or
                        not isinstance(e["unique"], list) or any(not isinstance(x, str) for x in e["unique"]) or
                        not set(e["unique"]) <= set(e["columns"])):
                    raise ControlError("INVALID_CSV_COLUMNS")
        if len(set(ids)) != len(ids):
            raise ControlError("DUPLICATE_CRITERION")
        limits = data["limits"]
        if not isinstance(limits, dict) or set(limits) != {
            "shots", "seconds", "no_progress", "judge_calls", "max_usd", "reserve_usd", "unknown_cost"
        }:
            raise ControlError("INVALID_LIMIT_FIELDS")
        integer(limits["shots"], "SHOTS", 1, 20)
        number(limits["seconds"], "SECONDS", 1, 86400)
        integer(limits["no_progress"], "NO_PROGRESS", 1, 20)
        if not isinstance(limits["judge_calls"], dict) or set(limits["judge_calls"]) != set(ROLES[1:]):
            raise ControlError("INVALID_JUDGE_CAPS")
        for role, cap in limits["judge_calls"].items():
            integer(cap, "JUDGE_CAP", 0, 100)
        if not isinstance(limits["reserve_usd"], dict) or set(limits["reserve_usd"]) != set(ROLES):
            raise ControlError("INVALID_RESERVATIONS")
        for value in [limits["max_usd"], *limits["reserve_usd"].values()]:
            money(value)
        if limits["unknown_cost"] not in {"stop", "bounded_calls"}:
            raise ControlError("INVALID_UNKNOWN_COST_POLICY")
        for flag in ("semantic_review", "human_final"):
            data.setdefault(flag, False)
            if type(data[flag]) is not bool:
                raise ControlError("INVALID_REVIEW_FLAG")
        data.setdefault("data_policy", "local_only")
        if data["data_policy"] not in {"local_only", "redacted_evidence"}:
            raise ControlError("INVALID_DATA_POLICY")
        if "supervisor" in data and data["supervisor"] not in {"human", "dot"}:
            raise ControlError("INVALID_SUPERVISOR")
        return cls(data)

    @property
    def hash(self) -> str:
        return digest(self.payload)

    @property
    def limits(self) -> dict:
        return self.payload["limits"]
