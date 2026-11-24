#!/usr/bin/env python3
"""research-kb-qa pipeline engine (stdlib only).

Architecture: contract-first + single manifest + append-only provenance ledger.
All stages read/write one canonical intermediate representation
`<work>/state.json`. The result.json for the grader is derived from it, so partial
delivery always reflects the latest honest state.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone

try:
    from datetime import UTC  # py3.11+
except ImportError:  # pragma: no cover - fallback for older interpreters
    UTC = timezone.utc

VERSION = "1.1.0"
SIZE_LIMIT_PER_FILE = 2 * 1024 * 1024  # 2MB text-read cap; larger files get hashed but not fully parsed

FILE_KIND_EXT = {
    ".md": "text", ".markdown": "text", ".txt": "text", ".csv": "text",
    ".json": "text", ".yaml": "text", ".yml": "text", ".xml": "text",
    ".html": "text", ".htm": "text", ".tex": "text", ".bib": "text",
    ".py": "text", ".r": "text", ".ipynb": "text", ".log": "text",
    ".pdf": "pdf",
    ".doc": "office", ".docx": "office", ".xls": "office", ".xlsx": "office",
    ".ppt": "office", ".pptx": "office", ".odt": "office", ".ods": "office",
    ".odp": "office",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".tif": "image", ".tiff": "image", ".svg": "image",
    ".bmp": "image",
    ".zip": "archive", ".tar": "archive", ".gz": "archive", ".tgz": "archive",
    ".7z": "archive", ".rar": "archive",
}

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")

_CASE_ID_KEYS = ("case_id", "caseId", "id", "challenge_id")
_QUESTIONS_KEYS = ("questions", "qa", "queries", "tasks")
_CONTRACT_KEYS = ("output_contract", "outputContract", "contract")
_MATERIAL_KEYS = ("materials", "material_paths", "paths", "files")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _utc_clock_second() -> float:
    return time.time()


def _log(state: dict, stage: str, action: str, status: str, detail: str = "") -> None:
    """Append an entry to the append-only provenance ledger."""
    seq = len(state.setdefault("change_log", [])) + 1
    entry = {
        "seq": seq,
        "time": _now_iso(),
        "stage": stage,
        "action": action,
        "status": status,
    }
    if detail:
        entry["detail"] = detail
    state["change_log"].append(entry)


def load_json_safe(path: str) -> object:
    """Read a JSON file tolerating BOM/bad encoding. Returns None on failure."""
    if not path or not os.path.isfile(path):
        return None
    data = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                data = json.load(fh)
            break
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            continue
    return data


def read_text_safe(path: str, max_bytes: int = SIZE_LIMIT_PER_FILE) -> tuple:
    """Return (text_or_None, encoding_or_None, error_or_None)."""
    if not os.path.isfile(path):
        return None, None, "missing_file"
    size = os.path.getsize(path)
    if size > max_bytes:
        return None, None, "size_limit_skipped"
    if _is_binary(path):
        return None, None, "binary_sniffed"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except (UnicodeDecodeError, OSError):
            continue
    return None, None, "undecodable"


def _is_binary(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
        return b"\x00" in head
    except OSError:
        return False


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def classify_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    kind = FILE_KIND_EXT.get(ext)
    if kind:
        return kind
    if _is_binary(path):
        return "unknown"
    return "text"


def _extract_frontmatter(text: str) -> dict:
    """Best-effort Markdown/YAML frontmatter extraction. Never raises."""
    if text is None:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().strip('"\'')
        val = val.strip().strip('"\'')
        if key:
            fm[key] = val
    return fm


_VERSION_MARKERS = (
    r"(19|20)\d{2}[-_.]?\d{2}[-_.]?\d{2}",   # dates
    r"(19|20)\d{6}",                          # compact dates
    r"[-_.]?(v|ver|version|rev|r)[-_]?\d+",   # version tokens
    r"[-_.]?(final|draft|copy|old|new|backup|archive|accepted|submitted|published)",
)


def version_base(name: str) -> str:
    """Strip version/date markers to derive a version family base name."""
    stem = os.path.splitext(name)[0]
    for pat in _VERSION_MARKERS:
        stem = re.sub(pat, "", stem, flags=re.I)
    stem = re.sub(r"[-_.\s]+", "_", stem).strip("_")
    return stem.lower()


def version_tag(name: str) -> str | None:
    tags = []
    if re.search(r"(19|20)\d{2}[-_.]?\d{2}[-_.]?\d{2}|(19|20)\d{6}", name):
        tags.append("date")
    if re.search(r"[-_.]?(v|ver|version|rev|r)[-_]?\d+", name, re.I):
        tags.append("version")
    if re.search(r"[-_.]?(final|draft|copy|old|new|backup|archive|accepted|submitted|published)", name, re.I):
        tags.append("flag")
    return ",".join(tags) if tags else None


def _echo_contract(case: dict | None) -> dict:
    """Recover output_contract keys verbatim; empty/missing => placeholder keys."""
    if not isinstance(case, dict):
        return {}
    raw = None
    for k in _CONTRACT_KEYS:
        if k in case:
            raw = case[k]
            break
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, list):
        return {"items": list(raw)}
    return {}


def _extract_case_id(case: dict | None) -> str | None:
    if not isinstance(case, dict):
        return None
    for k in _CASE_ID_KEYS:
        v = case.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v)
    return None


def _extract_questions(case: dict | None) -> list:
    if not isinstance(case, dict):
        return []
    raw = []
    for k in _QUESTIONS_KEYS:
        if k in case:
            v = case[k]
            if isinstance(v, list):
                raw = v
            elif isinstance(v, (str, dict)):
                raw = [v]
            break
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append({"question": item})
        elif isinstance(item, dict) and isinstance(item.get("question"), str):
            out.append(item)
        elif isinstance(item, dict) and (item.get("q") or item.get("query")):
            out.append({"question": str(item.get("q") or item.get("query")), "id": item.get("id")})
    return out


def _material_paths(case: dict | None) -> list:
    if not isinstance(case, dict):
        return []
    out = []
    for k in _MATERIAL_KEYS:
        v = case.get(k)
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    out.append(x)
                elif isinstance(x, dict) and isinstance(x.get("path"), str):
                    out.append(x["path"])
        elif isinstance(v, str):
            out.append(v)
    return out


def _walk_files(root: str, max_files: int = 5000) -> tuple:
    """Yield (abs_path, rel_path) for all files under root, deterministically."""
    files = []
    dirs = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        reldir = os.path.relpath(dirpath, root)
        if reldir != ".":
            dirs.append((reldir, dirpath))
        for fn in filenames:
            abs_p = os.path.join(dirpath, fn)
            if os.path.islink(abs_p):
                continue  # no symlinks in skill resources; guard anyway
            files.append((abs_p, os.path.relpath(abs_p, root)))
            if len(files) >= max_files:
                return files, dirs
    return files, dirs


def _file_mode(path: str) -> dict:
    try:
        st = os.stat(path)
    except OSError:
        return {}
    mode = st.st_mode & 0o7777
    readable = os.access(path, os.R_OK)
    writable = os.access(path, os.W_OK)
    return {
        "mode": format(mode, "o"),
        "readable": bool(readable),
        "writable": bool(writable),
        "uid": getattr(st, "st_uid", None),
        "gid": getattr(st, "st_gid", None),
    }


def _pdf_page_hint(path: str) -> dict:
    """Return {'pages': n} when pdftotext is available, else {'needs_ocr': true}."""
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return {"needs_ocr": True, "reason": "pdftotext_not_found"}
    try:
        import subprocess
        proc = subprocess.run(
            [pdftotext, path, "-", "-l", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        )
        # Counting pages reliably requires -listenc or parsing; keep it honest:
        return {"pages": "not_counted", "pdftotext_ok": proc.returncode == 0}
    except Exception as exc:  # noqa: BLE001 - degrade, never crash
        return {"needs_ocr": True, "reason": f"pdftotext_failed:{type(exc).__name__}"}


class Engine:
    def __init__(self, input_dir: str, output_dir: str, work_dir: str):
        self.input_dir = os.path.abspath(input_dir)
        self.output_dir = os.path.abspath(output_dir)
        self.work_dir = os.path.abspath(work_dir)
        for d in (self.output_dir, self.work_dir):
            os.makedirs(d, exist_ok=True)
        self.state_path = os.path.join(self.work_dir, "state.json")

    # -- persistence -----------------------------------------------------
    def _load_state(self) -> dict:
        s = load_json_safe(self.state_path)
        return s if isinstance(s, dict) else {}

    def _save_state(self, state: dict) -> None:
        state["runtime"]["state_mtime"] = _now_iso()
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")

    def _fresh_state(self) -> dict:
        return {
            "schema_version": VERSION,
            "case_id": None,
            "status": "partial",
            "output_contract_echo": {},
            "sources": [],
            "uncertainty": [],
            "pending_human": [],
            "errors": [],
            "degradations": [],
            "limitations": [],
            "failures_recovery": [],
            "answers": [],
            "library": {},
            "change_log": [],
            "generated_artifacts": [],
            "runtime": {"engine_version": VERSION, "started_at": _now_iso()},
        }

    # -- stage 0: scaffold ----------------------------------------------
    def scaffold(self) -> dict:
        state = self._fresh_state()
        case_path = os.path.join(self.input_dir, "case.json")
        task_path = os.path.join(self.input_dir, "task.md")

        case = load_json_safe(case_path)
        task_text, _, task_err = read_text_safe(task_path)

        if case is None and os.path.isfile(case_path):
            state["status"] = "input_error"
            state["errors"].append("case.json exists but is not valid JSON; continuing with empty contract")
            _log(state, "scaffold", "read_case_json", "error", "invalid_json")
        elif case is None:
            state["status"] = "input_error"
            state["errors"].append("case.json missing")
            _log(state, "scaffold", "read_case_json", "error", "missing")

        state["case_id"] = _extract_case_id(case) or "unknown_case"
        contract = _echo_contract(case)
        state["output_contract_echo"] = contract

        if task_err:
            state["degradations"].append(f"task.md unreadable: {task_err}")
            _log(state, "scaffold", "read_task_md", "degraded", task_err)
        else:
            state["runtime"]["task_md_bytes"] = len(task_text or "")
            _log(state, "scaffold", "read_task_md", "ok")

        state["runtime"]["questions"] = _extract_questions(case)
        state["runtime"]["material_hints"] = _material_paths(case)
        state["runtime"]["contract_key_count"] = len(contract)

        _log(state, "scaffold", "emit_partial_skeleton", "ok",
             f"case_id={state['case_id']} contract_keys={len(contract)} status=partial")
        self._save_state(state)
        self._emit_result(state)
        return state

    # -- stage 1: inventory ---------------------------------------------
    def inventory(self) -> dict:
        state = self._load_state()
        if not state:
            state = self.scaffold()

        files, dirs = _walk_files(self.input_dir)
        sources = []
        duplicate_map: dict = {}
        empty_files = []
        version_map: dict = {}
        file_type_counter: dict = {}
        encodings: dict = {}
        frontmatter_files = 0
        frontmatter_fields: set = set()
        permissions = []
        unreadable = []

        case = load_json_safe(os.path.join(self.input_dir, "case.json"))
        material_hints = set(_material_paths(case))

        for abs_p, rel in files:
            if rel in ("case.json", "task.md"):
                continue  # meta inputs, not materials
            sha = sha256_of(abs_p)
            size = os.path.getsize(abs_p)
            kind = classify_file(abs_p)
            fm = {}
            tip = None
            enc = None
            orig = "task_input"
            for h in material_hints:
                if rel == h or rel.replace("\\", "/").endswith(h.lstrip("./")):
                    orig = "case_material"
                    break
            if kind == "text":
                text, enc, err = read_text_safe(abs_p)
                if err:
                    unreadable.append({"path": rel, "sha256": sha, "reason": err})
                else:
                    tip = text[:1600] if text else ""
                    if tip and tip.startswith("---"):
                        fm = _extract_frontmatter(text)
                        if fm:
                            frontmatter_files += 1
                            frontmatter_fields.update(fm.keys())
            elif kind == "pdf":
                hint = _pdf_page_hint(abs_p)
                if hint.get("needs_ocr"):
                    state["degradations"].append(f"pdf without text layer hint: {rel}")
                tip = hint
            elif kind in ("office", "archive", "image", "unknown"):
                tip = {"sniff": kind, "needs_converter": kind in ("office", "archive")}

            src = {
                "path": rel, "sha256": sha, "type": kind, "size": size,
                "mtime": datetime.fromtimestamp(
                    os.path.getmtime(abs_p), UTC).isoformat(timespec="seconds"),
                "version_tag": version_tag(os.path.basename(rel)),
                "source": orig,
                "permissions": _file_mode(abs_p),
            }
            if enc:
                src["text_encoding"] = enc
                encodings[enc] = encodings.get(enc, 0) + 1
            if fm:
                src["frontmatter"] = fm
            if tip is not None:
                src["preview"] = tip if isinstance(tip, str) else None
                if not isinstance(tip, str):
                    src["extract_hint"] = tip
            sources.append(src)

            file_type_counter[kind] = file_type_counter.get(kind, 0) + 1
            if sha:
                duplicate_map.setdefault(sha, []).append(rel)
            if size == 0:
                empty_files.append(rel)
            else:
                base = version_base(os.path.basename(rel))
                version_map.setdefault(base, []).append(src)
            permissions.append({"path": rel, **src["permissions"]})

        sources.sort(key=lambda s: (s["path"].count(os.sep), s["path"]))
        duplicate_groups = sorted(
            ({"sha256": sha, "paths": sorted(p)} for sha, p in duplicate_map.items() if len(p) > 1),
            key=lambda g: g["sha256"],
        )
        version_families = sorted(
            (
                {
                    "base": base,
                    "members": sorted(
                        ({"path": m["path"], "sha256": m["sha256"], "version_tag": m.get("version_tag")}
                         for m in members),
                        key=lambda m: m["path"],
                    ),
                }
                for base, members in version_map.items() if len(members) > 1
            ),
            key=lambda f: f["base"],
        )

        directory_tree = {}
        for reldir, dirpath in dirs:
            directory_tree[reldir] = {
                "mtime": datetime.fromtimestamp(os.path.getmtime(dirpath), UTC).isoformat(timespec="seconds"),
                "file_count": sum(1 for f in files if os.path.dirname(f[1]) == reldir),
            }

        state["sources"] = sources
        state["library"] = {
            "totals": {"files": len(sources), "dirs": len(dirs), "bytes": sum(s["size"] for s in sources)},
            "file_types": file_type_counter,
            "metadata_summary": {
                "with_frontmatter": frontmatter_files,
                "frontmatter_fields_seen": sorted(frontmatter_fields),
                "text_encodings": encodings,
            },
            "directory_tree": directory_tree,
            "duplicate_groups": duplicate_groups,
            "empty_files": empty_files,
            "version_families": version_families,
            "permissions_and_sources": permissions,
        }
        for item in unreadable:
            state["uncertainty"].append({
                "level": "unreadable", "item": item["path"],
                "reason": item["reason"], "sources": [item["path"]],
            })
        state["failures_recovery"] = [
            {"item": p, "action": "skipped_unreadable", "recovery": "marked_unreadable_and_kept_sha256"}
            for p in [u["path"] for u in unreadable]
        ]

        _log(state, "inventory", "inventory_complete", "ok",
             f"files={len(sources)} dups={len(duplicate_groups)} versions={len(version_families)} unreadable={len(unreadable)}")
        self._save_state(state)
        self._emit_result(state)
        return state

    # -- stage 2: answer -------------------------------------------------
    def answer(self) -> dict:
        state = self._load_state()
        if not state:
            state = self.scaffold()
        questions = state.get("runtime", {}).get("questions", [])
        answers_path = os.path.join(self.work_dir, "answers.json")
        if not os.path.isfile(answers_path):
            # Build honest skeletons: no evidence yet, so insufficient_evidence.
            answers = []
            for q in questions:
                answers.append({
                    "question": q.get("question"),
                    "answer": None,
                    "evidence": [],
                    "uncertainty": "insufficient_evidence",
                    "missing": "answer_needs_verification_against_material",
                })
            _log(state, "answer", "emit_answer_skeletons", "partial",
                 f"skeletons={len(answers)} (agent must fill evidence)")
        else:
            loaded = load_json_safe(answers_path)
            answers = loaded.get("answers", []) if isinstance(loaded, dict) else []
            _log(state, "answer", "merge_answers", "ok", f"answers={len(answers)}")
        state["answers"] = answers
        state["uncertainty"] = state.get("uncertainty", [])
        if not answers and questions:
            state["status"] = "partial"
        _log(state, "answer", "answer_stage_done", "ok", f"answers={len(answers)}")
        self._save_state(state)
        self._emit_result(state)
        return state

    # -- stage 3: finalize ----------------------------------------------
    def finalize(self) -> dict:
        state = self._load_state()
        if not state:
            state = self.scaffold()
        # Merge answers.json if present and answers were not yet merged.
        answers_path = os.path.join(self.work_dir, "answers.json")
        if os.path.isfile(answers_path):
            loaded = load_json_safe(answers_path)
            if isinstance(loaded, dict) and isinstance(loaded.get("answers"), list):
                if not state.get("answers"):
                    state["answers"] = loaded["answers"]
                if isinstance(loaded.get("uncertainty"), list):
                    acc = {json.dumps(u, sort_keys=True) for u in state.get("uncertainty", [])}
                    for u in loaded["uncertainty"]:
                        acc.add(json.dumps(u, sort_keys=True))
                    state["uncertainty"] = [json.loads(u) for u in sorted(acc)]

        # Recompute honest status.
        status = self._derive_status(state)
        state["status"] = status
        for a in state.get("answers", []):
            if a.get("uncertainty") == "insufficient_evidence" and not a.get("answer"):
                state["pending_human"].append({
                    "item": a.get("question"), "reason": "insufficient_evidence",
                })

        # Emit auxiliary artifacts.
        artifacts = []
        inv_md = os.path.join(self.output_dir, "inventory.md")
        self._write_inventory_md(state, inv_md)
        artifacts.append(os.path.relpath(inv_md, self.output_dir))
        evmap = os.path.join(self.output_dir, "evidence_map.json")
        self._write_evidence_map(state, evmap)
        artifacts.append(os.path.relpath(evmap, self.output_dir))
        state["generated_artifacts"] = artifacts

        _log(state, "finalize", "finalize_complete", status, f"status={status}")
        self._save_state(state)
        self._emit_result(state)
        return state

    # -- helpers ---------------------------------------------------------
    def _derive_status(self, state: dict) -> str:
        if state.get("errors") and not state.get("sources"):
            return "input_error"
        if not state.get("sources") and not state.get("library", {}).get("totals", {}).get("files"):
            return "insufficient_evidence"
        answers = state.get("answers", [])
        if not answers:
            return "partial"
        if any(not a.get("answer") for a in answers):
            return "partial"
        if any(a.get("uncertainty") in ("insufficient_evidence", "unreadable", "conflict") for a in answers):
            return "partial"
        return "ok"

    def _emit_result(self, state: dict) -> None:
        """Derive and write result.json (respecting contract keys verbatim)."""
        reserved = {"case_id", "status", "sources", "uncertainty", "pending_human",
                    "change_log", "answers", "library", "errors", "degradations",
                    "limitations", "failures_recovery", "runtime", "generated_artifacts",
                    "schema_version", "output_contract_echo", "output_contract"}
        result = {
            "case_id": state.get("case_id"),
            "status": state.get("status", "partial"),
            "sources": state.get("sources", []),
            "uncertainty": state.get("uncertainty", []),
            "pending_human": state.get("pending_human", []),
            "change_log": state.get("change_log", []),
            "answers": state.get("answers", []),
            "library": state.get("library", {}),
            "errors": state.get("errors", []),
            "degradations": state.get("degradations", []),
            "limitations": state.get("limitations", []),
            "failures_recovery": state.get("failures_recovery", []),
            "runtime": state.get("runtime", {}),
            "generated_artifacts": state.get("generated_artifacts", []),
        }
        contract = state.get("output_contract_echo", {})
        if isinstance(contract, dict):
            for k, v in contract.items():
                if k in reserved and result.get(k) not in (None, [], {}):
                    state.setdefault("errors", [])
                    coll = f"contract key '{k}' collides with reserved key; reserved value kept"
                    if coll not in state["errors"]:
                        state["errors"].append(coll)
                    continue
                result[k] = v
        out_path = os.path.join(self.output_dir, "result.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")

    def _write_inventory_md(self, state: dict, path: str) -> None:
        lib = state.get("library", {})
        lines = ["# Inventory", "",
                 f"- case_id: {state.get('case_id')}",
                 f"- files: {lib.get('totals', {}).get('files', 0)}",
                 f"- bytes: {lib.get('totals', {}).get('bytes', 0)}", ""]
        lines.append("## File types")
        for k, v in sorted(lib.get("file_types", {}).items()):
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append("## Duplicate groups (same SHA256)")
        for g in lib.get("duplicate_groups", []):
            lines.append(f"- {g['sha256'][:12]}: {', '.join(g['paths'])}")
        lines.append("")
        lines.append("## Version families")
        for f in lib.get("version_families", []):
            members = "; ".join(m["path"] for m in f["members"])
            lines.append(f"- {f['base']}: {members}")
        lines.append("")
        lines.append("## Sources")
        for s in state.get("sources", []):
            lines.append(f"- {s['path']} | {s['type']} | {s['sha256'][:12]} | {s.get('version_tag') or '-'}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def _write_evidence_map(self, state: dict, path: str) -> None:
        ev = []
        for a in state.get("answers", []):
            for e in a.get("evidence", []):
                ev.append({"question": a.get("question"), **e})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"evidence": ev}, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")


def dirsinh(dirs: list) -> int:  # helper kept for clarity (inlined in a moment)
    return len(dirs)


STAGES = ("scaffold", "inventory", "answer", "finalize", "run", "inspect", "selftest")


def run_stage(stage: str, input_dir: str, output_dir: str, work_dir: str) -> dict:
    eng = Engine(input_dir, output_dir, work_dir)
    if stage == "run":
        eng.scaffold()
        eng.inventory()
        eng.answer()
        return eng.finalize()
    if stage in ("scaffold", "inventory", "answer", "finalize"):
        return getattr(eng, stage)()
    if stage == "inspect":
        state = eng._load_state()
        if not state:
            state = eng.scaffold()
        print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
        return state
    if stage == "selftest":
        return eng._load_state()
    raise ValueError(f"unknown stage: {stage}")


if __name__ == "__main__":  # pragma: no cover - main.py is the entry point
    import argparse
    ap = argparse.ArgumentParser(description="research-kb-qa engine (direct run)")
    ap.add_argument("stage", nargs="?", default="run", choices=STAGES)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args()
    try:
        run_stage(a.stage, a.input, a.output, a.work)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(1)