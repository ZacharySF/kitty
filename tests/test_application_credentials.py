"""Loading the application-account credentials and the approved-origin policy.

Only synthetic values: the real .private file is never read by tests.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from kitty.config import AuthenticationSettings
from kitty.core.apply.credentials import (
    CREDENTIALS_FILE, REDACTED, CredentialError, load_credentials, redact,
)
from kitty.settings import Settings

EMAIL = "pat.synthetic@example.invalid"
PASSWORD = "Synth#Pass word/1&2"


@pytest.fixture
def settings(tmp_path):
    return Settings.load(root=tmp_path, db_path=tmp_path / "test.db")


def _write(settings, text: str, mode: int = 0o600):
    path = settings.root / CREDENTIALS_FILE
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def test_reads_the_two_known_keys_with_comments_quotes_and_export(settings):
    _write(settings, f"# my accounts\nexport APPLICATION_ACCOUNT_EMAIL={EMAIL}\n\n"
                     f"APPLICATION_ACCOUNT_PASSWORD='{PASSWORD}'\n")
    credentials = load_credentials(settings)
    assert (credentials.email, credentials.password) == (EMAIL, PASSWORD)
    assert PASSWORD not in repr(credentials) and EMAIL not in repr(credentials)
    assert "APPLICATION_ACCOUNT_PASSWORD" not in os.environ


@pytest.mark.parametrize("text, mode, problem", [
    (None, 0o600, "missing"),
    (f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\nAPPLICATION_ACCOUNT_PASSWORD={PASSWORD}\n", 0o644, "chmod 600"),
    (f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\n", 0o600, "needs both"),
    (f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\nAPPLICATION_ACCOUNT_PASSWORD=\n", 0o600, "needs both"),
    (f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\nOTHER_SECRET={PASSWORD}\n", 0o600, "line 2"),
    (f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\n{PASSWORD}\n", 0o600, "line 2"),
    (f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\nAPPLICATION_ACCOUNT_PASSWORD=\"{PASSWORD}\n", 0o600, "unbalanced"),
    (f"APPLICATION_ACCOUNT_EMAIL=not-an-email\nAPPLICATION_ACCOUNT_PASSWORD={PASSWORD}\n", 0o600, "not an email"),
    (f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\nAPPLICATION_ACCOUNT_PASSWORD={PASSWORD}\n"
     f"APPLICATION_ACCOUNT_PASSWORD={PASSWORD}\n", 0o600, "twice"),
])
def test_bad_files_are_refused_without_echoing_values(settings, text, mode, problem):
    if text is not None:
        _write(settings, text, mode)
    with pytest.raises(CredentialError) as caught:
        load_credentials(settings)
    message = str(caught.value)
    assert problem in message
    assert PASSWORD not in message and EMAIL not in message


def test_a_symlinked_credentials_file_is_refused(settings, tmp_path):
    elsewhere = tmp_path / "elsewhere.env"
    elsewhere.write_text(f"APPLICATION_ACCOUNT_EMAIL={EMAIL}\nAPPLICATION_ACCOUNT_PASSWORD={PASSWORD}\n")
    elsewhere.chmod(0o600)
    link = settings.root / CREDENTIALS_FILE
    link.parent.mkdir()
    link.symlink_to(elsewhere)
    with pytest.raises(CredentialError, match="symlink"):
        load_credentials(settings)


def test_redaction_covers_encoded_forms():
    text = (f"error at https://x.invalid/?p=Synth%23Pass%20word%2F1%262 "
            f"form=Synth%23Pass+word%2F1%262 json=\"{PASSWORD}\" html=Synth#Pass word/1&amp;2 user {EMAIL}")
    cleaned = redact(text, (EMAIL, PASSWORD))
    assert "Synth" not in cleaned and EMAIL not in cleaned
    assert cleaned.count(REDACTED) == 5


def test_policy_approves_only_exact_https_origins():
    policy = AuthenticationSettings(enabled=True, approved_origins=[
        "https://Example.wd5.myworkdayjobs.com/", "https://127.0.0.1:8443"])
    assert policy.approved_origins == ["https://example.wd5.myworkdayjobs.com", "https://127.0.0.1:8443"]
    assert policy.approves("https://example.wd5.myworkdayjobs.com/en-US/External/login?x=1")
    assert policy.approves("https://127.0.0.1:8443/login")
    for url in ("http://example.wd5.myworkdayjobs.com/login",            # not HTTPS
                "https://evil.example.wd5.myworkdayjobs.com/login",      # subdomain
                "https://example.wd5.myworkdayjobs.com.evil.test/",      # suffix
                "https://user@example.wd5.myworkdayjobs.com/",           # userinfo
                "https://example.wd5.myworkdayjobs.com:444/",            # other port
                "https://127.0.0.1:8444/login", "https://2130706433:8443/",
                "https://example.wd5.myworkdayjobs.com\\@evil.test/"):
        assert not policy.approves(url), url
    assert not AuthenticationSettings(approved_origins=["https://example.test"]).approves("https://example.test/")


@pytest.mark.parametrize("settings_kwargs", [
    {"enabled": True},                                                    # nothing approved
    {"allow_account_creation": True, "approved_origins": ["https://example.test"]},
    {"enabled": True, "approved_origins": ["https://example.test/login"]},  # a path, not an origin
    {"enabled": True, "approved_origins": ["http://example.test"]},
    {"enabled": "yes", "approved_origins": ["https://example.test"]},
])
def test_policy_rejects_loose_configuration(settings_kwargs):
    with pytest.raises(ValidationError):
        AuthenticationSettings(**settings_kwargs)
