"""MCP server — connect an OKF bundle to Claude (or any MCP client).

Implements the Model Context Protocol over stdio (newline-delimited
JSON-RPC 2.0, stdlib only). Once registered, Claude can search the
bundle, open concepts, and ground its answers in your knowledge base.

Register with Claude Code::

    claude mcp add my-kb -- pyokf mcp /path/to/bundle

or in a Claude Desktop / claude.ai connector config::

    {"mcpServers": {"my-kb": {"command": "pyokf", "args": ["mcp", "/path/to/bundle"]}}}

MCP docs: https://modelcontextprotocol.io
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import __version__
from .bundle import Bundle
from .concept import OKFError
from .search import Index

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "search_knowledge",
        "description": (
            "Search the OKF knowledge base with a short textual query "
            "(BM25, accent-insensitive). Returns ranked concept IDs with "
            "snippets. Use read_concept to open a result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Short search query"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_concept",
        "description": "Read one concept in full (frontmatter + markdown body) by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Concept ID"}},
            "required": ["id"],
        },
    },
    {
        "name": "list_concepts",
        "description": "List concept IDs with type/description, optionally filtered.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "tag": {"type": "string"},
                "trust_tier": {
                    "type": "string",
                    "enum": ["unverified", "machine-confirmed", "human-reviewed"],
                },
            },
        },
    },
]


class MCPServer:
    """Minimal stdio MCP server over a loaded :class:`Bundle`."""

    def __init__(self, bundle: Bundle, name: str = "pyokf") -> None:
        self.bundle = bundle
        self.name = name
        self._index = Index(bundle)

    # -- tool implementations ------------------------------------------ #

    def search_knowledge(self, query: str, limit: int = 5) -> str:
        hits = self._index.query(query, limit=limit)
        if not hits:
            return "No matching concepts."
        return "\n".join(f"{h.concept_id} (score {h.score}) — {h.snippet}" for h in hits)

    def read_concept(self, id: str) -> str:
        return self.bundle.get(id).to_text()

    def list_concepts(
        self,
        type: str | None = None,
        tag: str | None = None,
        trust_tier: str | None = None,
    ) -> str:
        lines = []
        for cid, c in self.bundle.items():
            if type and c.type != type:
                continue
            if tag and tag not in c.tags:
                continue
            if trust_tier and c.trust_tier != trust_tier:
                continue
            lines.append(f"{cid} [{c.type}] — {c.description or c.title or ''}")
        return "\n".join(lines) or "No concepts."

    # -- JSON-RPC dispatch --------------------------------------------- #

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            return self._result(
                msg_id,
                {
                    "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.name, "version": __version__},
                },
            )
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None  # notifications get no response
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            fn = {
                "search_knowledge": self.search_knowledge,
                "read_concept": self.read_concept,
                "list_concepts": self.list_concepts,
            }.get(name)
            if fn is None:
                return self._error(msg_id, -32602, f"unknown tool: {name}")
            try:
                text = fn(**args)
                return self._result(msg_id, {"content": [{"type": "text", "text": text}]})
            except (OKFError, TypeError) as exc:
                return self._result(
                    msg_id,
                    {
                        "content": [{"type": "text", "text": f"error: {exc}"}],
                        "isError": True,
                    },
                )
        if msg_id is not None:
            return self._error(msg_id, -32601, f"method not found: {method}")
        return None

    @staticmethod
    def _result(msg_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def serve_stdio(self) -> int:  # pragma: no cover - interactive loop
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle(message)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        return 0
