import pytest

from src.data import google_sheets_client


class _FakeCredentials:
    def __init__(self, valid: bool, expired: bool = False, refresh_token: str | None = None):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refreshed = False

    def refresh(self, request) -> None:
        self.refreshed = True
        self.valid = True

    def to_json(self) -> str:
        return '{"fake": true}'


def test_load_or_refresh_credentials_raises_when_no_token_and_not_interactive(tmp_path, monkeypatch):
    """2026-08-04新增：daily_pipeline.py自動排程用interactive=False呼叫——本機沒有
    可用token時，不應該卡住等待瀏覽器(那個callback永遠不會來)，要直接拋出明確的
    例外，讓呼叫端的try/except能正常略過、印出訊息，不影響pipeline其餘部分。"""
    monkeypatch.setattr(google_sheets_client, "TOKEN_PATH", tmp_path / "token.json")

    with pytest.raises(google_sheets_client.GoogleAuthRequiresInteractionError):
        google_sheets_client._load_or_refresh_credentials(interactive=False)


def test_load_or_refresh_credentials_returns_valid_cached_token_without_network_call(tmp_path, monkeypatch):
    """已經有效的本機token不應該觸發任何刷新/瀏覽器互動，不管interactive是True或False。"""
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(google_sheets_client, "TOKEN_PATH", token_path)
    fake_creds = _FakeCredentials(valid=True)
    monkeypatch.setattr(
        google_sheets_client.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: fake_creds),
    )

    result = google_sheets_client._load_or_refresh_credentials(interactive=False)

    assert result is fake_creds
    assert fake_creds.refreshed is False


def test_load_or_refresh_credentials_refreshes_expired_token_with_refresh_token(tmp_path, monkeypatch):
    """token過期但有refresh_token時，不管interactive是True或False都應該用refresh_token
    自動換發新token，不需要跳瀏覽器——這是「已經授權過、只是access token過期」的
    正常情境，跟「完全沒有授權過」不同。"""
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(google_sheets_client, "TOKEN_PATH", token_path)
    fake_creds = _FakeCredentials(valid=False, expired=True, refresh_token="fake-refresh-token")
    monkeypatch.setattr(
        google_sheets_client.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: fake_creds),
    )

    result = google_sheets_client._load_or_refresh_credentials(interactive=False)

    assert result is fake_creds
    assert fake_creds.refreshed is True
    assert token_path.read_text(encoding="utf-8") == '{"fake": true}'


def test_load_or_refresh_credentials_raises_when_expired_without_refresh_token_and_not_interactive(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(google_sheets_client, "TOKEN_PATH", token_path)
    fake_creds = _FakeCredentials(valid=False, expired=True, refresh_token=None)
    monkeypatch.setattr(
        google_sheets_client.Credentials, "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: fake_creds),
    )

    with pytest.raises(google_sheets_client.GoogleAuthRequiresInteractionError):
        google_sheets_client._load_or_refresh_credentials(interactive=False)
