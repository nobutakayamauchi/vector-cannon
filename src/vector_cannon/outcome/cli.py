"""The new entry point delegates all old commands to the original CLI."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .adapters import CodexAgent, ImportAgent, NativeJev, TextJudge
from .engine import OutcomeEngine
from .model import Contract, ControlError, canonical, strict_json
from .store import Store


def adapters(args):
    agent = ImportAgent(args.candidate) if args.candidate else CodexAgent(args.codex_path, args.worker_model)
    judges = {}
    if args.jev:
        judges["jev"] = NativeJev()
    for role in ("sol", "astra"):
        model = getattr(args, role + "_model")
        if model:
            judges[role] = TextJudge(role, model)
    return agent, judges


def _adapter_flags(parser):
    parser.add_argument("--candidate", type=Path, help="Assess a user-selected export without starting an AI worker.")
    parser.add_argument("--codex-path", default="codex")
    parser.add_argument("--worker-model", default=None)
    parser.add_argument("--jev", action="store_true", help="Opt in to the real Jev decision endpoint.")
    parser.add_argument("--sol-model", default=None, help="Explicit AI Gateway model ID for the Sol role.")
    parser.add_argument("--astra-model", default=None, help="Explicit AI Gateway model ID for the Astra role.")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vector-cannon outcome")
    sub = p.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan", help="Validate the immutable contract. No writes and no model calls.")
    plan.add_argument("--contract", required=True, type=Path)
    start = sub.add_parser("run", help="Start a new outcome session; execution is opt-in.")
    start.add_argument("--contract", required=True, type=Path)
    start.add_argument("--source", required=True, type=Path)
    start.add_argument("--session", required=True, type=Path)
    start.add_argument("--execute", action="store_true")
    start.add_argument("--step", action="store_true", help="Stop at a durable checkpoint after one external operation.")
    _adapter_flags(start)
    resume = sub.add_parser("resume", help="Resume a safe checkpoint; never replay an uncertain operation.")
    resume.add_argument("--session", required=True, type=Path)
    resume.add_argument("--execute", action="store_true")
    resume.add_argument("--step", action="store_true")
    _adapter_flags(resume)
    accept = sub.add_parser("accept", help="Accept a previously verified human-final delivery by evidence hash.")
    accept.add_argument("--session", required=True, type=Path)
    accept.add_argument("--evidence-hash", required=True)
    _adapter_flags(accept)
    for action in ("status", "report"):
        item = sub.add_parser(action)
        item.add_argument("--session", required=True, type=Path)
    return p


def outcome_main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action in {"status", "report"}:
            result = Store(args.session).report()
        elif args.action == "plan" or (args.action == "run" and not args.execute):
            contract = Contract.parse(strict_json(args.contract.read_text(encoding="utf-8")))
            result = {"state": "PLAN_ONLY", "contract_hash": contract.hash, "contract": contract.payload,
                      "model_calls": 0, "writes": 0, "provider_readiness": "NOT_PROBED"}
        elif args.action == "resume" and not args.execute:
            result = Store(args.session).report()
            result["execution_requested"] = False
        else:
            agent, judges = adapters(args)
            if judges and not os.environ.get("AI_GATEWAY_API_KEY"):
                raise ControlError("AI_GATEWAY_KEY_NOT_CONFIGURED")
            if args.action == "run":
                contract = Contract.parse(strict_json(args.contract.read_text(encoding="utf-8")))
                engine = OutcomeEngine.create(contract, args.source, args.session, agent, judges)
            else:
                engine = OutcomeEngine(Store(args.session), agent, judges)
            if args.action == "accept":
                result = engine.accept(args.evidence_hash)
            else:
                result = engine.run(max_operations=1 if args.step else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("state") in {"PLAN_ONLY", "VERIFIED_DONE", "VERIFY", "READY", "JUDGE", "SEAL"} else 1
    except (ControlError, ValueError, OSError, sqlite3.DatabaseError) as exc:
        # Provider credentials, arbitrary raw logs, and HTTP response bodies are not printed.
        reason = str(exc) if isinstance(exc, ControlError) else type(exc).__name__
        print(canonical({"state": "BLOCKED", "reason": reason}), file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "outcome":
        return outcome_main(args[1:])
    from ..cli import main as legacy_main
    return legacy_main(args)


if __name__ == "__main__":
    raise SystemExit(outcome_main())
