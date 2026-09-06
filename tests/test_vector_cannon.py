from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from vector_cannon.gateway import CreditSnapshot, PROVIDER_SPECS, get_provider_spec
from vector_cannon.issue_command import parse_issue_body
from vector_cannon.repo_tools import RepositoryReader
from vector_cannon.runner import CannonRunResult, VectorCannon


def test_repo_reader_blocks_path_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    reader = RepositoryReader(repo)
    with pytest.raises(ValueError):
        reader.read_file("../outside.txt")


def test_repo_reader_blocks_env_and_redacts_secret(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (repo / "safe.txt").write_text("key=sk-abcdefghijklmnopqrstuvwxyz123456", encoding="utf-8")
    reader = RepositoryReader(repo)
    assert reader.read_file(".env")["error"] == "file_blocked"
    result = reader.read_file("safe.txt")
    assert "[REDACTED_SECRET]" in result["content"]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result["content"]


def test_repo_tools_are_read_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "a.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    before = target.read_bytes()
    reader = RepositoryReader(repo)
    json.loads(reader.execute_tool("list_tree", {"path": "."}))
    json.loads(reader.execute_tool("read_file", {"path": "a.txt"}))
    json.loads(reader.execute_tool("search_text", {"query": "beta"}))
    assert target.read_bytes() == before


def test_tree_ignores_git_and_secret_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("secret", encoding="utf-8")
    (repo / ".env.local").write_text("secret", encoding="utf-8")
    (repo / "README.md").write_text("ok", encoding="utf-8")
    reader = RepositoryReader(repo)
    tree = reader.list_tree(depth=3)
    assert "README.md" in tree["entries"]
    assert not any(".git" in path for path in tree["entries"])
    assert not any(".env" in path for path in tree["entries"])


def test_search_does_not_follow_out_of_root_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("needle from outside", encoding="utf-8")
    (repo / "escape.txt").symlink_to(outside)
    reader = RepositoryReader(repo)
    result = reader.search_text("needle")
    assert result["matches"] == []
    assert "escape.txt" not in reader.searched_files


def test_search_prunes_ignored_directories(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    ignored = repo / "node_modules"
    ignored.mkdir()
    (ignored / "package.js").write_text("needle ignored", encoding="utf-8")
    (repo / "safe.txt").write_text("needle safe", encoding="utf-8")
    reader = RepositoryReader(repo)
    result = reader.search_text("needle")
    assert [match["path"] for match in result["matches"]] == ["safe.txt"]
    assert not any(path.startswith("node_modules/") for path in reader.searched_files)


def test_input_estimate_is_conservative() -> None:
    messages = [{"role": "user", "content": "x" * 300}]
    estimate = VectorCannon._estimated_input_tokens(messages, [])
    assert estimate >= 100


def test_provider_registry_is_fixed_and_uses_distinct_secret_names() -> None:
    assert {"vercel", "openai", "openrouter", "unorouter", "gemini", "together", "fireworks", "groq"} <= set(PROVIDER_SPECS)
    assert get_provider_spec("vercel").key_env == "AI_GATEWAY_API_KEY"
    assert get_provider_spec("unorouter").key_env == "UNOROUTER_API_KEY"
    assert get_provider_spec("gemini").base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert get_provider_spec("openrouter").catalog_pricing_per_token is True
    with pytest.raises(Exception):
        get_provider_spec("attacker-controlled-provider")


class _TruncatingGateway:
    provider_id = "vercel"
    has_exact_credit_meter = True
    def find_model(self, model: str): return {"id": model, "pricing": {"input": "0.000001", "output": "0.000001"}}
    def catalog_pricing(self, model): return Decimal("0.000001"), Decimal("0.000001")
    def declared_free_model(self, model: str) -> bool: return False
    def credits(self) -> CreditSnapshot: return CreditSnapshot(balance=Decimal("10.00"), total_used=Decimal("0"))
    def add_request_metadata(self, request, *, tag: str) -> None: request["tag"] = tag
    def add_reasoning(self, request, effort) -> None:
        if effort: request["reasoning"] = {"effort": effort}
    @staticmethod
    def usage_tokens(payload): return 10, 10
    @staticmethod
    def reported_cost(payload): return None
    def chat_completion(self, payload):
        return {"usage": {"prompt_tokens": 10, "completion_tokens": 10}, "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "partial answer"}}]}


def test_runner_marks_length_completion_as_truncated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Inspect the repository.", encoding="utf-8")
    cannon = VectorCannon(gateway=_TruncatingGateway())
    result = cannon.fire(model="openai/test", repository=repo, prompt_file=prompt, max_usd=Decimal("1.00"), max_steps=1, log_dir=tmp_path / "logs")
    assert result.stop_reason == "truncated"
    assert result.answer == "partial answer"
    assert result.accounting_mode == "credit_delta"


def test_run_logs_are_collision_safe(tmp_path: Path) -> None:
    result = CannonRunResult(tag="same-tag", provider="vercel", model="openai/test", reasoning_effort=None, repository="repo", prompt_file="prompt.md", started_at="2026-09-06T00:00:00+00:00", elapsed_seconds=0.1, credit_before="1.00", credit_after="0.90", observed_cost="0.10", accounting_mode="credit_delta", pricing_source="provider_catalog", input_tokens=100, output_tokens=20, model_turns=1, tool_calls=0, files_read=(), files_searched=0, answer="ok", stop_reason="completed")
    first = VectorCannon._write_log(result, tmp_path)
    second = VectorCannon._write_log(result, tmp_path)
    assert first != second
    assert first.exists() and second.exists()


def test_issue_command_defaults_to_vercel_and_accepts_owner_repo() -> None:
    shot = parse_issue_body("""
model: openai/gpt-5.6-sol
target_repo: nobutakayamauchi/RTS
target_ref: main
prompt: repo_recon
reasoning: low
max_usd: 0.75
tag: rts-sol-01
""", allowed_owner="nobutakayamauchi")
    assert shot.provider == "vercel"
    assert shot.target_repo == "nobutakayamauchi/RTS"
    assert str(shot.max_usd) == "0.75"


def test_issue_command_accepts_simple_model_id_and_manual_prices() -> None:
    shot = parse_issue_body("""
provider: gemini
model: gemini-3.8-flash
target_repo: nobutakayamauchi/RTS
input_price_per_million: 1.50
output_price_per_million: 9.00
tag: gemini-shot
""", allowed_owner="nobutakayamauchi")
    assert shot.provider == "gemini"
    assert shot.model == "gemini-3.8-flash"
    assert shot.input_price_per_million == Decimal("1.50")
    assert shot.output_price_per_million == Decimal("9.00")


def test_issue_command_rejects_other_owner_unsafe_ref_and_unknown_provider() -> None:
    with pytest.raises(ValueError): parse_issue_body("model: openai/gpt-5.6-sol\ntarget_repo: someone/else\n", allowed_owner="nobutakayamauchi")
    with pytest.raises(ValueError): parse_issue_body("model: openai/gpt-5.6-sol\ntarget_repo: nobutakayamauchi/RTS\ntarget_ref: ../main\n", allowed_owner="nobutakayamauchi")
    with pytest.raises(ValueError): parse_issue_body("provider: evil\nmodel: x/y\ntarget_repo: nobutakayamauchi/RTS\n", allowed_owner="nobutakayamauchi")


def test_issue_command_requires_both_manual_prices() -> None:
    with pytest.raises(ValueError): parse_issue_body("provider: openai\nmodel: gpt-5.6-sol\ntarget_repo: nobutakayamauchi/RTS\ninput_price_per_million: 4\n", allowed_owner="nobutakayamauchi")
