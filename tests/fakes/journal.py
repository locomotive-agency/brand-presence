"""What every stand-in shares: one record of calls, in order, the rules that make a named
call be refused or fail, and the errors that say a stand-in was used in a way it cannot
answer for."""
from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace

OK = "ok"
REFUSED = "refused"
FAILED = "failed"
# The stand-in could not read the call. Nothing was done, and nothing can be concluded.
NOT_UNDERSTOOD = "not understood"

# Every misuse raised since `misuses()` was last called, with what is still to be settled.
_MISUSES: list[BaseException] = []
_CHECKS: list[Callable[[], list[str]]] = []


class Misuse(BaseException):
    """The stand-in was asked for something it cannot answer truthfully, so the test that
    met it proves nothing. It is no `Exception`: code that closes a run on any error must
    not take it for a fault of the service. Each one is also kept, so the fixture in
    tests/conftest.py fails a test that met one, wherever it was caught."""

    def __init__(self, *args):
        super().__init__(*args)
        _MISUSES.append(self)


def misuses() -> list[BaseException]:
    """Every misuse raised since the last call, and forget them. A test that means to meet
    one calls this to say so."""
    found = list(_MISUSES)
    _MISUSES.clear()
    return found


def at_the_end(check: Callable[[], list[str]]) -> None:
    """Have `unsettled` ask this too. A stand-in gives a check of what it still holds open."""
    _CHECKS.append(check)


def unsettled() -> list[str]:
    """What a test left behind that makes its result worthless: a misuse nobody owned up
    to, or whatever a stand-in's own check reports. Asking forgets all of it."""
    found = [f"{type(error).__name__}: {error}" for error in misuses()]
    for check in _CHECKS:
        found += check()
    _CHECKS.clear()
    return found


@dataclass(frozen=True)
class Call:
    """One call a stand-in received. `subject` is what it was about: a statement, a table,
    an object's path, a secret's name, a range of a sheet. It never holds a secret's value."""

    service: str
    name: str
    subject: str
    outcome: str = OK
    detail: dict = field(default_factory=dict)


class Journal:
    """The calls of every stand-in that shares it, in the order they arrived. One journal
    for a test's stand-ins lets it check an order across them, such as bucket before table."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._lock = threading.RLock()

    def record(self, call: Call) -> Call:
        with self._lock:
            self.calls.append(call)
        return call

    def of(self, service: str | None = None, name: str | None = None,
           match: str | None = None, outcome: str | None = None) -> list[Call]:
        """The calls that fit, in order. `match` is a pattern looked for in the subject."""
        with self._lock:
            calls = list(self.calls)
        return [
            call for call in calls
            if (service is None or call.service == service)
            and (name is None or call.name == name)
            and (outcome is None or call.outcome == outcome)
            and (match is None or re.search(match, call.subject))
        ]

    def remark(self, start: int, service: str, name: str, outcome: str) -> None:
        """Give another outcome to the last such call recorded at or after `start`."""
        with self._lock:
            for index in range(len(self.calls) - 1, start - 1, -1):
                call = self.calls[index]
                if call.service == service and call.name == name:
                    self.calls[index] = replace(call, outcome=outcome)
                    return

    def position(self, call: Call) -> int:
        with self._lock:
            return next(i for i, other in enumerate(self.calls) if other is call)


@dataclass
class Rule:
    call: str
    outcome: str
    pattern: re.Pattern | None
    inverted: bool
    skip: int
    times: int | None
    error: Callable[[], BaseException] | None

    def fits(self, subject: str) -> bool:
        if self.pattern is None:
            return True
        found = self.pattern.search(subject) is not None
        return found != self.inverted


class StandIn:
    """The base of a stand-in. A subclass names its service, its calls and the two errors."""

    service = ""
    calls: tuple[str, ...] = ()

    def __init__(self, journal: Journal | None = None) -> None:
        self.journal = journal if journal is not None else Journal()
        self._rules: list[Rule] = []
        self._rules_lock = threading.RLock()

    # What a test tells the stand-in ------------------------------------------------

    def refuse(self, call: str, match: str | None = None) -> None:
        """Refuse every such call, as for an account that lacks the right. `match` limits
        it to subjects in which the pattern is found."""
        self._add(call, REFUSED, match, inverted=False, skip=0, times=None, error=None)

    def refuse_all_but(self, call: str, match: str) -> None:
        """Refuse every such call whose subject does not hold the pattern."""
        self._add(call, REFUSED, match, inverted=True, skip=0, times=None, error=None)

    def fail(self, call: str, match: str | None = None, *, times: int | None = None,
             skip: int = 0, error: Callable[[], BaseException] | None = None) -> None:
        """Make such a call fail, as for a fault of the service. `skip` lets that many
        through first; `times` is how many then fail, and None means every one. `error`
        builds the exception to raise in place of the stand-in's own."""
        self._add(call, FAILED, match, inverted=False, skip=skip, times=times, error=error)

    def _add(self, call: str, outcome: str, match: str | None, *, inverted: bool, skip: int,
             times: int | None, error) -> None:
        if call not in self.calls:
            raise ValueError(
                f"{self.service} has no call named {call!r}; its calls are {self.calls}"
            )
        pattern = re.compile(match) if match is not None else None
        with self._rules_lock:
            self._rules.append(Rule(call, outcome, pattern, inverted, skip, times, error))

    # What a stand-in does on each call -----------------------------------------------

    def _decide(self, call: str, subject: str) -> Rule | None:
        """The first rule that stops this call, counting the call against it."""
        with self._rules_lock:
            for rule in self._rules:
                if rule.call != call or not rule.fits(subject):
                    continue
                if rule.skip > 0:
                    rule.skip -= 1
                    continue
                if rule.times is not None:
                    if rule.times == 0:
                        continue
                    rule.times -= 1
                return rule
        return None

    def _enter(self, call: str, subject: str, **detail) -> None:
        """Record the call, and raise if a rule stops it."""
        error = self._check(call, subject, **detail)
        if error is not None:
            raise error

    def _check(self, call: str, subject: str, **detail) -> BaseException | None:
        """Record the call. Returns the error a rule gives it, or None."""
        rule = self._decide(call, subject)
        outcome = rule.outcome if rule else OK
        self.journal.record(Call(self.service, call, subject, outcome, detail))
        if rule is None:
            return None
        if rule.error is not None:
            return rule.error()
        return self._refusal() if rule.outcome == REFUSED else self._failure()

    def _refusal(self) -> BaseException:
        raise NotImplementedError

    def _failure(self) -> BaseException:
        raise NotImplementedError
