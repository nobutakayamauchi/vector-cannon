"""Two-step local acceptance demo. NO REAL AI OR PROVIDER CONNECTIONS."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from vector_cannon.outcome.engine import OutcomeEngine
from vector_cannon.outcome.model import Contract, strict_json


class DemoFixture:
    def __init__(self):
        self.calls = 0

    def identity(self):
        return {"kind": "SIMULATED", "assurance": "NOT_REAL_AI", "name": "two-step-repair-demo"}

    def preflight(self, workspace):
        pass

    def execute(self, contract, workspace, logs, directive, timeout, lock_fd):
        self.calls += 1
        (workspace / "out.txt").write_text("not finished\n" if self.calls == 1 else "ready\n")
        return {"kind": "SIMULATED", "cost_usd": "0", "cost_status": "MEASURED", "real_ai_calls": 0}


if __name__ == "__main__":
    contract = Contract.parse(strict_json(Path(__file__).with_name("fixture.json").read_text()))
    root = Path(tempfile.mkdtemp(prefix="vector-cannon-offline-demo-"))
    source = root / "source"
    source.mkdir()
    (source / "README.md").write_text("Unchanged source\n")
    report = OutcomeEngine.create(contract, source, root / "session", DemoFixture()).run()
    assert report["state"] == "VERIFIED_DONE", report
    assert report["calls"] == {"worker": 2, "jev": 0, "sol": 0, "astra": 0}, report
    assert not (source / "out.txt").exists()
    print(json.dumps({"demonstration": "OFFLINE_FIXTURE_NOT_REAL_AI", "session": str(root / "session"),
                      "report": report}, indent=2))
