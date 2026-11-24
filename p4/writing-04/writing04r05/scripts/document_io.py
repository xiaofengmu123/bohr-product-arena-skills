"""Deterministic file I/O helpers for the citation-management skill.

Pure standard library. All text reads are tolerant (utf-8-sig with a latin-1
fallback), JSON reads degrade to None on bad input, and writes are atomic via
a temp file + os.replace so an interrupted run never leaves a half-written
artifact. A Ledger object accumulates errors / degradations / warnings instead
of raising, which keeps the 900s harness run alive through bad material.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB single-file cap


class Ledger:
    """Accumulates non-fatal problems so the caller can record them honestly."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.degradations: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        if msg not in self.errors:
            self.errors.append(msg)

    def degrade(self, msg: str) -> None:
        if msg not in self.degradations:
            self.degradations.append(msg)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path: Path, ledger: Ledger) -> str:
    """Read a text file with tolerant encoding; returns '' on any failure."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        ledger.error(f"{path.name}: unreadable ({exc})")
        return ""
    if size > MAX_FILE_BYTES:
        ledger.degrade(f"{path.name}: skipped (size {size} > {MAX_FILE_BYTES})")
        return ""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            ledger.error(f"{path.name}: read failed ({exc})")
            return ""
    ledger.error(f"{path.name}: undecodable text")
    return ""


def load_json_file(path: Path, ledger: Ledger) -> dict | list | None:
    text = read_text(path, ledger)
    if not text:
        return None
    return load_json_text(text, path.name, ledger)


def load_json_text(text: str, name: str, ledger: Ledger) -> dict | list | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        ledger.error(f"{name}: bad JSON ({exc})")
        return None


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, obj: object) -> None:
    write_text_atomic(
        path,
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def list_files_recursive(root: Path) -> list[Path]:
    """Deterministically sorted list of files under root (no symlink follow)."""
    if not root.exists():
        return []
    out: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            out.append(p)
    return sorted(out, key=lambda x: str(x).replace("\\", "/"))
