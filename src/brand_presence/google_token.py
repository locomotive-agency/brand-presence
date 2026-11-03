"""Application Default Credentials for read-only Google property APIs."""
from __future__ import annotations

from collections.abc import Callable

ANALYTICS_READ_ONLY = "https://www.googleapis.com/auth/analytics.readonly"
SEARCH_CONSOLE_READ_ONLY = "https://www.googleapis.com/auth/webmasters.readonly"


READ_ONLY_SCOPES = (ANALYTICS_READ_ONLY, SEARCH_CONSOLE_READ_ONLY)


Provider = Callable[[str], str]


class NotReadOnly(ValueError):
    def __init__(self):
        super().__init__("a source may ask for a scope on the read-only list alone")


class ApplicationDefault:
    def __init__(self):
        self._held: dict[str, object] = {}

    def __call__(self, scope: str) -> str:
        import google.auth
        from google.auth.transport.requests import Request

        credentials = self._held.get(scope)
        if credentials is None:
            credentials, _ = google.auth.default(scopes=[scope])
            self._held[scope] = credentials
        if not credentials.valid:
            credentials.refresh(Request())
        return credentials.token

    def __repr__(self) -> str:
        return "ApplicationDefault()"

    def quota_project(self, scope: str) -> str | None:
        return getattr(self._held.get(scope), "quota_project_id", None)


class ReadOnly:
    __slots__ = ("_provider",)

    def __init__(self, provider: Provider):
        self._provider = provider

    def __call__(self, scope: str) -> str:
        if scope not in READ_ONLY_SCOPES:
            raise NotReadOnly()
        return self._provider(scope)

    def __repr__(self) -> str:
        return "ReadOnly()"


class AccountToken:
    __slots__ = ("scope", "_provider")

    def __init__(self, scope: str, provider: Provider):
        self.scope = scope
        self._provider = provider

    def header(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._provider(self.scope)}"}
        if isinstance(self._provider, ApplicationDefault):
            project = self._provider.quota_project(self.scope)
            if project:
                headers["x-goog-user-project"] = project
        return headers

    def __repr__(self) -> str:
        return f"AccountToken(scope={self.scope!r})"


def reader(scope: str, provider: Provider) -> AccountToken:
    if scope not in READ_ONLY_SCOPES:
        raise NotReadOnly()
    return AccountToken(scope, provider)
