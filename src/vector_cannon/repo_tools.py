from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

IGNORED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".next", "dist", "build", "coverage"}
BLOCKED_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa", "id_ed25519", "credentials.json", "credentials.*.json")
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

@dataclass
class RepositoryReader:
    root: Path
    max_file_bytes: int = 96_000
    read_files: set[str] = field(default_factory=set)
    searched_files: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Repository root does not exist: {self.root}")

    def _resolve(self, relative: str) -> Path:
        candidate = (self.root / (relative or ".")).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Path escapes repository root.")
        return candidate

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() if path != self.root else "."

    def _is_blocked(self, path: Path) -> bool:
        rel = self._relative(path)
        parts = set(path.relative_to(self.root).parts) if path != self.root else set()
        if parts & IGNORED_DIRS:
            return True
        name = path.name
        return any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern) for pattern in BLOCKED_PATTERNS)

    def _resolved_inside_root(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if resolved != self.root and self.root not in resolved.parents:
            return None
        return resolved

    @staticmethod
    def _looks_binary(raw: bytes) -> bool:
        return b"\x00" in raw[:4096]

    @staticmethod
    def _redact(text: str) -> str:
        for pattern in SECRET_PATTERNS:
            text = pattern.sub("[REDACTED_SECRET]", text)
        return text

    def list_tree(self, path: str = ".", depth: int = 2, max_entries: int = 250) -> dict[str, Any]:
        base = self._resolve(path)
        if not base.exists():
            return {"error": "path_not_found", "path": path}
        if self._is_blocked(base):
            return {"error": "path_blocked", "path": path}
        if base.is_file():
            return {"entries": [self._relative(base)]}
        depth = max(0, min(int(depth), 6))
        max_entries = max(1, min(int(max_entries), 1000))
        entries: list[str] = []
        base_depth = len(base.parts)
        for current, dirs, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            current_depth = len(current_path.parts) - base_depth
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not (current_path / d).is_symlink() and not self._is_blocked(current_path / d))
            if current_depth > depth:
                dirs[:] = []
                continue
            if current_path != base:
                entries.append(self._relative(current_path) + "/")
            if current_depth <= depth:
                for filename in sorted(files):
                    file_path = current_path / filename
                    if not self._is_blocked(file_path):
                        entries.append(self._relative(file_path))
                    if len(entries) >= max_entries:
                        return {"entries": entries, "truncated": True}
        return {"entries": entries, "truncated": False}

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        target = self._resolve(path)
        if not target.is_file():
            return {"error": "file_not_found", "path": path}
        if self._is_blocked(target):
            return {"error": "file_blocked", "path": path}
        raw = target.read_bytes()
        if len(raw) > self.max_file_bytes:
            return {"error": "file_too_large", "path": path, "bytes": len(raw), "limit": self.max_file_bytes}
        if self._looks_binary(raw):
            return {"error": "binary_file", "path": path}
        lines = self._redact(raw.decode("utf-8", errors="replace")).splitlines()
        start = max(1, int(start_line))
        end = len(lines) if end_line is None else max(start, min(int(end_line), len(lines)))
        end = min(end, start + 499)
        selected = lines[start - 1:end]
        rel = self._relative(target)
        self.read_files.add(rel)
        return {"path": rel, "start_line": start, "end_line": end, "total_lines": len(lines), "content": "\n".join(f"{number}: {line}" for number, line in enumerate(selected, start=start)), "truncated": end < len(lines)}

    def _iter_search_candidates(self, base: Path) -> Iterator[Path]:
        if base.is_file():
            yield base
            return
        for current, dirs, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            kept_dirs: list[str] = []
            for dirname in sorted(dirs):
                candidate = current_path / dirname
                if dirname in IGNORED_DIRS or candidate.is_symlink() or self._is_blocked(candidate):
                    continue
                if self._resolved_inside_root(candidate) is not None:
                    kept_dirs.append(dirname)
            dirs[:] = kept_dirs
            for filename in sorted(files):
                candidate = current_path / filename
                if self._is_blocked(candidate) or self._resolved_inside_root(candidate) is None:
                    continue
                yield candidate

    def search_text(self, query: str, path: str = ".", max_matches: int = 40) -> dict[str, Any]:
        if not query:
            return {"error": "empty_query"}
        base = self._resolve(path)
        if not base.exists() or self._is_blocked(base):
            return {"error": "path_unavailable", "path": path}
        max_matches = max(1, min(int(max_matches), 100))
        needle = query.casefold()
        matches: list[dict[str, Any]] = []
        for candidate in self._iter_search_candidates(base):
            try:
                raw = candidate.read_bytes()
            except OSError:
                continue
            if len(raw) > self.max_file_bytes or self._looks_binary(raw):
                continue
            rel = self._relative(candidate)
            self.searched_files.add(rel)
            text = self._redact(raw.decode("utf-8", errors="replace"))
            for line_no, line in enumerate(text.splitlines(), start=1):
                if needle in line.casefold():
                    matches.append({"path": rel, "line": line_no, "text": line[:500]})
                    if len(matches) >= max_matches:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "list_tree": result = self.list_tree(**arguments)
            elif name == "read_file": result = self.read_file(**arguments)
            elif name == "search_text": result = self.search_text(**arguments)
            else: result = {"error": "unknown_tool", "name": name}
        except (OSError, ValueError, TypeError) as exc:
            result = {"error": "tool_error", "message": str(exc)}
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def tool_schema() -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": "list_tree", "description": "List repository paths. Read-only. Use this to map the repository before reading files.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "depth": {"type": "integer", "minimum": 0, "maximum": 6, "default": 2}, "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 250}}, "additionalProperties": False}}},
            {"type": "function", "function": {"name": "read_file", "description": "Read up to 500 lines from one non-secret text file in the repository.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1, "default": 1}, "end_line": {"type": ["integer", "null"], "minimum": 1}}, "required": ["path"], "additionalProperties": False}}},
            {"type": "function", "function": {"name": "search_text", "description": "Search text files in the repository for a literal case-insensitive string.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string", "default": "."}, "max_matches": {"type": "integer", "minimum": 1, "maximum": 100, "default": 40}}, "required": ["query"], "additionalProperties": False}}},
        ]
