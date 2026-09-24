from __future__ import annotations

import argparse
from pathlib import Path

from .runner import encoded, run, write_new_or_identical


def main() -> int:
    parser = argparse.ArgumentParser(description="W12 S01: read-only capability audit only")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="Offline deterministic audit of captured evidence")
    audit.add_argument("--evidence", type=Path, required=True)
    audit.add_argument("--out", type=Path, required=True)
    audit.add_argument("--repo-root", type=Path, default=Path.cwd())
    audit.add_argument("--w11-root", type=Path)
    audit.add_argument("--w12-root", type=Path)
    probe = sub.add_parser("probe", help="One bounded public GET; no automatic retry")
    probe.add_argument("name", choices=("sdk_docs", "app_status", "catalog", "leaderboard", "recent_btc_upside"))
    probe.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "probe":
        # Lazy import keeps the normal audit entirely free of network imports.
        from .probe import public_probe
        if args.out.exists() or args.out.is_symlink():
            parser.error("probe output must be new")
        receipt = public_probe(args.name)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        write_new_or_identical(args.out, encoded(receipt))
        print(receipt["outcome"])
        return 0
    roots = {"repo": args.repo_root}
    if args.w11_root is not None:
        roots["w11"] = args.w11_root
    if args.w12_root is not None:
        roots["w12"] = args.w12_root
    result = run(args.evidence, args.out, roots)
    print(result["final_status"])
    # BLOCKED is a completed S01 finding; execution errors still fail nonzero.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
