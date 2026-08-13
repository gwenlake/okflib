"""pyokf command-line interface.

Like ``git`` or ``uv``, the CLI works on the bundle you are *inside*: run
``pyokf init``, ``cd`` into it, and every command applies to it — no path to
repeat. Outside a bundle (or in scripts and CI), point at one with ``-C``.

Usage examples::

    pyokf init mon_bundle && cd mon_bundle
    pyokf add notes/idee --type Note --title "Idée" -d "À creuser."
    pyokf list --type Note --tier human-reviewed
    pyokf show notes/idee
    pyokf verify notes/idee --by human:sylvain@gwenlake.com
    pyokf validate
    pyokf ingest ~/documents/           # a directory, a file, or '-' for stdin
    pyokf ask "quel est le budget ?"

    pyokf -C mon_bundle stats           # from anywhere
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .bundle import Bundle, find_bundle_root, is_bundle_root
from .concept import OKFError
from .llm import DEFAULT_MODEL  # honours the PYOKF_MODEL env var

MODEL_HELP = f"LLM to use (default: {DEFAULT_MODEL}; set PYOKF_MODEL to change it)"


# ---------------------------------------------------------------------- #
# Progress reporting — LLM work takes minutes; say what is happening.
# Colour only on a terminal, and never when NO_COLOR is set (no-color.org).
# ---------------------------------------------------------------------- #

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
DIM, GREEN, YELLOW, BOLD = "2", "32", "33", "1"


def _paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("s" if count != 1 else "")


def _step(text: str) -> None:
    """Announce a step, leaving the line open for its outcome."""
    print(f"  {text} ", end="", flush=True)


def _done(result: list[str] | str) -> None:
    """Close the open line: a green tick, or a yellow warning with the reason."""
    if isinstance(result, str):
        print(_paint(f"⚠ {result}", YELLOW), flush=True)
    else:
        print(_paint(f"✓ {_plural(len(result), 'concept')}", GREEN), flush=True)


def _root(args) -> Path:
    """The bundle to operate on: ``-C`` if given, else discovered from the cwd."""
    if getattr(args, "bundle", None):
        root = Path(args.bundle)
        if not root.is_dir():
            raise OKFError(f"not a directory: {root}")
        return root
    root = find_bundle_root()
    if root is None:
        raise OKFError(
            "not inside an OKF bundle — run `pyokf init` here, cd into a bundle, "
            "or point at one with `pyokf -C <path> ...`"
        )
    return root


def _load(args) -> tuple[Bundle, Path]:
    root = _root(args)
    return Bundle.load(root), root


def cmd_init(args) -> int:
    root = Path(args.directory)
    root.mkdir(parents=True, exist_ok=True)
    if is_bundle_root(root):
        print(f"Already an OKF bundle: {root}")
        return 0
    Bundle().save(root)
    Bundle().append_log(root, "Bundle initialized.", kind="Initialization")
    print(f"Initialized empty OKF bundle in {root}")
    if root.resolve() != Path.cwd():
        print(f"Next: cd {root} — then commands apply to it without a path.")
    return 0


def cmd_add(args) -> int:
    bundle, root = _load(args)
    body = sys.stdin.read() if args.body == "-" else (args.body or "")
    bundle.create(
        args.id,
        type=args.type,
        title=args.title,
        description=args.description,
        tags=args.tag or [],
        status=args.status,
        body=body,
    )
    bundle.save(root)
    print(f"Added {args.id} ({args.type})")
    return 0


def cmd_list(args) -> int:
    bundle, _ = _load(args)
    items = bundle.items()
    if args.type:
        items = bundle.by_type(args.type)
    if args.tag:
        items = [(c, k) for c, k in items if args.tag in k.tags]
    if args.tier:
        items = [(c, k) for c, k in items if k.trust_tier == args.tier]
    if args.status:
        items = [(c, k) for c, k in items if k.effective_status == args.status]
    for cid, c in items:
        flags = [c.type, c.trust_tier]
        if c.is_deprecated:
            flags.append("deprecated")
        if c.is_stale():
            flags.append("STALE")
        desc = f" — {c.description}" if c.description else ""
        print(f"{cid}  [{', '.join(flags)}]{desc}")
    return 0


def cmd_show(args) -> int:
    bundle, _ = _load(args)
    print(bundle.get(args.id).to_text(), end="")
    return 0


def cmd_remove(args) -> int:
    bundle, root = _load(args)
    bundle.remove(args.id)
    bundle.save(root, clean=True)
    print(f"Removed {args.id}")
    return 0


def cmd_verify(args) -> int:
    bundle, root = _load(args)
    concept = bundle.get(args.id)
    concept.verify(by=args.by)
    bundle.save(root)
    print(f"Verified {args.id} by {args.by} -> tier: {concept.trust_tier}")
    return 0


def cmd_validate(args) -> int:
    bundle, _ = _load(args)
    report = bundle.validate()
    print(report)
    return 0 if report.conformant else 1


def cmd_search(args) -> int:
    bundle, _ = _load(args)
    for cid, c in bundle.search(args.text):
        print(f"{cid}  — {c.description or c.title or ''}")
    return 0


def cmd_stale(args) -> int:
    bundle, _ = _load(args)
    on = date.fromisoformat(args.on) if args.on else None
    for cid, c in bundle.stale(on):
        print(f"{cid}  (stale since {c.stale_after})")
    return 0


def cmd_log(args) -> int:
    bundle, root = _load(args)
    bundle.append_log(root, args.entry, kind=args.kind)
    print("Logged.")
    return 0


def cmd_graph(args) -> int:
    from .graph import FORMATS

    bundle, _ = _load(args)
    print(FORMATS[args.format](bundle), end="")
    return 0


def cmd_view(args) -> int:
    import webbrowser

    from .graph import to_html

    bundle, root = _load(args)
    out = Path(args.output) if args.output else root / "graph.html"
    out.write_text(to_html(bundle, title=args.title or root.resolve().name), "utf-8")
    print(f"Wrote {out} ({len(bundle)} concepts)")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_stats(args) -> int:
    import json as _json

    bundle, _ = _load(args)
    print(_json.dumps(bundle.stats(), indent=2, ensure_ascii=False))
    return 0


def cmd_export(args) -> int:
    bundle, root = _load(args)
    out = bundle.export_archive(root, args.archive)
    print(f"Wrote {out}")
    return 0


def cmd_query(args) -> int:
    from .search import Index

    bundle, _ = _load(args)
    hits = Index(bundle).query(args.text, limit=args.limit)
    if not hits:
        print("No matching concepts.")
        return 1
    for h in hits:
        print(f"{h.score:>7.3f}  {h.concept_id}  — {h.snippet}")
    return 0


def cmd_ask(args) -> int:
    from .llm import ask

    bundle, _ = _load(args)
    answer, hits = ask(bundle, args.question, limit=args.limit, model=args.model)
    print(answer)
    if hits:
        print("\nSources: " + ", ".join(h.concept_id for h in hits))
    return 0


def cmd_mcp(args) -> int:
    from .mcp import MCPServer

    bundle, _ = _load(args)
    return MCPServer(bundle, name=args.name).serve_stdio()


def cmd_ingest(args) -> int:
    # imported lazily: needs network/API key
    from .llm import ingest_dir, ingest_text

    bundle, root = _load(args)
    source = args.source
    label = "stdin" if source == "-" else Path(source).name
    print(_paint(f"Ingesting {label} with {args.model}", DIM))

    if source != "-" and Path(source).is_dir():
        # One LLM call per file, minutes for a large directory — report each
        # file as it is picked up so the run never looks frozen.
        def on_progress(rel, index, total, result):
            if result is None:
                _step(f"{_paint(f'[{index}/{total}]', DIM)} {rel}")
            else:
                _done(result)

        created: list[str] = []
        results = ingest_dir(
            bundle,
            source,
            prefix=args.prefix or "",
            model=args.model,
            hint=args.hint,
            on_progress=on_progress,
        )
        for res in results.values():
            if isinstance(res, list):
                created += res
        summary = (
            f"{_plural(len(created), 'concept')}"
            f" from {_plural(len(results), 'file')}"
        )
    else:
        # A single file goes through the readers too — a lone PDF must work
        # exactly like the same PDF inside an ingested directory.
        from .readers import read_text

        _step(label)
        text = sys.stdin.read() if source == "-" else read_text(source)
        created = ingest_text(
            bundle,
            text,
            prefix=args.prefix or "",
            model=args.model,
            hint=args.hint,
            source=None if source == "-" else source,
        )
        _done(created)
        summary = _plural(len(created), "concept")

    bundle.save(root)
    if created:
        bundle.append_log(
            root,
            f"Ingested {source}: " + ", ".join(f"[{c}](/{c}.md)" for c in created),
            kind="Creation",
        )
    print(_paint(summary, BOLD))
    if created:
        print(_paint("Review them: pyokf list --tier unverified", DIM))
    return 0


LEGACY_HINT = """\
error: `pyokf {command} {arg}` — the bundle is no longer an argument (since 0.5.0).

    cd {arg} && pyokf {command}{rest}
    pyokf -C {arg} {command}{rest}"""


def _legacy_bundle_arg(argv: list[str]) -> str | None:
    """Detect the pre-0.5 ``pyokf <cmd> <bundle> ...`` form and explain the move."""
    if len(argv) < 2 or argv[0].startswith("-") or argv[0] == "init":
        return None
    candidate = argv[1]
    if candidate.startswith("-") or not is_bundle_root(Path(candidate)):
        return None
    rest = " ".join(argv[2:])
    return LEGACY_HINT.format(
        command=argv[0], arg=candidate, rest=f" {rest}" if rest else ""
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    legacy = _legacy_bundle_arg(argv)
    if legacy:
        print(legacy, file=sys.stderr)
        return 2

    # `-C` is accepted both before and after the subcommand; SUPPRESS keeps the
    # subparser from clobbering a value given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-C",
        "--bundle",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="bundle to operate on (default: discovered from the current directory)",
    )

    parser = argparse.ArgumentParser(
        prog="pyokf",
        description=(
            "Manage Open Knowledge Format (OKF) bundles. Commands apply to the "
            "bundle you are inside, like git; use -C to point at another one."
        ),
        epilog="pyokf is developed and maintained by Gwenlake (https://gwenlake.com).",
        parents=[common],
    )
    # NB: no set_defaults(bundle=...) here — `parents=` shares the action object
    # with every subparser, and set_defaults would overwrite its SUPPRESS default,
    # making the subparser reset a `-C` given before the subcommand.
    parser.add_argument(
        "--version",
        action="version",
        version=f"pyokf {__version__} — a Gwenlake library (https://gwenlake.com)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help, parents=[common])

    p = add("init", "create a bundle here (or in the given directory)")
    p.add_argument("directory", nargs="?", default=".")
    p.set_defaults(func=cmd_init)

    p = add("add", "add a concept")
    p.add_argument("id", help="concept ID, e.g. notes/idee")
    p.add_argument("--type", required=True)
    p.add_argument("--title")
    p.add_argument("-d", "--description")
    p.add_argument("--tag", action="append", help="repeatable")
    p.add_argument("--status", choices=["draft", "stable", "deprecated"])
    p.add_argument("--body", help="markdown body, or '-' to read stdin")
    p.set_defaults(func=cmd_add)

    p = add("list", "list concepts")
    p.add_argument("--type")
    p.add_argument("--tag")
    p.add_argument("--tier", choices=["unverified", "machine-confirmed", "human-reviewed"])
    p.add_argument("--status", choices=["draft", "stable", "deprecated"])
    p.set_defaults(func=cmd_list)

    p = add("show", "print a concept file")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)

    p = add("remove", "remove a concept")
    p.add_argument("id")
    p.set_defaults(func=cmd_remove)

    p = add("verify", "append a verification stamp (v0.2)")
    p.add_argument("id")
    p.add_argument("--by", required=True, help="e.g. human:sylvain@gwenlake.com")
    p.set_defaults(func=cmd_verify)

    p = add("validate", "check OKF conformance")
    p.set_defaults(func=cmd_validate)

    p = add("search", "full-text search")
    p.add_argument("text")
    p.set_defaults(func=cmd_search)

    p = add("stale", "list concepts past their stale_after date")
    p.add_argument("--on", help="ISO date to evaluate against (default: today)")
    p.set_defaults(func=cmd_stale)

    p = add("log", "append an entry to the bundle log.md")
    p.add_argument("entry")
    p.add_argument("--kind", default="Update")
    p.set_defaults(func=cmd_log)

    p = add("graph", "export the link graph")
    p.add_argument(
        "--format", choices=["dot", "mermaid", "json", "html"], default="mermaid"
    )
    p.set_defaults(func=cmd_graph)

    p = add("view", "open the knowledge graph in your browser (interactive HTML)")
    p.add_argument("-o", "--output", help="write here instead of <bundle>/graph.html")
    p.add_argument("--title", help="page title (default: the bundle directory name)")
    p.add_argument("--no-open", action="store_true", help="write the file, don't open it")
    p.set_defaults(func=cmd_view)

    p = add("stats", "bundle statistics")
    p.set_defaults(func=cmd_stats)

    p = add("export", "pack the bundle as .tar.gz or .zip")
    p.add_argument("archive", help="output path, e.g. bundle.tar.gz or bundle.zip")
    p.set_defaults(func=cmd_export)

    p = add("query", "rank concepts against a short textual query (BM25)")
    p.add_argument("text")
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=cmd_query)

    p = add("ask", "LLM: answer a question from the knowledge base (RAG)")
    p.add_argument("question")
    p.add_argument("--limit", type=int, default=5, help="concepts retrieved as context")
    p.add_argument("--model", default=DEFAULT_MODEL, help=MODEL_HELP)
    p.set_defaults(func=cmd_ask)

    p = add("mcp", "serve the bundle to Claude via MCP (stdio)")
    p.add_argument("--name", default="pyokf")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser(
        "ingest",
        help="LLM: turn a directory, a file, or stdin into concepts (ANTHROPIC_API_KEY)",
        parents=[common],
        aliases=["ingest-dir"],  # pre-0.5 name, kept working
    )
    p.add_argument("source", help="directory, text file, or '-' for stdin")
    p.add_argument("--prefix", help="nest created concepts under this directory")
    p.add_argument("--model", default=DEFAULT_MODEL, help=MODEL_HELP)
    p.add_argument("--hint", help="steering instruction for the LLM")
    p.set_defaults(func=cmd_ingest)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except OKFError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # e.g. `pyokf list | head`
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
