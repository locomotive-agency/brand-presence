"""Invented Peec wire responses from the source fixture, on the local project fake."""

import httpx

from brand_presence import runner
from brand_presence.runs import Period, period_start
from brand_presence.settings import PeecBrand, PeecProject
from brand_presence.sources.peec import PeecSource
from brand_presence.vendors.peec import BASE
from project import Killed, Project


class Peec:
    def __init__(self, router):
        self.calls = []
        self.kill_path = None
        self.empty = False
        self.bad_detail = False
        self.archived = False
        self.text = "Aardvark Sample is an invented brand."
        router.get(url__startswith=BASE).mock(side_effect=self.get)

    def get(self, request):
        assert request.method == "GET"
        assert request.headers["x-api-key"] == "example-key"
        path = request.url.path.removeprefix("/customer/v1")
        self.calls.append((path, dict(request.url.params)))
        if path == self.kill_path:
            self.kill_path = None
            raise Killed()
        if path != "/projects":
            assert request.url.params["project_id"] == "example-project"
        common = dict(
            id="example-chat",
            prompt={"id": "example-prompt"},
            model_channel={"id": "openai-0"},
            model={"id": "example-model"},
        )
        if path == "/projects":
            body = {"data": [{"id": "example-project", "country_code": "NL"}]}
        elif path == "/brands":
            body = {"data": [{"id": "example-brand", "is_own": True}], "total_count": 1}
        elif path == "/model-channels":
            body = {"data": [{"id": "openai-0", "is_active": True}]}
        elif path == "/prompts":
            body = {
                "data": [
                    dict(
                        id="example-prompt",
                        messages=[{"content": "Which kettle?"}],
                        user_location={"country": "NL"},
                        is_archived=self.archived,
                        topic=None,
                        tags=[],
                        created_at="2026-03-01",
                    )
                ],
                "total_count": 1,
            }
        elif path == "/chats":
            body = {
                "data": [] if self.empty else [dict(common, date=request.url.params["start_date"])],
                "total_count": 0 if self.empty else 1,
            }
        elif path == "/chats/example-chat/content":
            body = dict(
                common,
                messages=[{"role": "assistant", "content": self.text}],
                brands_mentioned=[],
                sources=[{"url": "https://aardvark.example/kettle", "citation_count": 1}],
                links=[],
            )
            if self.bad_detail:
                body["messages"] = []
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=body)


def prepared(tmp_path, router):
    project = Project(tmp_path, router)
    project.settings.brands[0].name = "Aardvark Sample"
    project.settings.brands[0].domains = ["aardvark.example"]
    project.settings.peec_projects = [
        PeecProject(
            mapping="example",
            project_id="example-project",
            client_brand_id="aardvark",
            country_code="NL",
            engines=["chatgpt"],
            first_date="2026-02-23",
        )
    ]
    project.settings.peec_brands = [
        PeecBrand(mapping="example", brand_id="aardvark", vendor_brand_id="example-brand")
    ]
    return project, Peec(router)


def run(project, period="2026-W09"):
    with httpx.Client() as client:
        return runner.run(
            PeecSource(),
            project.settings,
            runner.World(
                project.bq,
                client,
                {"peec-api-key": "example-key"},
                clock=lambda: project.now,
                sleep=lambda _: None,
                wait_seconds=60,
            ),
            period=(Period(period, period_start(period)) if period else None),
        )
