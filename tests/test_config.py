from pathlib import Path

import pytest

from glosswork.config import ConfigError, Settings, load_settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GW_AUTH_MODE", raising=False)
    for search_var in ("GW_MODEL_DIR", "GW_EMBEDDING_MODEL", "GW_EMBEDDING_ENABLED"):
        monkeypatch.delenv(search_var, raising=False)
    settings = load_settings()
    assert settings.auth_mode == "standalone"
    assert settings.data_dir == Path("/data")
    assert settings.log_level == "info"
    assert settings.max_attachment_bytes == 26_214_400


def test_search_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """The search settings (docs/DATA_MODEL.md section 13, DD-32).

    ``GW_MODEL_DIR`` defaults to where the image bakes the model, never to a cache or
    a download target: the runtime locates the model by configuration and cannot fetch
    it. ``GW_EMBEDDING_ENABLED`` defaults on, so a deployment gets semantic indexing by
    not thinking about it and turns it off deliberately.
    """
    for search_var in ("GW_MODEL_DIR", "GW_EMBEDDING_MODEL", "GW_EMBEDDING_ENABLED"):
        monkeypatch.delenv(search_var, raising=False)
    settings = load_settings()
    assert settings.model_dir == Path("/app/models")
    assert settings.embedding_model == "bge-small-en-v1.5"
    assert settings.embedding_enabled is True


def test_embedding_can_be_disabled_and_the_model_dir_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GW_EMBEDDING_ENABLED", "false")
    monkeypatch.setenv("GW_MODEL_DIR", str(tmp_path / "elsewhere"))
    settings = load_settings()
    assert settings.embedding_enabled is False
    assert settings.model_dir == tmp_path / "elsewhere"


def test_invalid_auth_mode_fails_fast_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GW_AUTH_MODE", "bogus")
    with pytest.raises(ConfigError) as exc_info:
        load_settings()
    assert "GW_AUTH_MODE" in str(exc_info.value)


def test_invalid_log_level_fails_fast_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GW_LOG_LEVEL", "not-a-level")
    with pytest.raises(ConfigError) as exc_info:
        load_settings()
    assert "GW_LOG_LEVEL" in str(exc_info.value)


def test_non_positive_max_attachment_bytes_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GW_MAX_ATTACHMENT_BYTES", "0")
    with pytest.raises(ConfigError) as exc_info:
        load_settings()
    assert "GW_MAX_ATTACHMENT_BYTES" in str(exc_info.value)


def test_oidc_mode_requires_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_AUTH_MODE", "oidc")
    monkeypatch.delenv("GW_OIDC_ISSUER", raising=False)
    with pytest.raises(ConfigError) as exc_info:
        load_settings()
    assert "GW_OIDC_ISSUER" in str(exc_info.value)


