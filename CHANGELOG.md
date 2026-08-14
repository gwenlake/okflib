# Changelog

## 0.5.0

**Breaking:** the CLI works on the bundle you are *inside*, like `git` and `uv` — the
bundle is no longer a positional argument.

- `okflib init` then `cd`, and every command applies to that bundle from anywhere inside
  it (the root is found by walking up, `Bundle.find_bundle_root` / `is_bundle_root`).
  Use `-C PATH` (accepted before or after the subcommand) to point elsewhere, e.g. in CI.
- The old form prints a migration hint instead of failing obscurely:
  `okflib list kb` → `cd kb && okflib list` or `okflib -C kb list`.
- `okflib init` takes an optional directory (defaults to the current one) and is idempotent.
- `ingest` and `ingest-dir` are one command: `okflib ingest <directory | file | ->`
  auto-detects the source. `ingest-dir` stays as an alias.
- `ingest` reports progress as it runs — one line per file (`[3/31] paper.pdf ✓ 5 concepts`),
  printed *before* each LLM call rather than all at the end. A 30-document run no longer
  looks frozen for minutes. `ingest_dir(..., on_progress=...)` exposes the same signal to
  library callers; nothing in the library prints. Colour on a terminal only, and never
  under `NO_COLOR`.
- `okflib view` now shows the **folder hierarchy** alongside the link graph: a hollow node
  per directory (`papers` → `papers/legislative` → its concepts), toggled by the **Folders**
  button. Concepts created by ingestion have no cross-links yet, so the link graph alone
  rendered them as isolated dots.
- `okflib view`: dragging a node keeps the layout live, so neighbours rearrange while you
  hold it and the graph re-settles on release.
- Fixed: `okflib view` produced a blank page — `hideTip` was a `const` referenced by code
  that runs earlier in the script, so the whole page died at load with a `ReferenceError`.
  The view also had no transform until the layout settled, leaving nodes off-screen for
  the first seconds; it now frames itself from the first frame. A test runs the page's JS
  against a stub DOM to catch both classes of failure.
- Model selection: `--model` on `ingest` / `ask` (already present) is now joined by the
  `OKFLIB_MODEL` environment variable, and the default moves from `claude-sonnet-4-6` to
  `claude-sonnet-5`. `claude-haiku-4-5` is a good, cheaper fit for bulk ingestion.
- Fixed: `okflib ingest <single file>` read the file as UTF-8 text instead of going through
  `okflib.readers`, so a lone PDF or .docx crashed with a `UnicodeDecodeError` while the
  same file inside an ingested directory worked.
- `okflib view`: an interactive HTML knowledge graph, opened in your browser —
  force-directed layout clustered by directory, colour by top-level directory,
  size by link count, search, click-to-inspect, and a table view. Self-contained
  (no CDN, no dependency); also available as `okflib graph --format html`.

## 0.4.0

- `okflib ingest-dir`: ingest every document in a directory (md, txt, html, csv, json, code natively; PDF/Word via the `docs` extra), one LLM call per file, originating file recorded in v0.2 `sources`
- BM25 retrieval (`okflib.search.Index`, `okflib query`): pure Python, accent-insensitive, frontmatter-weighted
- `okflib ask`: retrieve-then-answer (RAG) over the bundle with cited concept IDs
- MCP server (`okflib mcp`): stdio Model Context Protocol server exposing `search_knowledge` / `read_concept` / `list_concepts` to Claude and other MCP clients

## 0.3.0

- CLI: `graph` (DOT / Mermaid / JSON exports), `stats`, `export`
- `Bundle.load_archive()` for `.tar.gz` / `.zip` bundles
- `okf_version` declared in the bundle-root `index.md` frontmatter (SPEC §11) and read back on load
- `Bundle.stats()` summary counters
- uv-native project (`uv_build` backend, dependency groups, lockfile), `py.typed`, ruff, GitHub Actions CI
- Apache-2.0 license

## 0.2.0

- OKF v0.2 signal families: `generated`, `verified` (+ derived trust tiers), `status`, `stale_after`, `sources` with credibility signals and `[^id]` footnote checking
- Bundle filters: `by_trust_tier`, `by_status`, `stale`, `active`
- CLI (`init`, `add`, `list`, `show`, `remove`, `verify`, `validate`, `search`, `stale`, `log`, `ingest`)
- LLM ingestion: raw text -> OKF concepts (Anthropic API by default, pluggable completer)

## 0.1.0

- OKF v0.1 core: concepts, frontmatter round-trip, bundles, cross-links + graph, `index.md` / `log.md` generation, permissive validation, archive export
