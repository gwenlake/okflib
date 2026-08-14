"""Knowledge Bundles — OKF SPEC §3, §6, §7, §9.

A bundle is a directory tree of markdown files. Concept IDs are the
file paths relative to the bundle root, without the ``.md`` suffix
(e.g. ``tables/users.md`` -> ``tables/users``).
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .concept import (
    FRONTMATTER_RE,
    RESERVED_FILENAMES,
    Concept,
    FrontmatterError,
    OKFError,
)
from .trust import STATUSES


class ConceptNotFound(OKFError, KeyError):
    pass


@dataclass
class ValidationIssue:
    """A single conformance problem or warning."""

    concept_id: str | None
    message: str
    fatal: bool  # fatal issues break OKF v0.1 conformance (SPEC §9)

    def __str__(self) -> str:
        level = "ERROR" if self.fatal else "WARN"
        where = f"[{self.concept_id}] " if self.concept_id else ""
        return f"{level}: {where}{self.message}"


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def conformant(self) -> bool:
        return not any(i.fatal for i in self.issues)

    def __iter__(self) -> Iterator[ValidationIssue]:
        return iter(self.issues)

    def __str__(self) -> str:
        if not self.issues:
            return "OK: bundle is conformant, no issues found"
        return "\n".join(str(i) for i in self.issues)


class Bundle:
    """An in-memory OKF knowledge bundle, loadable from / savable to disk.

    ``okf_version`` is the spec version the bundle declares (SPEC §11);
    it is written to / read from the bundle-root ``index.md`` frontmatter,
    the only place frontmatter is permitted in an index file.
    """

    OKF_VERSION = "0.2"

    def __init__(self, okf_version: str | None = None) -> None:
        self._concepts: dict[str, Concept] = {}
        self.okf_version = okf_version or self.OKF_VERSION

    # ------------------------------------------------------------------ #
    # Mapping-style access
    # ------------------------------------------------------------------ #

    def __len__(self) -> int:
        return len(self._concepts)

    def __contains__(self, concept_id: str) -> bool:
        return _norm_id(concept_id) in self._concepts

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._concepts))

    def __getitem__(self, concept_id: str) -> Concept:
        return self.get(concept_id)

    def __setitem__(self, concept_id: str, concept: Concept) -> None:
        self.add(concept_id, concept)

    def __delitem__(self, concept_id: str) -> None:
        self.remove(concept_id)

    def ids(self) -> list[str]:
        return sorted(self._concepts)

    def items(self) -> Iterator[tuple[str, Concept]]:
        for cid in sorted(self._concepts):
            yield cid, self._concepts[cid]

    def get(self, concept_id: str) -> Concept:
        cid = _norm_id(concept_id)
        try:
            return self._concepts[cid]
        except KeyError:
            raise ConceptNotFound(cid) from None

    def add(self, concept_id: str, concept: Concept) -> None:
        cid = _norm_id(concept_id)
        name = PurePosixPath(cid).name + ".md"
        if name in RESERVED_FILENAMES:
            raise OKFError(
                f"'{name}' is a reserved filename and cannot be a concept (SPEC §3.1)"
            )
        self._concepts[cid] = concept

    def remove(self, concept_id: str) -> Concept:
        cid = _norm_id(concept_id)
        try:
            return self._concepts.pop(cid)
        except KeyError:
            raise ConceptNotFound(cid) from None

    # ------------------------------------------------------------------ #
    # Convenience creation
    # ------------------------------------------------------------------ #

    def create(
        self,
        concept_id: str,
        type: str,
        body: str = "",
        **frontmatter,
    ) -> Concept:
        """Create, register, and return a new concept in one call.

        Known keys (title, description, resource, tags, timestamp) map to
        the corresponding attributes; anything else lands in ``extra``.
        """
        known = {
            k: frontmatter.pop(k)
            for k in (
                "title",
                "description",
                "resource",
                "tags",
                "generated",
                "verified",
                "status",
                "stale_after",
                "sources",
            )
            if k in frontmatter
        }
        # v0.1 compat: accept timestamp=... and map it to generated.at
        ts = frontmatter.pop("timestamp", None)
        concept = Concept(type=type, body=body, extra=frontmatter, **known)
        if ts is not None and concept.generated is None:
            from .trust import Stamp

            concept.generated = Stamp(at=ts)
        if concept.generated is None:
            concept.touch()
        self.add(concept_id, concept)
        return concept

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def by_type(self, type: str) -> list[tuple[str, Concept]]:
        return [(cid, c) for cid, c in self.items() if c.type == type]

    def by_tag(self, tag: str) -> list[tuple[str, Concept]]:
        return [(cid, c) for cid, c in self.items() if tag in c.tags]

    # -- v0.2 signal filters ------------------------------------------- #

    def by_status(self, status: str) -> list[tuple[str, Concept]]:
        """Filter on lifecycle status ('draft', 'stable', 'deprecated')."""
        return [(cid, c) for cid, c in self.items() if c.effective_status == status]

    def by_trust_tier(self, *tiers: str) -> list[tuple[str, Concept]]:
        """Filter on trust tier(s): 'unverified', 'machine-confirmed', 'human-reviewed'."""
        return [(cid, c) for cid, c in self.items() if c.trust_tier in tiers]

    def stale(self, on: date | None = None) -> list[tuple[str, Concept]]:
        """Concepts whose ``stale_after`` date has passed."""
        return [(cid, c) for cid, c in self.items() if c.is_stale(on)]

    def active(self, on: date | None = None) -> list[tuple[str, Concept]]:
        """Concepts that are neither deprecated nor stale — safe to surface."""
        return [
            (cid, c)
            for cid, c in self.items()
            if not c.is_deprecated and not c.is_stale(on)
        ]

    def search(self, text: str) -> list[tuple[str, Concept]]:
        """Naive case-insensitive substring search over title/description/body."""
        needle = text.lower()
        out = []
        for cid, c in self.items():
            haystack = " ".join(
                filter(None, [cid, c.title, c.description, c.body, *c.tags])
            ).lower()
            if needle in haystack:
                out.append((cid, c))
        return out

    def stats(self) -> dict[str, Any]:
        """Summary counters: totals and breakdowns by type, tier, and status."""
        from collections import Counter

        by_type: Counter = Counter()
        by_tier: Counter = Counter()
        by_status: Counter = Counter()
        stale = 0
        for _, c in self.items():
            by_type[c.type] += 1
            by_tier[c.trust_tier] += 1
            by_status[c.effective_status] += 1
            if c.is_stale():
                stale += 1
        broken = sum(1 for i in self.validate() if "broken link" in i.message)
        return {
            "concepts": len(self),
            "by_type": dict(by_type.most_common()),
            "by_trust_tier": dict(by_tier),
            "by_status": dict(by_status),
            "stale": stale,
            "broken_links": broken,
        }

    def graph(self) -> dict[str, set[str]]:
        """Directed link graph: concept ID -> set of linked concept IDs (SPEC §5.3).

        Broken links are kept as edges (they may represent
        not-yet-written knowledge and are not malformed).
        """
        edges: dict[str, set[str]] = {}
        for cid, concept in self.items():
            targets: set[str] = set()
            for link in concept.concept_links():
                targets.add(_resolve_link(cid, link.target))
            edges[cid] = targets
        return edges

    # ------------------------------------------------------------------ #
    # Validation (SPEC §9)
    # ------------------------------------------------------------------ #

    def validate(self) -> ValidationReport:
        report = ValidationReport()
        known = set(self._concepts)
        for cid, concept in self.items():
            if not concept.type or not concept.type.strip():
                report.issues.append(
                    ValidationIssue(cid, "empty required 'type' field", fatal=True)
                )
            if concept.description is None:
                report.issues.append(
                    ValidationIssue(cid, "missing recommended 'description'", fatal=False)
                )
            if concept.status is not None and concept.status not in STATUSES:
                report.issues.append(
                    ValidationIssue(
                        cid,
                        f"unknown status '{concept.status}' (expected one of {STATUSES})",
                        fatal=False,
                    )
                )
            if concept.is_stale():
                report.issues.append(
                    ValidationIssue(
                        cid,
                        f"stale since {concept.stale_after} — needs re-verification",
                        fatal=False,
                    )
                )
            source_ids = {s.id for s in concept.sources if s.id}
            for ref in concept.footnote_refs():
                if ref not in source_ids:
                    report.issues.append(
                        ValidationIssue(
                            cid,
                            f"footnote [^{ref}] has no matching entry in 'sources'",
                            fatal=False,
                        )
                    )
            for link in concept.concept_links():
                target = _resolve_link(cid, link.target)
                if target not in known:
                    report.issues.append(
                        ValidationIssue(cid, f"broken link -> {link.target}", fatal=False)
                    )
        return report

    # ------------------------------------------------------------------ #
    # Index & log generation (SPEC §6, §7)
    # ------------------------------------------------------------------ #

    def make_index(self, directory: str = "") -> str:
        """Generate the markdown body of an ``index.md`` for one directory."""
        directory = _norm_id(directory) if directory else ""
        prefix = f"{directory}/" if directory else ""
        concepts: list[tuple[str, Concept]] = []
        subdirs: set[str] = set()
        for cid, c in self.items():
            if not cid.startswith(prefix):
                continue
            rest = cid[len(prefix) :]
            if "/" in rest:
                subdirs.add(rest.split("/", 1)[0])
            else:
                concepts.append((rest, c))

        lines: list[str] = []
        if concepts:
            lines += ["# Contents", ""]
            for rest, c in concepts:
                title = c.title or rest
                desc = f" - {c.description}" if c.description else ""
                lines.append(f"* [{title}]({rest}.md){desc}")
        if subdirs:
            if lines:
                lines.append("")
            lines += ["# Subdirectories", ""]
            for sub in sorted(subdirs):
                lines.append(f"* [{sub}]({sub}/)")
        return "\n".join(lines) + "\n"

    def append_log(
        self, root: Path, entry: str, kind: str = "Update", when: date | None = None
    ) -> None:
        """Append an entry to the bundle-root ``log.md`` (newest-first, SPEC §7)."""
        root = Path(root)
        when = when or date.today()
        heading = f"## {when.isoformat()}"
        line = f"* **{kind}**: {entry}"
        log_path = root / "log.md"
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8")
        else:
            text = "# Directory Update Log\n"
        lines = text.splitlines()
        if heading in lines:
            idx = lines.index(heading)
            lines.insert(idx + 1, line)
        else:
            # insert right after the H1 title
            insert_at = 1 if lines and lines[0].startswith("# ") else 0
            lines[insert_at:insert_at] = ["", heading, line]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Disk I/O
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, root: str | Path, strict: bool = False) -> Bundle:
        """Load a bundle from a directory tree.

        With ``strict=False`` (the default), files that fail to parse are
        skipped — matching the permissive consumption model of SPEC §9.
        With ``strict=True``, a :class:`FrontmatterError` is raised instead.
        """
        root = Path(root)
        if not root.is_dir():
            raise OKFError(f"not a directory: {root}")
        bundle = cls()
        root_index = root / "index.md"
        if root_index.exists():
            m = FRONTMATTER_RE.match(root_index.read_text(encoding="utf-8"))
            if m:
                try:
                    meta = yaml.safe_load(m.group(1)) or {}
                    if isinstance(meta, dict) and meta.get("okf_version"):
                        bundle.okf_version = str(meta["okf_version"])
                except yaml.YAMLError:
                    pass
        for path in sorted(root.rglob("*.md")):
            if path.name in RESERVED_FILENAMES:
                continue
            cid = path.relative_to(root).as_posix()[:-3]
            try:
                bundle._concepts[cid] = Concept.from_text(path.read_text(encoding="utf-8"))
            except FrontmatterError:
                if strict:
                    raise
        return bundle

    def save(
        self,
        root: str | Path,
        write_indexes: bool = True,
        clean: bool = False,
    ) -> None:
        """Write the bundle to a directory tree.

        ``write_indexes=True`` (re)generates an ``index.md`` in every
        directory. ``clean=True`` first removes concept files on disk that
        no longer exist in the bundle.
        """
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)

        if clean:
            for path in root.rglob("*.md"):
                if path.name in RESERVED_FILENAMES:
                    continue
                cid = path.relative_to(root).as_posix()[:-3]
                if cid not in self._concepts:
                    path.unlink()
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()

        directories = {""}
        for cid, concept in self.items():
            path = root / f"{cid}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(concept.to_text(), encoding="utf-8")
            parts = PurePosixPath(cid).parts[:-1]
            for i in range(len(parts)):
                directories.add("/".join(parts[: i + 1]))

        if write_indexes:
            for directory in directories:
                index_path = (root / directory if directory else root) / "index.md"
                content = self.make_index(directory)
                if not directory and self.okf_version:
                    # SPEC §11: bundles MAY declare their OKF version in the
                    # bundle-root index.md frontmatter.
                    content = f'---\nokf_version: "{self.okf_version}"\n---\n\n{content}'
                index_path.write_text(content, encoding="utf-8")

    @classmethod
    def load_archive(cls, archive: str | Path, strict: bool = False) -> Bundle:
        """Load a bundle from a ``.tar.gz`` / ``.tgz`` / ``.zip`` archive."""
        archive = Path(archive)
        with tempfile.TemporaryDirectory() as tmp:
            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(tmp)
            else:
                with tarfile.open(archive) as tf:
                    tf.extractall(tmp, filter="data")
            root = Path(tmp)
            entries = [p for p in root.iterdir() if not p.name.startswith(".")]
            if len(entries) == 1 and entries[0].is_dir():
                root = entries[0]  # archive wraps a single top-level directory
            return cls.load(root, strict=strict)

    def export_archive(self, root: str | Path, archive: str | Path) -> Path:
        """Save the bundle then pack it as a ``.tar.gz`` or ``.zip`` archive."""
        root = Path(root)
        archive = Path(archive)
        self.save(root)
        fmt = "zip" if archive.suffix == ".zip" else "gztar"
        base = str(archive).removesuffix(".zip").removesuffix(".tar.gz")
        return Path(shutil.make_archive(base, fmt, root_dir=root))


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


def is_bundle_root(path: str | Path) -> bool:
    """True if ``path`` is the root of a bundle (not one of its subdirectories).

    A root is recognized by its ``index.md`` declaring ``okf_version``
    (SPEC §11 — the only index allowed frontmatter), or, for hand-written
    bundles, by the presence of the root-only ``log.md`` (SPEC §7).
    """
    path = Path(path)
    index = path / "index.md"
    if not index.is_file():
        return False
    match = FRONTMATTER_RE.match(index.read_text(encoding="utf-8", errors="replace"))
    if match:
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        if isinstance(meta, dict) and meta.get("okf_version"):
            return True
    return (path / "log.md").is_file()


def find_bundle_root(start: str | Path | None = None) -> Path | None:
    """Walk up from ``start`` (default: the current directory) to the bundle root.

    Mirrors how ``git`` finds its repository: run a command anywhere inside a
    bundle and it applies to that bundle. Returns None if there is none.
    """
    current = Path(start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if is_bundle_root(candidate):
            return candidate
    return None


def _norm_id(concept_id: str) -> str:
    cid = concept_id.strip().strip("/")
    if cid.endswith(".md"):
        cid = cid[:-3]
    if not cid:
        raise OKFError("empty concept ID")
    if ".." in PurePosixPath(cid).parts:
        raise OKFError(f"invalid concept ID: {concept_id!r}")
    return cid


def _resolve_link(source_id: str, target: str) -> str:
    """Resolve a link target to a concept ID (SPEC §5.1 / §5.2)."""
    target = target.split("#", 1)[0]
    if target.startswith("/"):
        return _norm_id(target)
    base = PurePosixPath(source_id).parent
    resolved = []
    for part in (base / target).parts:
        if part == "..":
            if resolved:
                resolved.pop()
        elif part not in (".",):
            resolved.append(part)
    return _norm_id("/".join(resolved))
