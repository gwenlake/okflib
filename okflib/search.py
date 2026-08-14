"""Lexical retrieval — rank concepts against short textual queries.

A small BM25 implementation (pure Python, no dependencies) with
accent-insensitive tokenization, so ``métrique`` matches ``metrique``.
Frontmatter fields are weighted above the body: a query word hitting a
title or tag matters more than one buried in prose.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

_WORD_RE = re.compile(r"\w+", re.UNICODE)

#: Field weights: how many times a field's tokens are counted.
WEIGHTS = {"id": 2, "title": 3, "description": 2, "tags": 3, "type": 1, "body": 1}


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return [t.lower() for t in _WORD_RE.findall(text) if len(t) > 1]


@dataclass
class Hit:
    concept_id: str
    score: float
    snippet: str


class Index:
    """A BM25 index over a bundle. Rebuild after mutating the bundle."""

    K1 = 1.5
    B = 0.75

    def __init__(self, bundle) -> None:
        self._bundle = bundle
        self._docs: dict[str, dict[str, int]] = {}
        self._df: dict[str, int] = {}
        self._lengths: dict[str, int] = {}
        for cid, c in bundle.items():
            tokens: list[str] = []
            fields = {
                "id": cid.replace("/", " ").replace("-", " "),
                "title": c.title or "",
                "description": c.description or "",
                "tags": " ".join(c.tags),
                "type": c.type,
                "body": c.body,
            }
            for field, weight in WEIGHTS.items():
                tokens += tokenize(fields[field]) * weight
            tf: dict[str, int] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            self._docs[cid] = tf
            self._lengths[cid] = len(tokens)
            for tok in tf:
                self._df[tok] = self._df.get(tok, 0) + 1
        n = max(len(self._docs), 1)
        self._avgdl = sum(self._lengths.values()) / n or 1.0
        self._n = n

    def query(self, text: str, limit: int = 5, min_score: float = 0.0) -> list[Hit]:
        terms = tokenize(text)
        scores: dict[str, float] = {}
        for term in terms:
            df = self._df.get(term)
            if not df:
                continue
            idf = math.log(1 + (self._n - df + 0.5) / (df + 0.5))
            for cid, tf_map in self._docs.items():
                tf = tf_map.get(term)
                if not tf:
                    continue
                dl = self._lengths[cid]
                denom = tf + self.K1 * (1 - self.B + self.B * dl / self._avgdl)
                scores[cid] = scores.get(cid, 0.0) + idf * tf * (self.K1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [
            Hit(cid, round(score, 3), self._snippet(cid, terms))
            for cid, score in ranked[:limit]
            if score > min_score
        ]

    def _snippet(self, cid: str, terms: list[str], width: int = 160) -> str:
        c = self._bundle.get(cid)
        if c.description:
            return c.description
        body = " ".join(c.body.split())
        low = body.lower()
        for term in terms:
            pos = low.find(term)
            if pos != -1:
                start = max(0, pos - width // 3)
                return ("…" if start else "") + body[start : start + width].strip()
        return body[:width]
