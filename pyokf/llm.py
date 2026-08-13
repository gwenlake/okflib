"""LLM-powered ingestion: turn raw text into OKF concepts automatically.

Design:

- ``ingest_text(bundle, text)`` asks an LLM to segment/structure the text
  into one or more OKF concepts (id, type, title, description, tags, body)
  returned as strict JSON, then creates them in the bundle.
- The LLM call is a plain callable ``complete(system, user) -> str`` so any
  provider works. The default (:func:`anthropic_complete`) calls the
  Anthropic Messages API (docs: https://docs.claude.com/en/api/overview)
  using the ``ANTHROPIC_API_KEY`` environment variable — no SDK required.
- Per the v0.2 spec, generated concepts are stamped
  ``generated: {by: pyokf/<model>, at: ...}`` and left **unverified**:
  a human (or another process) is expected to ``verify()`` them.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone

from .bundle import Bundle
from .concept import Concept, OKFError
from .trust import Source, Stamp

#: Model used when none is given. Override per run with ``--model`` / the
#: ``model=`` argument, or globally with the ``PYOKF_MODEL`` env var.
FALLBACK_MODEL = "claude-sonnet-5"
DEFAULT_MODEL = os.environ.get("PYOKF_MODEL") or FALLBACK_MODEL

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

Completer = Callable[[str, str], str]

#: ``(relative_path, index, total, result)`` — ``result`` is None when the file
#: is picked up, then the created IDs or a ``"skipped: ..."`` string when done.
Progress = Callable[[str, int, int, "list[str] | str | None"], None]

SYSTEM_PROMPT = """\
You convert raw text into Open Knowledge Format (OKF) concepts.

OKF: each concept is one markdown file with YAML frontmatter. Required
frontmatter field: `type` (e.g. Note, Metric, Playbook, Reference, API
Endpoint, Table). Recommended: title, description (one sentence), tags.
The body is structural markdown (headings, lists, tables). Concepts may
cross-link with markdown links like [title](/path/to/concept.md).

Given the user's text, decide how many concepts it should become (split
distinct topics into separate concepts; keep cohesive content together)
and return ONLY a JSON array, no prose, no markdown fences:

[
  {
    "id": "kebab-case/path/no-extension",
    "type": "Concept type",
    "title": "Display title",
    "description": "One-sentence summary.",
    "tags": ["tag1", "tag2"],
    "body": "# Markdown body...\\n"
  }
]

