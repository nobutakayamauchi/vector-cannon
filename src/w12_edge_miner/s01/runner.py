from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .frozen import sha256, verify_frozen
from .models import load_audit


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def write_new_or_identical(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError("output symlinks are not allowed")
    if path.exists():
        if path.read_text() != content:
            raise FileExistsError(f"Refusing to overwrite different evidence: {path}")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def markdown(result: dict[str, Any]) -> str:
    lines = ["# W12 Edge Miner — S01 Wallet Data Capability Audit", "",
             f"**{result['final_status']}** — PAPER / READ-ONLY; LIVE HARD LOCKED.", "",
             f"Source: `{result['source_id']}`", f"Product: {result['venue_product']}", "",
             "## Capability evidence", "", "| Capability | Status | Evidence / limitation |",
             "| --- | --- | --- |"]
    for c in result["capabilities"]:
        detail = c["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {c['name']} | {c['state']} | {detail} ({', '.join(c['evidence_ids'])}) |")
    lines += ["", "## Audit questions", ""]
    for row in result["audit_questions"]:
        lines += [f"{row['number']}. **{row['question']}** {row['answer']}"]
    lines += ["", "## Blockers", ""]
    lines += [f"- {b['field']}: {b['code']} — {b['detail']}" for b in result["blockers"]] or ["- None"]
    lines += ["", "## Provenance", ""]
    for p in result["provenance"]:
        lines += [f"- **{p['evidence_id']}** [{p['title']}]({p['uri']}); retrieved {p['retrieved_at']}; {p['kind']}.",
                  f"  Claim relied upon: {p['claim']}", f"  SHA-256: `{p['sha256'] or 'UNKNOWN'}`."]
    lines += ["", "## Read-only probes", "", "Request latency is this environment's HTTP duration, not source publication/finality delay.", ""]
    for p in result["probes"]:
        lines += [f"- GET {p['url']} → HTTP {p['http_status']}; {p['outcome']}; received {p['observed_at']}; {p['request_latency_ms']} ms."]
    lines += ["", "## Frozen inputs and validation", "", "```json",
              encoded({"repository": result["repository"], "frozen_inputs": result["frozen_inputs"]}).rstrip(), "```", "",
              "Reproduction and executed validation: see VALIDATION.md in this directory.", "",
              "The audit consumes captured evidence offline. No winner roster, S02 ledger, new Shot, inference call or live order was produced.",
              "LIVE ORDERS SENT: 0", "NEXT_ACTION: " + result["next_action"], ""]
    return "\n".join(lines)


def run(evidence_path: Path, output_dir: Path, roots: dict[str, Path]) -> dict[str, Any]:
    for name in ("w11", "w12"):
        if name in roots and output_dir.resolve().is_relative_to(roots[name].resolve()):
            raise ValueError("output inside frozen evidence root")
    data = json.loads(evidence_path.read_text())
    audit = load_audit(data)
    result = audit.to_payload()
    result["frozen_inputs"] = verify_frozen(data["frozen_pins"], roots)
    if result["frozen_inputs"]["status"] != "PASS":
        result["blockers"].append({"field": "frozen_inputs", "code": result["frozen_inputs"]["status"],
                                    "detail": "Original W11/W12 inputs not fully verified unchanged"})
        result["final_status"] = "BLOCKED_WALLET_DATA_SOURCE"
    result.update(evidence_input_sha256=sha256(evidence_path), repository=data["repository"],
                  audit_questions=data["audit_questions"], probes=data.get("probes", []),
                  live_order_path="UNREACHABLE", live_orders_sent=0,
                  read_only_boundary="PASS", runner_mode="OFFLINE_CAPTURED_EVIDENCE")
    result["next_action"] = ("STOP_BLOCKED" if result["blockers"] else "S02 canonical source schema + immutable event ledger")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"wallet_data_capability.json": encoded(result), "WALLET_DATA_CAPABILITY_AUDIT.md": markdown(result)}
    # Preflight both artifacts before any write; never replace different reports.
    for name, content in outputs.items():
        path = output_dir / name
        if path.is_symlink() or (path.exists() and path.read_text() != content):
            raise FileExistsError(f"Existing output differs: {path}")
    for name, content in outputs.items():
        write_new_or_identical(output_dir / name, content)
    return result
