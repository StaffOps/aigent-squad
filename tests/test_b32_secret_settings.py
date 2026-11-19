"""B-32 — secret settings are ``SecretStr``: masked in repr/dumps, revealed only at use sites."""
import pytest
from pydantic import SecretStr

from src.core.config import Settings, reveal

SECRET_FIELDS = {
    "OPENAI_API_KEY": "openai_api_key",
    "REDIS_PASSWORD": "redis_password",
    "SLACK_BOT_TOKEN": "slack_bot_token",
    "SLACK_SIGNING_SECRET": "slack_signing_secret",
    "SUPERVISOR_INTERNAL_TOKEN": "supervisor_internal_token",
    "GITLAB_TOKEN": "gitlab_token",
    "DOCS_PORTAL_TOKEN": "docs_portal_token",
    "POSTGRES_PASSWORD": "postgres_password",
}


@pytest.fixture
def loaded(monkeypatch):
    values = {env: f"s3cr3t-{env.lower()}-value" for env in SECRET_FIELDS}
    for env, val in values.items():
        monkeypatch.setenv(env, val)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    return Settings(), values


def test_every_secret_field_is_secretstr(loaded):
    s, _ = loaded
    for field in SECRET_FIELDS.values():
        assert isinstance(getattr(s, field), SecretStr), field


def test_repr_and_dump_never_show_plaintext(loaded):
    s, values = loaded
    dumps = (repr(s), str(s), str(s.model_dump()), s.model_dump_json())
    for val in values.values():
        for text in dumps:
            assert val not in text


def test_reveal_returns_plaintext_at_use_site(loaded):
    s, values = loaded
    for env, field in SECRET_FIELDS.items():
        assert reveal(getattr(s, field)) == values[env]


@pytest.mark.parametrize("raw, expected", [(None, ""), ("plain", "plain"), (SecretStr("x"), "x"), (SecretStr(""), ""), (123, "123")])
def test_reveal_accepts_none_str_and_secretstr(raw, expected):
    assert reveal(raw) == expected


def test_unset_secrets_default_to_none(monkeypatch):
    for env in SECRET_FIELDS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    s = Settings()
    for field in SECRET_FIELDS.values():
        if field == "postgres_password":
            # Keeps the pre-B-32 local default (docker-compose "changeme").
            assert reveal(s.postgres_password) == "changeme"
        else:
            assert getattr(s, field) is None


@pytest.mark.parametrize("presented, ok", [("tok-123456", True), ("wrong", False), ("", False)])
def test_internal_auth_compares_against_revealed_secretstr(monkeypatch, presented, ok):
    """Fail-closed supervisor check still works when the token is a SecretStr."""
    from fastapi import HTTPException

    from src.core import internal_auth
    from src.core.config import settings

    monkeypatch.setattr(settings, "supervisor_internal_token", SecretStr("tok-123456"))
    if ok:
        assert internal_auth.require_internal_token(presented) is None
    else:
        with pytest.raises(HTTPException) as ei:
            internal_auth.require_internal_token(presented)
        assert ei.value.status_code == 401


def test_internal_auth_fail_closed_on_empty_secretstr(monkeypatch):
    from fastapi import HTTPException

    from src.core import internal_auth
    from src.core.config import settings

    monkeypatch.setattr(settings, "supervisor_internal_token", SecretStr(""))
    with pytest.raises(HTTPException):
        internal_auth.require_internal_token("")


def test_openai_key_loader_reveals_secretstr(monkeypatch):
    from src.core.config import settings
    from src.core.llm.openai_provider import load_api_key

    monkeypatch.setattr(settings, "openai_api_key_file", None)
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("Bearer sk-test-abc"))
    assert load_api_key() == "sk-test-abc"


def test_internal_auth_with_mock_settings_fails_closed_not_typeerror(monkeypatch):
    """Security review F2: an unexpected type (MagicMock) must deny, not raise TypeError."""
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from src.core import internal_auth

    monkeypatch.setattr(internal_auth, "settings", MagicMock())
    with pytest.raises(HTTPException) as ei:
        internal_auth.require_internal_token("anything")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_kb_store_connects_with_settings_values(monkeypatch):
    """POSTGRES_* now come from Settings; the password is revealed only for asyncpg."""
    from src.core.config import settings
    from src.core.kb import store as store_mod

    seen = {}

    async def fake_pool(**kw):
        seen.update(kw)
        return object()

    monkeypatch.setattr(store_mod.asyncpg, "create_pool", fake_pool)
    monkeypatch.setattr(settings, "postgres_host", "pg.example")
    monkeypatch.setattr(settings, "postgres_password", SecretStr("pg-s3cret"))
    st = store_mod.KbStore()
    await st.connect()
    assert seen["host"] == "pg.example"
    assert seen["password"] == "pg-s3cret"
    assert seen["database"] == settings.postgres_db