Rules:
- "id" is a relative path (letters, digits, hyphens, slashes), no ".md".
- Write body/title/description in the same language as the input text.
- Do not invent facts absent from the input.
- Cross-link concepts you create together when they reference each other.
"""


class LLMError(OKFError):
    """Raised when the LLM call or its output cannot be used."""


def anthropic_complete(
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Default completer: Anthropic Messages API via stdlib urllib."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError(
            "no API key: set the ANTHROPIC_API_KEY environment variable or pass api_key="
        )
    payload = json.dumps(
        {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
    ).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:  # pragma: no cover - network
        raise LLMError(
            f"Anthropic API error {exc.code}: {exc.read().decode()[:300]}"
        ) from exc
    return "".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    )


def _parse_json_array(raw: str) -> list[dict]:
    text = raw.strip()
    text = re.sub(r"\A```(?:json)?\s*|\s*```\Z", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise LLMError(f"LLM did not return a JSON array: {raw[:200]!r}")
    try:
        items = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"unparseable LLM JSON: {exc}") from exc
    if not isinstance(items, list):
        raise LLMError("LLM JSON is not an array")
    return items


def ingest_text(
    bundle: Bundle,
    text: str,
    prefix: str = "",
    complete: Completer | None = None,
    model: str = DEFAULT_MODEL,
    hint: str | None = None,
    source: str | None = None,
) -> list[str]:
    """Turn raw ``text`` into OKF concepts inside ``bundle``.

    Returns the list of created concept IDs. ``prefix`` nests them under a
    subdirectory; ``hint`` adds a steering instruction (e.g. "type Playbook,
    tags oncall"); ``source`` records provenance (e.g. the originating file)
    in each concept's v0.2 ``sources`` field. Pass a custom
    ``complete(system, user) -> str`` to use another LLM provider; default
    is the Anthropic API.
    """
    if complete is None:
        complete = lambda s, u: anthropic_complete(s, u, model=model)  # noqa: E731

    user = text if not hint else f"Instruction: {hint}\n\n---\n\n{text}"
    items = _parse_json_array(complete(SYSTEM_PROMPT, user))

    created: list[str] = []
    stamp_by = f"pyokf/{model}"
    now = datetime.now(timezone.utc)
    for item in items:
        if not isinstance(item, dict) or not item.get("type"):
            raise LLMError(f"concept missing required 'type': {item!r}")
        cid = str(item.get("id") or item.get("title") or f"concept-{len(created) + 1}")
        cid = re.sub(r"[^a-z0-9/\-]+", "-", cid.lower()).strip("-/")
        if prefix:
            cid = f"{prefix.strip('/')}/{cid}"
        if cid in bundle:  # avoid silently overwriting existing knowledge
            base, n = cid, 2
            while f"{base}-{n}" in bundle:
                n += 1
            cid = f"{base}-{n}"
        concept = Concept(
            type=str(item["type"]),
            title=item.get("title"),
            description=item.get("description"),
            tags=[str(t) for t in item.get("tags", []) or []],
            generated=Stamp(by=stamp_by, at=now),
            sources=[Source(resource=source)] if source else [],
            body=str(item.get("body", "")),
        )
        bundle.add(cid, concept)
        created.append(cid)
    return created


def ingest_dir(
    bundle: Bundle,
    directory,
    prefix: str = "",
    complete: Completer | None = None,
    model: str = DEFAULT_MODEL,
    hint: str | None = None,
    glob: str = "**/*",
    max_chars: int = 60_000,
    on_progress: Progress | None = None,
) -> dict[str, list[str] | str]:
    """Read every supported document under ``directory`` and turn each into
    OKF concepts (one LLM call per file, so one bad file never sinks the run).

    Returns ``{relative_path: [created concept IDs]}`` for ingested files and
    ``{relative_path: "skipped: reason"}`` for the others. Each concept records
    its originating file in ``sources`` (v0.2 provenance).

    ``on_progress(rel, index, total, result)`` is called twice per file — once
    with ``result=None`` when the file is picked up, once with its outcome — so
    a caller can report a long run as it happens instead of after it. A run of
    30 documents is 30 LLM calls and takes minutes; nothing here prints.
    """
    from pathlib import Path

    from .readers import ReaderError, iter_paths, read_text

    directory = Path(directory)
    paths = list(iter_paths(directory, glob=glob))
    total = len(paths)
    results: dict[str, list[str] | str] = {}

    for index, path in enumerate(paths, start=1):
        rel = path.relative_to(directory).as_posix()
        if on_progress:
            on_progress(rel, index, total, None)
        try:
            text = read_text(path)
        except ReaderError as exc:
            results[rel] = f"skipped: {exc}"
        else:
            if not text.strip():
                results[rel] = "skipped: no text content"
            else:
                file_hint = f"Source file: {rel}." + (f" {hint}" if hint else "")
                try:
                    results[rel] = ingest_text(
                        bundle,
                        text[:max_chars],
                        prefix=prefix,
                        complete=complete,
                        model=model,
                        hint=file_hint,
                        source=rel,
                    )
                except LLMError as exc:
                    results[rel] = f"skipped: {exc}"
        if on_progress:
            on_progress(rel, index, total, results[rel])
    return results


ASK_SYSTEM = """\
You answer questions using ONLY the OKF knowledge concepts provided.
Each concept starts with `## <concept ID>`. Cite the IDs you used, inline
or at the end. If the knowledge does not contain the answer, say so
plainly instead of guessing. Answer in the language of the question."""


def ask(
    bundle: Bundle,
    question: str,
    limit: int = 5,
    complete: Completer | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple[str, list]:
    """Retrieve the most relevant concepts (BM25) and have the LLM answer
    from them. Returns ``(answer, hits)`` so callers can show the sources.
    """
    from .search import Index

    hits = Index(bundle).query(question, limit=limit)
    if not hits:
        return ("Aucun concept pertinent trouvé dans la base de connaissances.", [])
    context = "\n\n".join(
        f"## {h.concept_id}\n\n{bundle.get(h.concept_id).to_text()}" for h in hits
    )
    if complete is None:
        complete = lambda s, u: anthropic_complete(s, u, model=model)  # noqa: E731
    answer = complete(ASK_SYSTEM, f"Knowledge:\n\n{context}\n\n---\n\nQuestion: {question}")
    return answer.strip(), hits
