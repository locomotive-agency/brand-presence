from types import SimpleNamespace

import google.auth
import pytest

from brand_presence import google_token


def test_adc_refreshes_once_and_keeps_quota_project(monkeypatch):
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
    token = google_token.reader(google_token.SEARCH_CONSOLE_READ_ONLY,
                                google_token.ApplicationDefault())
    for _ in range(2):
        assert token.header() == {'Authorization': 'Bearer example-access-token',
                                  'x-goog-user-project': 'example-project'}
    assert len(refreshed) == 1
    assert asked == [[google_token.SEARCH_CONSOLE_READ_ONLY, google_token.CLOUD_PLATFORM]]
    assert 'example-access-token' not in repr(token)


def test_reader_refuses_unlisted_scopes_before_auth():
    with pytest.raises(google_token.NotReadOnly):
        google_token.reader('https://www.googleapis.com/auth/example-write',
                            lambda scope: pytest.fail('auth must not be called'))