def test_oidc_mode_requires_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-P7: the OIDC redirect_uri is built from GW_BASE_URL, never from the
    inbound request, so it must be set whenever OIDC is enabled."""
    monkeypatch.setenv("GW_AUTH_MODE", "oidc")
    monkeypatch.setenv("GW_OIDC_ISSUER", "https://example.okta.com")
    monkeypatch.setenv("GW_OIDC_CLIENT_ID", "glosswork")
    monkeypatch.delenv("GW_BASE_URL", raising=False)
    with pytest.raises(ConfigError) as exc_info:
        load_settings()
    assert "GW_BASE_URL" in str(exc_info.value)


def test_oidc_mode_with_issuer_and_base_url_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GW_AUTH_MODE", "oidc")
    monkeypatch.setenv("GW_OIDC_ISSUER", "https://example.okta.com")
    monkeypatch.setenv("GW_OIDC_CLIENT_ID", "glosswork")
    monkeypatch.setenv("GW_BASE_URL", "https://glosswork.example.com")
    settings = load_settings()
    assert settings.auth_mode == "oidc"


# ------------------------------------------------- .env.example drift


def _env_example_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".env.example"


def _documented_variables() -> set[str]:
    """Every ``GW_*`` name assigned in ``.env.example``, ignoring comments."""
    documented: set[str] = set()
    for line in _env_example_path().read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        documented.add(stripped.split("=", 1)[0].strip())
    return documented


def test_every_setting_config_reads_appears_in_env_example() -> None:
    """FR-P3 makes ``.env.example`` normative: "Configuration entirely by environment
    variable, documented in a shipped ``.env.example``".

    This test exists because the file had already fallen behind. ``GW_MODEL_DIR``
    shipped with search and never reached it, so an operator configuring from the file
    onto a non-default model path got a fail-fast at startup naming a variable the
    documentation did not mention — a live defect against FR-P3, not a chore. So this
    is the same instrument the ``web/src/api/schema.ts`` freshness test already
    provides: make the drift fail a test rather than surprise an operator.

    ``Settings`` carries ``env_prefix="GW_"``, so its field names *are* the variables
    the application reads; there is no second list to keep in step.
    """
    expected = {f"GW_{name.upper()}" for name in Settings.model_fields}
    missing = sorted(expected - _documented_variables())
    assert missing == [], (
        f".env.example does not document {missing}. Every GW_* variable config.py "
        "reads must appear there (FR-P3)."
    )


def test_env_example_documents_nothing_config_does_not_read() -> None:
    """The other direction, which matters just as much: a variable that lingers in
    the file after the code stopped reading it is an operator setting something with
    no effect. The two interim ``GW_INSECURE_*`` auth switches that real sign-in
    removed are the precedent, and this is what would have caught either one being left behind.

    (Their literal names are deliberately not written here:
    ``test_interim_credential_removed_from_web`` asserts they appear nowhere outside a
    short allowlist of documentation files, and a docstring mentioning one would defeat
    that guard for the sake of a nicer sentence.)"""
    known = {f"GW_{name.upper()}" for name in Settings.model_fields}
    stale = sorted(name for name in _documented_variables() if name not in known)
    assert stale == [], (
        f".env.example documents {stale}, which config.py no longer reads. Remove "
        "them, or add the setting back."
    )


# ---------------------------------------------------- read-only mode (DD-38)


def test_read_only_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Measured** (without read-only mode ``Settings`` has no such attribute)."""
    monkeypatch.delenv("GW_READ_ONLY", raising=False)
    monkeypatch.delenv("GW_SUBSCRIBE_URL", raising=False)
    settings = load_settings()
    assert settings.read_only is False
    assert settings.subscribe_url is None


def test_read_only_and_a_subscribe_url_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Measured** (without the settings, both were silently ignored)."""
    monkeypatch.setenv("GW_READ_ONLY", "true")
    monkeypatch.setenv("GW_SUBSCRIBE_URL", "https://glosswork.example.com/subscribe")
    settings = load_settings()
    assert (getattr(settings, "read_only", None), getattr(settings, "subscribe_url", None)) == (
        True,
        "https://glosswork.example.com/subscribe",
    )


def test_a_blank_read_only_refuses_startup_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Measured**. A boolean like every other boolean setting: blank is not
    "off", which is why ``.env.example`` ships ``GW_READ_ONLY=false``."""
    monkeypatch.setenv("GW_READ_ONLY", "")
    with pytest.raises(ConfigError) as exc_info:
        load_settings()
    assert "GW_READ_ONLY" in str(exc_info.value)


def test_a_blank_subscribe_url_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Measured.** ``.env.example`` ships it blank, like ``GW_BOOTSTRAP_SECRET``, so an
    operator using that file as ``--env-file`` is not refused over a feature they never
    asked for."""
    monkeypatch.setenv("GW_SUBSCRIBE_URL", "")
    settings = load_settings()
    assert settings.subscribe_url is None


@pytest.mark.parametrize(
    "value",
    ["/subscribe", "ftp://glosswork.example.com/subscribe", "https://", "glosswork.example.com"],
)
def test_a_subscribe_url_that_is_not_absolute_http_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """**Measured.** An agent is told to go to this address; a relative path, another
    scheme, or no host is an address nobody can reach."""
    monkeypatch.setenv("GW_SUBSCRIBE_URL", value)
    with pytest.raises(ConfigError) as exc_info:
        load_settings()
    assert "GW_SUBSCRIBE_URL" in str(exc_info.value)


def test_a_subscribe_url_is_allowed_without_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Measured**. The settings are independent: the trial banner needs the link
    while the trial is still running."""
    monkeypatch.delenv("GW_READ_ONLY", raising=False)
    monkeypatch.setenv("GW_SUBSCRIBE_URL", "http://localhost:8080/subscribe")
    settings = load_settings()
    assert settings.read_only is False
    assert settings.subscribe_url == "http://localhost:8080/subscribe"


def test_env_example_ships_read_only_as_false() -> None:
    """**Measured**. Not blank: a blank boolean refuses startup."""
    lines = [line.strip() for line in _env_example_path().read_text().splitlines()]
    assert ("GW_READ_ONLY=false" in lines, "GW_SUBSCRIBE_URL=" in lines) == (True, True)
