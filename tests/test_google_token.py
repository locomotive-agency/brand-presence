from types import SimpleNamespace

import google.auth
import pytest

from brand_presence import google_token


@pytest.mark.parametrize("scope", google_token.READ_ONLY_SCOPES)
def test_adc_refreshes_once_and_keeps_quota_project(monkeypatch, scope):
    asked, refreshed = [], []
    credentials = SimpleNamespace(valid=False, token=None, quota_project_id='example-project')

    def refresh(request):
        refreshed.append(request)
        credentials.valid, credentials.token = True, 'example-access-token'

    def default(*, scopes):
        asked.append(scopes)
        return credentials, 'example-project'

    credentials.refresh = refresh
    monkeypatch.setattr(google.auth, 'default', default)
    token = google_token.reader(scope,
                                google_token.ApplicationDefault())
    for _ in range(2):
        assert token.header() == {'Authorization': 'Bearer example-access-token',
                                  'x-goog-user-project': 'example-project'}
    assert len(refreshed) == 1
    assert asked == [[scope]]
    assert 'example-access-token' not in repr(token)


def test_reader_refuses_unlisted_scopes_before_auth():
    with pytest.raises(google_token.NotReadOnly):
        google_token.reader('https://www.googleapis.com/auth/example-write',
                            lambda scope: pytest.fail('auth must not be called'))


def test_user_adc_keeps_login_scopes(monkeypatch, tmp_path):
    import json

    login_scopes = ["https://www.googleapis.com/auth/cloud-platform",
                    google_token.SEARCH_CONSOLE_READ_ONLY]
    path = tmp_path / "adc.json"
    path.write_text(json.dumps({
        "type": "authorized_user", "client_id": "example-client",
        "client_secret": "example-secret", "refresh_token": "example-refresh",
        "scopes": login_scopes,
    }))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-project")
    from google.auth import _default

    credentials, _ = _default.default(scopes=[google_token.SEARCH_CONSOLE_READ_ONLY])
    assert credentials.requires_scopes is False
    assert credentials.scopes == login_scopes
