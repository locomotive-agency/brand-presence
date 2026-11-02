"""The rules that judge an AI answer, each on a sample that trips it (ai-answers "Error pages
and captchas are not counted as answers", the scenarios "A completed task with a real
answer" and "Every rule has a sample that trips it"; "No overview shown is an answer", the
scenarios "A results page without an overview" and "An overview key that holds no
overview")."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brand_presence.sources import ai_answers_rules as rules
from brand_presence.vendors import cloro

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cloro"

# The sample each rule trips, by the rule's name.
SAMPLES = {
    "task failed": "task_failed",
    "vendor error": "rule_vendor_error",
    "overview unreadable": "rule_overview_unreadable",
    "no results": "rule_no_results",
    "no text": "rule_no_text",
    "block page": "rule_block_page",
    "too short": "rule_too_short",
}
# The samples that are answers.
USABLE = ("answer", "answer_inside_word", "overview", "overview_none", "overview_none_as_sent")


def sample(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def chat(text: str, *sources: str) -> dict:
    """A completed answer of a chat engine."""
    return {"task": {"taskType": "CHATGPT", "status": cloro.COMPLETED},
            "response": {"text": text, "sources": [{"url": url} for url in sources]}}


def test_every_rule_has_a_name_and_a_sample():
    names = [rule.name for rule in rules.RULES]
    assert len(set(names)) == len(names) and all(names)
    assert set(SAMPLES) == set(names)


@pytest.mark.parametrize("rule", rules.RULES, ids=lambda rule: rule.name)
def test_each_rule_marks_its_sample_unusable(rule):
    answer = sample(SAMPLES[rule.name])
    assert rule.trips(answer, rules.is_overview(answer))
    # It is counted under that rule, and under no earlier one.
    assert rules.tripped(answer) == rule.name


@pytest.mark.parametrize("name", USABLE)
def test_a_usable_sample_trips_no_rule(name):
    answer = sample(name)
    overview = rules.is_overview(answer)
    assert [rule.name for rule in rules.RULES if rule.trips(answer, overview)] == []
    assert rules.tripped(answer) is None


def test_the_real_answer_names_two_brands_and_is_no_error_page():
    answer = sample("answer")
    text = rules.text_of(rules.content_of(answer, overview=False))
    assert "Sample Brand Aardvark" in text and "capybara sample" in text
    assert rules.tripped(answer) is None


def test_the_samples_of_the_text_rules_are_reported_as_completed():
    # The vendor called each of them completed. Only the rules tell them from an answer.
    for rule in ("vendor error", "overview unreadable", "no results", "no text", "block page",
                 "too short"):
        assert cloro.status_of(sample(SAMPLES[rule])) == cloro.COMPLETED
    assert cloro.status_of(sample(SAMPLES["task failed"])) == cloro.FAILED


def test_a_page_with_no_overview_is_an_answer_and_a_task_with_no_response_is_not():
    none = sample("overview_none")
    assert rules.is_overview(none) and rules.none_shown(none, overview=True)
    assert rules.tripped(none) is None
    # A key that is there and holds no overview is no answer: the vendor leaves the key
    # out where none was shown, and nothing else reads as "none shown".
    for held in (None, "", [], "no overview"):
        given = {**none, "response": {**none["response"], "aioverview": held}}
        assert not rules.none_shown(given, overview=True)
        assert rules.tripped(given) == "overview unreadable"
    # Nothing came back at all: no page was read.
    assert rules.tripped({"task": none["task"]}) == "no text"
    # The same response from a chat engine has no text.
    asked_of_chat = {**none, "task": {**none["task"], "taskType": "CHATGPT"}}
    assert rules.tripped(asked_of_chat) == "no text"
    # An overview that was shown is judged like any text.
    shown = {**none, "response": {"aioverview": {"text": "Access denied", "sources": []}}}
    assert rules.tripped(shown) == "block page"


def test_a_page_with_no_overview_is_read_as_cloro_sends_it():
    # The vendor leaves out `aioverview` on pages without an overview.
    sent = sample("overview_none_as_sent")
    assert sorted(sent["response"]) == ["organicResults", "peopleAlsoAsk", "relatedSearches"]
    assert rules.none_shown(sent, overview=True) and rules.tripped(sent) is None
    # The same page with the overview a page with one carries is an answer of its text.
    shown = {**sent, "response": {**sent["response"],
                                  "aioverview": sample("overview")["response"]["aioverview"]}}
    assert not rules.none_shown(shown, overview=True) and rules.tripped(shown) is None


def test_a_page_with_no_overview_and_no_result_is_not_an_answer():
    none = sample("overview_none")
    # An empty page, a page with an empty list of results, and a block page's markup.
    for response in ({}, {"organicResults": []}, {"organicResults": [{}]},
                     {"html": "<html><body>Unusual traffic</body></html>"}):
        blocked = {**none, "response": response}
        assert not rules.none_shown(blocked, overview=True)
        assert rules.tripped(blocked) == "no results"
    # One result of any kind is a page that was read.
    for response in ({"organicResults": none["response"]["organicResults"][:1]},
                     {"relatedSearches": [{"query": "voorbeeld theepot"}]}):
        assert rules.tripped({**none, "response": response}) is None
    # An overview with results under none of it is judged by its text, as before.
    shown = sample("overview")
    only = {**shown, "response": {"aioverview": shown["response"]["aioverview"]}}
    assert rules.tripped(only) is None


def test_the_engine_is_the_one_that_was_asked_whatever_the_vendor_echoes():
    for name in ("overview", "overview_none"):
        answer = sample(name)
        task = answer["task"]
        without = {key: value for key, value in task.items() if key != "taskType"}
        # Read from the echo alone, a missing or another name of the type is a chat answer.
        assert rules.tripped({**answer, "task": without}) == "no text"
        assert rules.tripped({**answer, "task": {**without, "type": "GOOGLE"}}) == "no text"
        # The echo is read in any letter case.
        assert rules.tripped({**answer, "task": {**task, "taskType": "google"}}) is None
        # Told what was asked, the echo is not read at all.
        for echoed in (without, {**task, "taskType": "CHATGPT"}):
            assert rules.tripped({**answer, "task": echoed}, overview=True) is None
    chat_answer = sample("answer")
    assert rules.tripped({**chat_answer, "task": {**chat_answer["task"], "taskType": "GOOGLE"}},
                         overview=False) is None


def test_an_answer_that_speaks_of_a_captcha_at_length_is_an_answer():
    long = ("Een captcha is een korte test die een website toont om mensen van programma's "
            "te onderscheiden. ") * 4
    assert len(long) > rules.BLOCK_PAGE_LENGTH
    assert rules.tripped(chat(long)) is None
    # The same words alone are a block page, whatever their case and spacing.
    assert rules.tripped(chat("  Please complete the\n CAPTCHA  ")) == "block page"
    assert rules.tripped(chat("Checking   your browser before you continue.",
                              "https://voorbeeld-gids.example/")) == "block page"


def test_a_short_text_with_a_source_is_an_answer():
    assert rules.tripped(chat("Geen idee.")) == "too short"
    assert rules.tripped(chat("Geen idee.", "https://voorbeeld-gids.example/")) is None
    at_the_limit = "a" * rules.MIN_LENGTH
    assert rules.tripped(chat(at_the_limit)) is None
    assert rules.tripped(chat(at_the_limit[:-1])) == "too short"


def test_an_error_object_makes_a_completed_answer_unusable_wherever_it_is():
    good = chat("Sample Brand Aardvark is een goede keuze voor een voorbeeld theepot.")
    assert rules.tripped(good) is None
    assert rules.tripped({**good, "error": {"code": "A"}}) == "vendor error"
    assert rules.tripped({**good, "task": {**good["task"], "error": {"code": "A"}}}) \
        == "vendor error"
    # A task that is not completed is counted as failed, before anything else is read.
    for status in (cloro.FAILED, None):
        failed = {**good, "task": {**good["task"], "status": status}}
        assert rules.tripped(failed) == "task failed"
