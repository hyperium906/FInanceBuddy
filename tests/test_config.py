"""Configuration loading, with attention to how credentials arrive.

A development machine points ``GOOGLE_CREDS_PATH`` at the downloaded key file.
A deployed host has no such file — the key is gitignored and never leaves the
laptop — so it supplies the key inline through ``GOOGLE_CREDS_JSON``. These
tests pin both routes and the error messages taken when neither works.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finance_app import config as C  # noqa: E402

#: A structurally valid service-account key. The "private key" is nonsense --
#: nothing here ever authenticates, it only has to survive parsing.
FAKE_KEY = {
    "type": "service_account",
    "project_id": "example-project",
    "private_key_id": "0" * 40,
    "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
    "client_email": "robot@example-project.iam.gserviceaccount.com",
    "token_uri": "https://oauth2.googleapis.com/token",
}

OTHER_VARS = {
    "GOOGLE_SHEET_ID": "sheet-123",
    "GEMINI_API_KEY": "key-abc",
    "GEMINI_MODEL": "gemini-2.5-pro",
    "GEMINI_MODEL_FAST": "gemini-2.5-flash",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from an empty environment and no Streamlit secrets.

    ``load_dotenv()`` ran at import and may have loaded the developer's real
    ``.env``; a ``.streamlit/secrets.toml`` would leak in the same way. Both
    are cleared so the assertions below describe the code, not the machine.
    """
    for name in (*C.REQUIRED_VARS, "GOOGLE_CREDS_PATH", "GOOGLE_CREDS_JSON"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(C, "_from_secrets", lambda name: "")


def set_env(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    """Populate the non-credential variables, plus whatever the test adds."""
    for name, value in {**OTHER_VARS, **extra}.items():
        monkeypatch.setenv(name, value)


# -- the non-credential variables ------------------------------------------


def test_missing_variables_are_all_named_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One run should tell you everything to fix, not the first thing."""
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-123")
    with pytest.raises(C.ConfigError) as exc:
        C.load_config()
    message = str(exc.value)
    assert "GEMINI_API_KEY" in message
    assert "GEMINI_MODEL" in message
    assert "GEMINI_MODEL_FAST" in message
    assert "GOOGLE_SHEET_ID" not in message


def test_whitespace_only_value_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A variable set to spaces is unset, not a sheet ID of three blanks."""
    set_env(monkeypatch, GOOGLE_SHEET_ID="   ", GOOGLE_CREDS_PATH="creds.json")
    with pytest.raises(C.ConfigError, match="GOOGLE_SHEET_ID"):
        C.load_config()


def test_values_are_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trailing newline from a copy-paste must not reach the API."""
    set_env(monkeypatch, GEMINI_API_KEY=" key-abc\n", GOOGLE_CREDS_PATH="creds.json")
    assert C.load_config().gemini_api_key == "key-abc"


# -- credentials from a file path ------------------------------------------


def test_path_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """The development route: point at the file, parse nothing."""
    set_env(monkeypatch, GOOGLE_CREDS_PATH="/tmp/creds.json")
    settings = C.load_config()
    assert settings.google_creds_path == "/tmp/creds.json"
    assert settings.google_creds_info is None
    assert "/tmp/creds.json" in settings.creds_source


def test_path_is_not_opened_at_config_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing file is the Sheets layer's error to report, with its own
    retry button -- config must not turn it into a startup crash."""
    set_env(monkeypatch, GOOGLE_CREDS_PATH="/nonexistent/creds.json")
    assert C.load_config().google_creds_path == "/nonexistent/creds.json"


# -- credentials inline ----------------------------------------------------


def test_inline_json_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployed route: the whole key file pasted into one variable."""
    set_env(monkeypatch, GOOGLE_CREDS_JSON=json.dumps(FAKE_KEY))
    settings = C.load_config()
    assert settings.google_creds_info == FAKE_KEY
    assert settings.google_creds_path == ""


def test_inline_base64_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Base64 is accepted, since not every host carries a multi-line value."""
    encoded = base64.b64encode(json.dumps(FAKE_KEY).encode()).decode()
    set_env(monkeypatch, GOOGLE_CREDS_JSON=encoded)
    assert C.load_config().google_creds_info == FAKE_KEY


def test_inline_wins_over_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """With both set, the inline key wins: a path inherited from a local .env
    would otherwise beat the key actually configured on the host."""
    set_env(
        monkeypatch,
        GOOGLE_CREDS_JSON=json.dumps(FAKE_KEY),
        GOOGLE_CREDS_PATH="/tmp/stale.json",
    )
    settings = C.load_config()
    assert settings.google_creds_info == FAKE_KEY
    assert settings.google_creds_path == ""


def test_creds_source_names_the_account_not_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error messages and the connection cache key both use this, so it must
    identify the credentials without quoting the private key into a log."""
    set_env(monkeypatch, GOOGLE_CREDS_JSON=json.dumps(FAKE_KEY))
    source = C.load_config().creds_source
    assert FAKE_KEY["client_email"] in source
    assert "PRIVATE KEY" not in source


# -- credential failures ---------------------------------------------------


def test_no_credentials_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither route configured names both variables, so either fix is clear."""
    set_env(monkeypatch)
    with pytest.raises(C.ConfigError) as exc:
        C.load_config()
    assert "GOOGLE_CREDS_JSON" in str(exc.value)
    assert "GOOGLE_CREDS_PATH" in str(exc.value)


def test_malformed_inline_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A truncated paste -- the usual way this is got wrong."""
    set_env(monkeypatch, GOOGLE_CREDS_JSON='{"type": "service_account",')
    with pytest.raises(C.ConfigError, match="not valid JSON"):
        C.load_config()


def test_inline_value_that_is_neither_json_nor_base64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pasting the file's *path* into the JSON variable is a real mistake and
    deserves better than a base64 decoding error."""
    set_env(monkeypatch, GOOGLE_CREDS_JSON="./credentials.json")
    with pytest.raises(C.ConfigError, match="neither JSON nor base64"):
        C.load_config()


def test_inline_json_that_is_not_an_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid JSON, wrong shape."""
    set_env(monkeypatch, GOOGLE_CREDS_JSON='["not", "a", "key"]')
    with pytest.raises(C.ConfigError, match="must be a JSON object"):
        C.load_config()


@pytest.mark.parametrize("field", ["client_email", "private_key"])
def test_inline_json_missing_a_required_field(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """An OAuth client secret is the wrong file and looks plausible; say so."""
    incomplete = {k: v for k, v in FAKE_KEY.items() if k != field}
    set_env(monkeypatch, GOOGLE_CREDS_JSON=json.dumps(incomplete))
    with pytest.raises(C.ConfigError, match=field):
        C.load_config()


# -- the shipped secrets template ------------------------------------------


def test_secrets_template_parses_and_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The committed template is the thing people paste into a host. If it does
    not survive a TOML parse into a working config, the first deploy fails with
    a confusing error and the template is where the fault lies.

    This caught a real bug: the template used TOML triple-DOUBLE quotes around
    the raw key JSON. Those process escapes, so every literal backslash-n in
    `private_key` became a real newline and the JSON stopped parsing.
    """
    import tomllib

    template = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml.example"
    values = tomllib.loads(template.read_text())

    assert set(C.REQUIRED_VARS) <= set(values), "template is missing a required variable"

    for name, value in values.items():
        monkeypatch.setenv(name, str(value))
    # The template ships a placeholder key, so swap in a structurally real one
    # while keeping the template's own encoding choice.
    monkeypatch.setenv(
        "GOOGLE_CREDS_JSON",
        base64.b64encode(json.dumps(FAKE_KEY).encode()).decode(),
    )
    settings = C.load_config()
    assert settings.google_creds_info == FAKE_KEY
    assert settings.google_sheet_id


def test_raw_json_in_a_toml_double_quoted_string_is_rejected_clearly() -> None:
    """The shape the broken template produced. It must not reach google-auth as
    a half-mangled key; config should name the problem."""
    import tomllib

    mangled = tomllib.loads('K = """\n' + json.dumps(FAKE_KEY, indent=2) + '\n"""\n')["K"]
    with pytest.raises(C.ConfigError, match="not valid JSON"):
        C._decode_creds_json(mangled)


# -- the Streamlit secrets fallback ----------------------------------------


def test_secrets_fill_in_for_unset_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Streamlit Community Cloud has no .env; values come from its secret
    store, which only partly mirrors into the environment."""
    stored = {**OTHER_VARS, "GOOGLE_CREDS_JSON": json.dumps(FAKE_KEY)}
    monkeypatch.setattr(C, "_from_secrets", lambda name: stored.get(name, ""))
    settings = C.load_config()
    assert settings.google_sheet_id == "sheet-123"
    assert settings.google_creds_info == FAKE_KEY


def test_environment_wins_over_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly exported variable overrides the stored secret."""
    monkeypatch.setattr(
        C,
        "_from_secrets",
        lambda name: "from-secrets" if name == "GOOGLE_SHEET_ID" else "",
    )
    set_env(monkeypatch, GOOGLE_SHEET_ID="from-env", GOOGLE_CREDS_PATH="creds.json")
    assert C.load_config().google_sheet_id == "from-env"


def test_missing_secrets_are_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off Streamlit, and under `pytest`, touching ``st.secrets`` raises. That
    is the normal case, so it must come back empty rather than escape as a
    startup failure."""
    monkeypatch.undo()  # use the real _from_secrets, not the fixture's stub
    assert C._from_secrets("DEFINITELY_NOT_SET_ANYWHERE") == ""
