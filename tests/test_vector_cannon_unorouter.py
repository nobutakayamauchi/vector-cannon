from right_arm.vector_cannon.gateway import get_provider_spec


def test_unorouter_provider_is_fixed_and_keyed() -> None:
    spec = get_provider_spec("unorouter")
    assert spec.base_url == "https://api.unorouter.com/v1"
    assert spec.key_env == "UNOROUTER_API_KEY"


def test_unorouter_declares_free_suffix() -> None:
    spec = get_provider_spec("unorouter")
    assert spec.is_declared_free_model("gpt-oss-120b:free") is True
    assert spec.is_declared_free_model("gpt-6-astra") is False
