from __future__ import annotations

import json
from pathlib import Path

from vector_cannon.cli import main


def job_file(tmp_path: Path) -> Path:
    path = tmp_path / "job.json"
    path.write_text(
        json.dumps(
            {
                "job_id": "demo-job",
                "mission": "Create the requested fixture output.",
                "acceptance_criteria": [
                    {"id": "AC-001", "description": "fixture output exists"},
                    {"id": "AC-002", "description": "controller verification passes"},
                ],
                "allowed_files": ["fixture-output.txt"],
                "verification_commands": [
                    [
                        "python3",
                        "-c",
                        "from pathlib import Path; assert Path('fixture-output.txt').exists()",
                    ]
                ],
                "max_shots": 3,
                "timeout_seconds": 60,
                "max_sol_judgments": 2,
                "max_astra_judgments": 1,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_mission_without_execute_is_a_zero_fire_plan(tmp_path: Path, capsys) -> None:
    job = job_file(tmp_path)
    session = tmp_path / "session"

    code = main(
        [
            "mission",
            "--job",
            str(job),
            "--repo",
            str(tmp_path / "repo-not-needed-for-dry-plan"),
            "--session-dir",
            str(session),
            "--jev-provider",
            "vercel",
            "--jev-model",
            "demo/jev",
            "--sol-provider",
            "vercel",
            "--sol-model",
            "demo/sol",
            "--astra-provider",
            "vercel",
            "--astra-model",
            "demo/astra",
            "--json",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["execute"] is False
    assert output["decision_order"][-1]["judge"] == "astra"
    assert output["max_astra_judgments"] == 1
    assert not session.exists()


def test_mission_rejects_half_configured_sol(tmp_path: Path, capsys) -> None:
    job = job_file(tmp_path)

    code = main(
        [
            "mission",
            "--job",
            str(job),
            "--repo",
            str(tmp_path / "repo"),
            "--session-dir",
            str(tmp_path / "session"),
            "--jev-provider",
            "vercel",
            "--jev-model",
            "demo/jev",
            "--sol-provider",
            "vercel",
        ]
    )

    assert code == 2
    assert "sol requires both provider and model" in capsys.readouterr().err
