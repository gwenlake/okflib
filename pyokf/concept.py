"""Concept documents — OKF SPEC §4, including the v0.2 signal families.

A concept is a UTF-8 markdown file made of a YAML frontmatter block
(delimited by `---`) followed by a free-form markdown body.
The only REQUIRED frontmatter field is `type`.

v0.2 additions (all optional): ``generated``, ``verified``, ``status``,
``stale_after``, ``sources``. The v0.1 ``timestamp`` field is still read
for backward compatibility and mapped onto ``generated.at``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import yaml

from .trust import (
    HUMAN_REVIEWED,
    MACHINE_CONFIRMED,
    UNVERIFIED,
    Source,
    Stamp,
    _parse_date,
)

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

# Markdown links: [text](target) — used for cross-linking (SPEC §5)
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")

RESERVED_FILENAMES = {"index.md", "log.md"}

#: Recommended descriptive keys (v0.1, SPEC §4.1) — signal keys are separate.
RECOMMENDED_KEYS = ("title", "description", "resource", "tags")


class OKFError(Exception):
    """Base error for the pyokf library."""


class FrontmatterError(OKFError):
    """Raised when a concept file has a missing or unparseable frontmatter block."""


@dataclass
class Link:
    """A markdown link found in a concept body (SPEC §5)."""

    text: str
    target: str

    @property
    def is_external(self) -> bool:
        return "://" in self.target

    @property
    def is_bundle_absolute(self) -> bool:
        """True for bundle-relative links starting with `/` (recommended form, §5.1)."""
        return self.target.startswith("/")


@dataclass
class Concept:
    """A single unit of knowledge (SPEC §2, §4).

    ``type`` is the only required field. ``extra`` holds any
    producer-defined frontmatter keys, which are preserved verbatim
    when round-tripping (SPEC §4.1, Extensions).

    v0.2 signal fields (all optional):

    - ``generated`` — :class:`Stamp`; how/when the content was produced.
    - ``verified``  — list of :class:`Stamp`; independent confirmations.
    - ``status``    — ``draft`` / ``stable`` / ``deprecated`` (None = stable).
    - ``stale_after`` — absolute :class:`datetime.date`; past it, re-verify.
    - ``sources``   — list of :class:`Source`; provenance with credibility signals.
    """

    type: str
    title: str | None = None
    description: str | None = None
    resource: str | None = None
    tags: list[str] = field(default_factory=list)
    generated: Stamp | None = None
    verified: list[Stamp] = field(default_factory=list)
    status: str | None = None
    stale_after: date | None = None
    sources: list[Source] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    # ------------------------------------------------------------------ #
    # Construction / parsing
    # ------------------------------------------------------------------ #

    @classmethod
    def from_text(cls, text: str) -> Concept:
        """Parse a full markdown document (frontmatter + body)."""
        match = FRONTMATTER_RE.match(text)
        if not match:
            raise FrontmatterError("missing YAML frontmatter block (--- ... ---)")
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise FrontmatterError(f"unparseable YAML frontmatter: {exc}") from exc
        if not isinstance(meta, dict):
            raise FrontmatterError("frontmatter must be a YAML mapping")

        ctype = meta.pop("type", None)
        if not ctype or not str(ctype).strip():
            raise FrontmatterError("frontmatter is missing the required 'type' field")

        tags = meta.pop("tags", []) or []
        if isinstance(tags, str):
            tags = [tags]

        # v0.2 `generated`, with v0.1 `timestamp` fallback (compat rename)
        raw_generated = meta.pop("generated", None)
        generated = Stamp.from_yaml(raw_generated) if raw_generated is not None else None
        raw_ts = meta.pop("timestamp", None)
        if generated is None and raw_ts is not None:
            ts = (
                raw_ts
                if isinstance(raw_ts, datetime)
                else datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            )
            generated = Stamp(at=ts)

        raw_verified = meta.pop("verified", []) or []
        if isinstance(raw_verified, dict):
            raw_verified = [raw_verified]
        verified = [Stamp.from_yaml(v) for v in raw_verified]

        status = meta.pop("status", None)
        if status is not None:
            status = str(status)

        raw_sources = meta.pop("sources", []) or []
        if isinstance(raw_sources, dict):
            raw_sources = [raw_sources]
        sources = [Source.from_yaml(s) for s in raw_sources]

        return cls(
            type=str(ctype),
            title=meta.pop("title", None),
            description=meta.pop("description", None),
            resource=meta.pop("resource", None),
            tags=[str(t) for t in tags],
            generated=generated,
            verified=verified,
            status=status,
            stale_after=_parse_date(meta.pop("stale_after", None)),
            sources=sources,
            extra=meta,  # everything left over is preserved
            body=text[match.end() :],
        )

    # ------------------------------------------------------------------ #
    # v0.2 signal helpers
    # ------------------------------------------------------------------ #

    @property
    def timestamp(self) -> datetime | None:
        """v0.1 compatibility: last meaningful change (``generated.at``)."""
        return self.generated.at if self.generated else None

    @property
    def trust_tier(self) -> str:
        """Derive the advisory trust tier from ``verified``.

        - no ``verified`` entries        -> ``unverified``
        - machine confirmations only     -> ``machine-confirmed``
        - any ``human:<id>`` confirmation -> ``human-reviewed``
        """
        if not self.verified:
            return UNVERIFIED
        if any(v.is_human for v in self.verified):
            return HUMAN_REVIEWED
        return MACHINE_CONFIRMED

    @property
    def effective_status(self) -> str:
        """Lifecycle status; absent ``status`` means stable."""
        return self.status or "stable"

    @property
    def is_deprecated(self) -> bool:
        return self.effective_status == "deprecated"

    def is_stale(self, on: date | None = None) -> bool:
        """True if ``stale_after`` has passed (a plain date comparison)."""
        if self.stale_after is None:
            return False
        return (on or date.today()) > self.stale_after

    def verify(self, by: str, at: datetime | None = None) -> Stamp:
        """Append a verification stamp (e.g. ``by='human:kliu@acme'``)."""
        stamp = Stamp(by=by, at=at or datetime.now(timezone.utc))
        self.verified.append(stamp)
        return stamp

    def touch(self, by: str | None = None) -> None:
        """Mark the content as (re)generated now (UTC)."""
        now = datetime.now(timezone.utc)
        if self.generated is None:
            self.generated = Stamp(by=by, at=now)
        else:
            self.generated.at = now
            if by is not None:
                self.generated.by = by

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def frontmatter_dict(self) -> dict[str, Any]:
        """Frontmatter as an ordered dict: type, descriptive keys, signal keys, extras."""
        out: dict[str, Any] = {"type": self.type}
        if self.title is not None:
            out["title"] = self.title
        if self.description is not None:
            out["description"] = self.description
        if self.resource is not None:
            out["resource"] = self.resource
        if self.tags:
            out["tags"] = list(self.tags)
        if self.generated is not None:
            out["generated"] = self.generated.to_yaml()
        if self.verified:
            out["verified"] = [v.to_yaml() for v in self.verified]
        if self.status is not None:
            out["status"] = self.status
        if self.stale_after is not None:
            out["stale_after"] = self.stale_after.isoformat()
        if self.sources:
            out["sources"] = [s.to_yaml() for s in self.sources]
        out.update(self.extra)
        return out

    def to_text(self) -> str:
        """Serialize back to a full markdown document."""
        fm = yaml.safe_dump(
            self.frontmatter_dict(),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=None,
        ).rstrip("\n")
        body = self.body.lstrip("\n")
        doc = f"---\n{fm}\n---\n"
        if body:
            doc += f"\n{body}"
        if not doc.endswith("\n"):
            doc += "\n"
        return doc

    # ------------------------------------------------------------------ #
    # Body helpers
    # ------------------------------------------------------------------ #

    def links(self) -> Iterator[Link]:
        """Yield all markdown links found in the body (SPEC §5)."""
        for text, target in LINK_RE.findall(self.body):
            yield Link(text=text, target=target)

    def concept_links(self) -> Iterator[Link]:
        """Yield only links pointing at other concepts inside the bundle."""
        for link in self.links():
            if not link.is_external:
                yield link

    def footnote_refs(self) -> list[str]:
        """Source ids cited in the body via ``[^id]`` footnotes (v0.2 provenance)."""
        return re.findall(r"\[\^([^\]]+)\]", self.body)
