"""OKF v0.2 trust, provenance, and lifecycle signals.

v0.2 adds opt-in frontmatter families that let a consumer decide about a
concept before reading its body:

- ``generated: {by, at}``  — how/when content was produced (supersedes v0.1 ``timestamp``)
- ``verified: [{by, at}]`` — independent confirmations; drives the trust tier
- ``status``               — lifecycle: draft / stable / deprecated (absent = stable)
- ``stale_after``          — absolute date after which re-verification is required
- ``sources``              — materials the concept derives from, with credibility signals
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

#: Trust tiers derived from ``verified`` (advisory, not access control).
UNVERIFIED = "unverified"
MACHINE_CONFIRMED = "machine-confirmed"
HUMAN_REVIEWED = "human-reviewed"

#: Lifecycle statuses. Absent ``status`` means stable.
STATUSES = ("draft", "stable", "deprecated")


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _parse_date(value: Any) -> date | None:
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def _iso_dt(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@dataclass
class Stamp:
    """A ``{by, at}`` pair used by ``generated`` and ``verified``.

    ``by`` identifies an actor, e.g. ``reference_agent/gemini-2.5-pro``
    or ``human:kliu@acme``. Actors with the ``human:`` prefix count as
    human for trust-tier purposes.
    """

    by: str | None = None
    at: datetime | None = None

    @property
    def is_human(self) -> bool:
        return bool(self.by) and self.by.startswith("human:")

    @classmethod
    def from_yaml(cls, raw: Any) -> Stamp:
        if isinstance(raw, dict):
            return cls(by=raw.get("by"), at=_parse_dt(raw.get("at")))
        return cls(by=str(raw) if raw is not None else None)

    def to_yaml(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.by is not None:
            out["by"] = self.by
        if self.at is not None:
            out["at"] = _iso_dt(self.at)
        return out


@dataclass
class Source:
    """An entry of the ``sources`` provenance list (v0.2).

    Records what a concept derives from, plus objective credibility
    signals (``author``, ``usage_count``, ``last_modified``). OKF records
    the signals, not a score — scoring is left to the consumer.
    The ``id`` is what body footnotes reference (``[^the-id]``).
    """

    id: str | None = None
    resource: str | None = None
    title: str | None = None
    author: str | None = None
    usage_count: int | None = None
    last_modified: date | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, raw: Any) -> Source:
        if not isinstance(raw, dict):
            return cls(resource=str(raw))
        raw = dict(raw)
        return cls(
            id=raw.pop("id", None),
            resource=raw.pop("resource", None),
            title=raw.pop("title", None),
            author=raw.pop("author", None),
            usage_count=raw.pop("usage_count", None),
            last_modified=_parse_date(raw.pop("last_modified", None)),
            extra=raw,
        )

    def to_yaml(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in ("id", "resource", "title", "author", "usage_count"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.last_modified is not None:
            out["last_modified"] = self.last_modified.isoformat()
        out.update(self.extra)
        return out
