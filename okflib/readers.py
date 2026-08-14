"""File readers — extract plain text from documents for ingestion.

Core (stdlib only): markdown, txt, rst, csv/tsv, json, yaml, html
(tags stripped), and source code files. Optional, via
``pip install okflib[docs]``: PDF (pypdf) and Word (python-docx).
Unsupported or binary files are skipped with a reason.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

from .concept import OKFError

TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".text",
    ".rst",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".sql",
    ".sh",
    ".r",
    ".go",
    ".rs",
    ".java",
}
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}

SUPPORTED_SUFFIXES = TEXT_SUFFIXES | HTML_SUFFIXES | PDF_SUFFIXES | DOCX_SUFFIXES


class ReaderError(OKFError):
    """Raised when a file cannot be read as text."""


class _HTMLText(HTMLParser):
    _SKIP = {"script", "style"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def read_text(path: str | Path) -> str:
    """Extract plain text from a single file. Raises :class:`ReaderError`."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in PDF_SUFFIXES:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ReaderError(
                f"{path.name}: PDF support requires `pip install okflib[docs]`"
            ) from None
        try:
            return "\n\n".join(
                page.extract_text() or "" for page in PdfReader(str(path)).pages
            ).strip()
        except Exception as exc:
            raise ReaderError(f"{path.name}: {exc}") from exc

    if suffix in DOCX_SUFFIXES:
        try:
            import docx
        except ImportError:
            raise ReaderError(
                f"{path.name}: .docx support requires `pip install okflib[docs]`"
            ) from None
        try:
            return "\n".join(
                p.text for p in docx.Document(str(path)).paragraphs if p.text.strip()
            )
        except Exception as exc:
            raise ReaderError(f"{path.name}: {exc}") from exc

    if suffix in HTML_SUFFIXES:
        parser = _HTMLText()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parser.parts)

    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".json":  # pretty-print for the LLM
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        return text

    raise ReaderError(f"{path.name}: unsupported file type '{suffix or 'none'}'")


def iter_paths(directory: str | Path, glob: str = "**/*"):
    """Yield every candidate document path under ``directory``, without reading it.

    Cheap (stat calls only), so callers can count the work before doing it —
    which is what lets ingestion report ``[3/31]`` progress.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise OKFError(f"not a directory: {directory}")
    for path in sorted(directory.glob(glob)):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        yield path


def iter_documents(directory: str | Path, glob: str = "**/*"):
    """Yield ``(path, text)`` for every readable document under ``directory``,
    and ``(path, ReaderError)`` for files that were skipped."""
    for path in iter_paths(directory, glob=glob):
        try:
            text = read_text(path)
        except ReaderError as exc:
            yield path, exc
            continue
        if text.strip():
            yield path, text
