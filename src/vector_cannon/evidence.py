from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str

    def __post_init__(self) -> None:
        if not self.criterion_id.strip():
            raise ValueError("criterion_id must not be empty")
        if not self.description.strip():
            raise ValueError("criterion description must not be empty")


@dataclass(frozen=True)
class EvidencePack:
    job_id: str
    mission: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    shot_id: str | None
    controller_state: str
    stop_reason: str | None
    changed_files: tuple[str, ...]
    verification: tuple[dict[str, Any], ...]
    worker_claim: dict[str, Any] | None
    usage: dict[str, Any]
    artifacts: dict[str, str]
    scope_violations: tuple[str, ...] = ()
    patch_apply_check: str | None = None
    prior_judgments: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id must not be empty")
        if not self.mission.strip():
            raise ValueError("mission must not be empty")
        ids = [item.criterion_id for item in self.acceptance_criteria]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("acceptance criteria must be non-empty and uniquely identified")

    @classmethod
    def from_launcher_result(
        cls,
        *,
        job_id: str,
        mission: str,
        acceptance_criteria: Iterable[AcceptanceCriterion],
        result: dict[str, Any],
        prior_judgments: Iterable[dict[str, Any]] = (),
    ) -> "EvidencePack":
        if not isinstance(result, dict):
            raise ValueError("launcher result must be an object")
        verification = result.get("verification")
        changed = result.get("changed_files")
        artifacts = result.get("artifacts")
        usage = result.get("usage")
        if not isinstance(verification, list):
            verification = []
        if not isinstance(changed, list) or any(not isinstance(item, str) for item in changed):
            changed = []
        if not isinstance(artifacts, dict):
            artifacts = {}
        safe_artifacts = {
            str(key): str(value)
            for key, value in artifacts.items()
            if isinstance(key, str) and isinstance(value, (str, int, float))
        }
        if not isinstance(usage, dict):
            usage = {"status": "UNKNOWN", "reason": "NOT_OBSERVED"}
        worker = result.get("worker_claim")
        if not isinstance(worker, dict):
            worker = None
        return cls(
            job_id=job_id,
            mission=mission,
            acceptance_criteria=tuple(acceptance_criteria),
            shot_id=str(result.get("task_id")) if result.get("task_id") is not None else None,
            controller_state=str(result.get("state") or "UNKNOWN"),
            stop_reason=str(result.get("stop_reason")) if result.get("stop_reason") is not None else None,
            changed_files=tuple(changed),
            verification=tuple(item for item in verification if isinstance(item, dict)),
            worker_claim=worker,
            usage=usage,
            artifacts=safe_artifacts,
            scope_violations=tuple(
                str(item) for item in (result.get("scope_violations") or [])
                if isinstance(item, str)
            ),
            patch_apply_check=(
                str(result.get("patch_apply_check"))
                if result.get("patch_apply_check") is not None else None
            ),
            prior_judgments=tuple(item for item in prior_judgments if isinstance(item, dict)),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "mission": self.mission,
            "acceptance_criteria": [asdict(item) for item in self.acceptance_criteria],
            "shot_id": self.shot_id,
            "controller": {
                "state": self.controller_state,
                "stop_reason": self.stop_reason,
                "changed_files": list(self.changed_files),
                "verification": list(self.verification),
                "scope_violations": list(self.scope_violations),
                "patch_apply_check": self.patch_apply_check,
            },
            "worker_claim": self.worker_claim,
            "usage": self.usage,
            "artifacts": self.artifacts,
            "prior_judgments": list(self.prior_judgments),
        }
