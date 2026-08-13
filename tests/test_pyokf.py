import re
import shutil
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pyokf import Bundle, Concept, ConceptNotFound, FrontmatterError, OKFError

SAMPLE = textwrap.dedent(
    """\
    ---
    type: BigQuery Table
    title: Orders
    description: One row per completed customer order.
    resource: https://console.cloud.google.com/bigquery?p=acme&d=sales&t=orders
    tags: [sales, orders]
    timestamp: 2026-05-28T14:30:00Z
    custom_field: hello
    ---

    # Schema

    FK to [customers](/tables/customers.md).
    See also [ext](https://example.com/doc).
    """
)


def test_parse_frontmatter():
    c = Concept.from_text(SAMPLE)
    assert c.type == "BigQuery Table"
    assert c.title == "Orders"
    assert c.tags == ["sales", "orders"]
    assert c.timestamp == datetime(2026, 5, 28, 14, 30, tzinfo=timezone.utc)
    assert c.extra == {"custom_field": "hello"}  # unknown keys preserved
    assert "# Schema" in c.body


def test_missing_type_rejected():
    with pytest.raises(FrontmatterError):
        Concept.from_text("---\ntitle: no type here\n---\nbody\n")


def test_missing_frontmatter_rejected():
    with pytest.raises(FrontmatterError):
        Concept.from_text("just markdown, no frontmatter\n")


def test_round_trip():
    c = Concept.from_text(SAMPLE)
    c2 = Concept.from_text(c.to_text())
    assert c2 == c


def test_links():
    c = Concept.from_text(SAMPLE)
    links = list(c.links())
    assert len(links) == 2
    internal = list(c.concept_links())
    assert len(internal) == 1
    assert internal[0].target == "/tables/customers.md"
    assert internal[0].is_bundle_absolute


def test_bundle_save_load(tmp_path: Path):
    b = Bundle()
    b.create(
        "tables/orders",
        type="BigQuery Table",
        title="Orders",
        description="Orders table.",
        tags=["sales"],
    )
    b.create(
        "tables/customers",
        type="BigQuery Table",
        title="Customers",
        description="Customers table.",
    )
    b.create(
        "playbooks/incident",
        type="Playbook",
        title="Incident",
        description="What to do.",
        body="See [orders](/tables/orders.md).\n",
    )
    b.save(tmp_path)

    # concept files + generated indexes
    assert (tmp_path / "tables" / "orders.md").exists()
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "tables" / "index.md").exists()

    loaded = Bundle.load(tmp_path)
    assert len(loaded) == 3
    assert loaded["tables/orders"].title == "Orders"
    # index.md must not be loaded as a concept
    assert "index" not in loaded


def test_reserved_names_rejected():
    b = Bundle()
    with pytest.raises(OKFError):
        b.create("docs/index", type="Note")


def test_queries():
    b = Bundle()
    b.create("a", type="Metric", tags=["kpi"], description="d")
    b.create("b", type="Playbook", tags=["kpi", "oncall"], description="d")
    assert [cid for cid, _ in b.by_type("Metric")] == ["a"]
    assert [cid for cid, _ in b.by_tag("kpi")] == ["a", "b"]
    assert [cid for cid, _ in b.search("oncall")] == ["b"]


def test_graph_and_validation():
    b = Bundle()
    b.create("a", type="Note", description="d", body="[to b](/b.md) and [ghost](/nope.md)")
    b.create("b", type="Note", description="d")
    g = b.graph()
    assert g["a"] == {"b", "nope"}

    report = b.validate()
    assert report.conformant  # broken links are non-fatal (SPEC §9)
    assert any("broken link" in str(i) for i in report)


def test_relative_link_resolution():
    b = Bundle()
    b.create("dir/a", type="Note", description="d", body="[sib](./b.md) [up](../c.md)")
    b.create("dir/b", type="Note", description="d")
    b.create("c", type="Note", description="d")
    assert b.graph()["dir/a"] == {"dir/b", "c"}
    assert b.validate().conformant
    assert not any("broken" in str(i) for i in b.validate())


def test_log(tmp_path: Path):
    b = Bundle()
    b.append_log(tmp_path, "Created [a](/a.md).", kind="Creation")
    b.append_log(tmp_path, "Updated [a](/a.md).")
    text = (tmp_path / "log.md").read_text()
    assert text.startswith("# Directory Update Log")
    assert "**Creation**" in text and "**Update**" in text


def test_missing_concept():
    b = Bundle()
    with pytest.raises(ConceptNotFound):
        b.get("nope")


def test_permissive_load(tmp_path: Path):
    (tmp_path / "good.md").write_text("---\ntype: Note\n---\nok\n")
    (tmp_path / "bad.md").write_text("no frontmatter at all\n")
    b = Bundle.load(tmp_path)  # permissive by default
    assert b.ids() == ["good"]
    with pytest.raises(FrontmatterError):
        Bundle.load(tmp_path, strict=True)


# --------------------------------------------------------------------- #
# OKF v0.2 — trust, provenance, lifecycle
# --------------------------------------------------------------------- #

from datetime import date  # noqa: E402

V02_SAMPLE = textwrap.dedent(
    """\
    ---
    type: BigQuery Table
    title: Customer Orders
    description: One row per completed customer order.
    tags: [sales, orders]
    generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-30T14:00:00Z }
    verified:
      - { by: human:kliu@acme, at: 2026-07-01T16:00:00Z }
    status: stable
    stale_after: 2026-12-31
    sources:
      - id: warehouse-schema
        resource: https://wiki.acme.internal/data/warehouse/schemas/sales
        title: Warehouse schema
        author: team:data-platform
        usage_count: 1240
        last_modified: 2026-06-15
    ---

    # Schema

    `order_id` is unique. [^warehouse-schema]
    """
)


def test_v02_parse():
    c = Concept.from_text(V02_SAMPLE)
    assert c.generated.by == "reference_agent/gemini-2.5-pro"
    assert c.generated.at.year == 2026
    assert c.trust_tier == "human-reviewed"
    assert c.effective_status == "stable"
    assert c.stale_after == date(2026, 12, 31)
    assert not c.is_stale(on=date(2026, 8, 14))
    assert c.is_stale(on=date(2027, 1, 1))
    src = c.sources[0]
    assert src.id == "warehouse-schema" and src.usage_count == 1240
    assert c.footnote_refs() == ["warehouse-schema"]


def test_v02_round_trip():
    c = Concept.from_text(V02_SAMPLE)
    c2 = Concept.from_text(c.to_text())
    assert c2 == c


def test_trust_tiers():
    c = Concept(type="Metric")
    assert c.trust_tier == "unverified"
    c.verify(by="nightly-finance-job")
    assert c.trust_tier == "machine-confirmed"
    c.verify(by="human:jsmith@acme")
    assert c.trust_tier == "human-reviewed"
    assert len(c.verified) == 2


def test_v01_timestamp_compat():
    # A v0.1 concept with `timestamp` maps onto generated.at ...
    c = Concept.from_text(SAMPLE)
    assert c.timestamp is not None
    assert c.generated is not None and c.generated.at == c.timestamp
    # ... and serializes as v0.2 `generated`
    assert "generated:" in c.to_text() and "timestamp:" not in c.to_text()


def test_v02_bundle_filters():
    b = Bundle()
    b.create(
        "m/current",
        type="Metric",
        description="d",
        status="stable",
        verified=[],
        stale_after=date(2099, 1, 1),
    )
    b.create("m/legacy", type="Metric", description="d", status="deprecated")
    b.create("m/old", type="Metric", description="d", stale_after=date(2020, 1, 1))
    hr = b.create("m/hr", type="Metric", description="d")
    hr.verify(by="human:vp@acme")

    assert [cid for cid, _ in b.by_status("deprecated")] == ["m/legacy"]
    assert [cid for cid, _ in b.by_trust_tier("human-reviewed")] == ["m/hr"]
    assert [cid for cid, _ in b.stale(on=date(2026, 8, 14))] == ["m/old"]
    active = [cid for cid, _ in b.active(on=date(2026, 8, 14))]
    assert active == ["m/current", "m/hr"]


def test_v02_validation_warnings():
    b = Bundle()
    b.create("a", type="Note", description="d", status="wip", body="claim [^ghost]")
    report = b.validate()
    assert report.conformant  # all v0.2 issues are warnings, never fatal
    msgs = str(report)
    assert "unknown status" in msgs and "footnote" in msgs


# --------------------------------------------------------------------- #
# CLI + LLM ingestion
# --------------------------------------------------------------------- #

import json  # noqa: E402

from pyokf.cli import main as cli_main  # noqa: E402
from pyokf.llm import LLMError, _parse_json_array, ingest_text  # noqa: E402


def test_cli_workflow(tmp_path, capsys):
    root = str(tmp_path / "b")
    assert cli_main(["init", root]) == 0
    assert (
        cli_main(
            [
                "-C",
                root,
                "add",
                "notes/idee",
                "--type",
                "Note",
                "--title",
                "Idée",
                "-d",
                "À creuser.",
                "--tag",
                "demo",
            ]
        )
        == 0
    )
    # -C is also accepted after the subcommand
    assert (
        cli_main(["verify", "notes/idee", "-C", root, "--by", "human:sylvain@gwenlake.com"])
        == 0
    )
    assert cli_main(["-C", root, "list", "--tier", "human-reviewed"]) == 0
    out = capsys.readouterr().out
    assert "notes/idee" in out and "human-reviewed" in out
    assert cli_main(["-C", root, "validate"]) == 0
    assert cli_main(["-C", root, "show", "notes/idee"]) == 0
    assert "type: Note" in capsys.readouterr().out
    assert cli_main(["-C", root, "remove", "notes/idee"]) == 0
    assert not (tmp_path / "b" / "notes" / "idee.md").exists()


def test_cli_discovers_bundle_from_cwd(tmp_path, monkeypatch, capsys):
    root = tmp_path / "kb"
    assert cli_main(["init", str(root)]) == 0
    assert cli_main(["-C", str(root), "add", "notes/idee", "--type", "Note"]) == 0
    capsys.readouterr()

    # from a subdirectory of the bundle, like `git status`
    monkeypatch.chdir(root / "notes")
    assert cli_main(["list"]) == 0
    assert "notes/idee" in capsys.readouterr().out

    # outside any bundle: a clear error, not a traceback
    monkeypatch.chdir(tmp_path)
    assert cli_main(["list"]) == 2
    assert "not inside an OKF bundle" in capsys.readouterr().err


def test_cli_init_is_idempotent_and_defaults_to_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli_main(["init"]) == 0
    assert (tmp_path / "index.md").exists()
    assert cli_main(["init"]) == 0
    assert "Already an OKF bundle" in capsys.readouterr().out


def test_cli_rejects_legacy_bundle_argument(tmp_path, capsys):
    root = str(tmp_path / "kb")
    assert cli_main(["init", root]) == 0
    capsys.readouterr()
    assert cli_main(["list", root, "--tier", "unverified"]) == 2
    err = capsys.readouterr().err
    assert "no longer an argument" in err and f"pyokf -C {root} list" in err


def fake_complete(system, user):
    assert "OKF" in system
    return json.dumps(
        [
            {
                "id": "notes/reunion",
                "type": "Note",
                "title": "Réunion",
                "description": "CR de réunion.",
                "tags": ["cr"],
                "body": "# Décisions\n\n- Budget validé\n",
            },
            {
                "id": "metrics/budget",
                "type": "Metric",
                "title": "Budget",
                "description": "Budget projet.",
                "tags": [],
                "body": "Voir [réunion](/notes/reunion.md).\n",
            },
        ]
    )


def test_ingest_text():
    b = Bundle()
    created = ingest_text(b, "Réunion: budget validé...", complete=fake_complete)
    assert created == ["notes/reunion", "metrics/budget"]
    c = b["notes/reunion"]
    assert c.generated.by.startswith("pyokf/")
    assert c.trust_tier == "unverified"  # LLM output starts unverified
    assert b.validate().conformant


def test_ingest_prefix_and_collision():
    b = Bundle()
    ingest_text(b, "x", complete=fake_complete, prefix="import")
    created = ingest_text(b, "x", complete=fake_complete, prefix="import")
    assert "import/notes/reunion" in b
    assert created[0] == "import/notes/reunion-2"  # no silent overwrite


def test_parse_json_array_with_fences():
    raw = '```json\n[{"type": "Note"}]\n```'
    assert _parse_json_array(raw) == [{"type": "Note"}]
    with pytest.raises(LLMError):
        _parse_json_array("sorry, no json here")


def test_ingest_missing_type():
    b = Bundle()
    with pytest.raises(LLMError):
        ingest_text(b, "x", complete=lambda s, u: '[{"title": "no type"}]')


# --------------------------------------------------------------------- #
# Completeness features: okf_version, archives, graph exports, stats
# --------------------------------------------------------------------- #

from pyokf.graph import to_dot, to_json, to_mermaid  # noqa: E402


def _demo_bundle():
    b = Bundle()
    b.create(
        "tables/orders",
        type="Table",
        title="Orders",
        description="d",
        body="FK to [customers](/tables/customers.md) and [ghost](/nope.md).",
    )
    b.create("tables/customers", type="Table", title="Customers", description="d")
    return b


def test_okf_version_round_trip(tmp_path):
    b = _demo_bundle()
    b.save(tmp_path)
    text = (tmp_path / "index.md").read_text()
    assert text.startswith('---\nokf_version: "0.2"\n---')
    loaded = Bundle.load(tmp_path)
    assert loaded.okf_version == "0.2"
    assert len(loaded) == 2  # root index still not loaded as a concept


def test_archive_round_trip(tmp_path):
    b = _demo_bundle()
    for name in ("b.tar.gz", "b.zip"):
        archive = b.export_archive(tmp_path / "src", tmp_path / name)
        loaded = Bundle.load_archive(archive)
        assert loaded.ids() == b.ids()
        assert loaded.okf_version == "0.2"


def test_graph_exports():
    b = _demo_bundle()
    dot, mermaid, js = to_dot(b), to_mermaid(b), to_json(b)
    assert '"tables/orders" -> "tables/customers"' in dot
    assert "style=dashed" in dot  # ghost node for the broken link
    assert mermaid.startswith("graph LR")
    assert "-->" in mermaid
    data = json.loads(js)
    assert {"source": "tables/orders", "target": "nope"} in data["edges"]
    assert any(n.get("missing") for n in data["nodes"])


def test_html_export_is_self_contained():
    from pyokf.graph import to_html

    html = to_html(_demo_bundle(), title="Démo")
    assert html.startswith("<!doctype html>")
    assert "<title>Démo</title>" in html
    # no network: everything inlined, the only URL is the SVG namespace
    assert "<script src" not in html and "<link " not in html
    assert set(re.findall(r"https?://[^\"'\s]+", html)) == {"http://www.w3.org/2000/svg"}
    payload = json.loads(
        html.split('<script id="data" type="application/json">')[1].split("</script>")[0]
    )
    assert {n["id"] for n in payload["nodes"]} == {
        "tables/orders",
        "tables/customers",
        "nope",
        "dir:tables",  # the folder itself is a node — see below
    }
    assert any(n.get("missing") for n in payload["nodes"])
    assert payload["groups"] == ["tables"]  # colour grouping by top-level directory

    # Two structures: markdown links (the OKF graph) and folder containment
    link = {"s": "tables/orders", "t": "tables/customers", "kind": "link"}
    assert link in payload["links"]
    tree = {e["t"] for e in payload["links"] if e["kind"] == "tree"}
    assert tree == {"tables/orders", "tables/customers"}


def test_payload_nests_directories():
    """A folder node per path segment: papers → papers/legislative → concepts."""
    from pyokf.graph import _payload

    b = Bundle()
    b.create("papers/legislative/take-it-down", type="Reference", description="d")
    b.create("papers/legislative/free-speech", type="Reference", description="d")
    b.create("papers/other", type="Note", description="d")
    payload = _payload(b)

    folders = {n["id"]: n for n in payload["nodes"] if n["kind"] == "directory"}
    assert set(folders) == {"dir:papers", "dir:papers/legislative"}
    assert folders["dir:papers"]["size"] == 3  # counts the whole subtree
    assert folders["dir:papers/legislative"]["size"] == 2
    assert folders["dir:papers/legislative"]["label"] == "legislative"

    tree = {(e["s"], e["t"]) for e in payload["links"] if e["kind"] == "tree"}
    assert ("dir:papers", "dir:papers/legislative") in tree  # folder → subfolder
    assert ("dir:papers/legislative", "papers/legislative/free-speech") in tree
    assert ("dir:papers", "papers/other") in tree


def test_ingest_dir_reports_progress(tmp_path):
    (tmp_path / "a.md").write_text("# A\n\nContenu.")
    (tmp_path / "b.md").write_text("# B\n\nAutre.")
    (tmp_path / "empty.md").write_text("   \n")
    b = Bundle()
    seen = []
    ingest_dir(
        b,
        tmp_path,
        complete=fake_complete,
        on_progress=lambda rel, i, total, res: seen.append((rel, i, total, res)),
    )
    # two calls per file: picked up (result None), then the outcome
    assert [(rel, i, total) for rel, i, total, _ in seen] == [
        ("a.md", 1, 3), ("a.md", 1, 3),
        ("b.md", 2, 3), ("b.md", 2, 3),
        ("empty.md", 3, 3), ("empty.md", 3, 3),
    ]
    assert seen[0][3] is None and isinstance(seen[1][3], list)
    assert seen[-1][3] == "skipped: no text content"


def test_cli_ingest_shows_progress(tmp_path, monkeypatch, capsys):
    root = tmp_path / "kb"
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("# Titre\n\nContenu.")
    assert cli_main(["init", str(root)]) == 0
    monkeypatch.setattr(
        "pyokf.llm.anthropic_complete", lambda s, u, **kw: fake_complete(s, u)
    )
    assert cli_main(["-C", str(root), "ingest", str(docs)]) == 0
    out = capsys.readouterr().out
    assert "with claude-" in out          # header names the model
    assert "[1/1] note.md" in out         # per-file progress, before the call
    assert "✓ 2 concepts" in out          # outcome closes the same line
    assert "\033[" not in out             # no ANSI codes when not a terminal


def test_cli_ingest_single_file_uses_readers(tmp_path, monkeypatch, capsys):
    """A lone file goes through readers.read_text, like one inside a directory."""
    root = tmp_path / "kb"
    assert cli_main(["init", str(root)]) == 0
    page = tmp_path / "page.html"
    page.write_text("<html><body><h1>Titre</h1><p>Contenu.</p></body></html>")

    seen = {}

    def fake_ingest_text(bundle, text, **kwargs):
        seen["text"] = text
        bundle.create("notes/x", type="Note", title="X")
        return ["notes/x"]

    monkeypatch.setattr("pyokf.llm.ingest_text", fake_ingest_text)
    assert cli_main(["-C", str(root), "ingest", str(page)]) == 0
    # HTML tags stripped by the reader, not read as raw bytes
    assert "Titre" in seen["text"] and "<h1>" not in seen["text"]
    assert (root / "notes" / "x.md").exists()

    # an unsupported type fails with the reader's message, not a UnicodeDecodeError
    binary = tmp_path / "image.png"
    binary.write_bytes(b"\x89PNG\xe2\x80\x94")
    assert cli_main(["-C", str(root), "ingest", str(binary)]) == 2
    assert "unsupported file type" in capsys.readouterr().err


DOM_HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const js = html.split('</scr' + 'ipt>').slice(-2)[0].split('<scr' + 'ipt>').pop();
const payload = html.split('<scr' + 'ipt id="data" type="application/json">')[1]
  .split('</scr' + 'ipt>')[0];

const W = 1200, H = 800;
const mk = () => ({
  style: {}, classList: { toggle(){}, add(){}, remove(){}, contains(){ return false } },
  attrs: {}, children: [], listeners: {}, textContent: '', innerHTML: '',
  setAttribute(k, v) { this.attrs[k] = v }, getAttribute(k) { return this.attrs[k] },
  appendChild(c) { this.children.push(c); return c },
  append(...c) { this.children.push(...c) },
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f) },
  querySelectorAll(){ return [] },
  getBoundingClientRect: () => ({ width: W, height: H, left: 0, top: 0 }),
  get firstChild(){ return this.children[0] },
  get lastChild(){ return this.children[this.children.length - 1] },
});
const els = {};
for (const id of ['viz','scene','edges','nodes','labels','q','fit','toggle-tree',
                  'toggle-table','legend','details','tableview','tip']) els[id] = mk();
els['data'] = Object.assign(mk(), { textContent: payload });
global.document = {
  getElementById: (id) => els[id] || mk(),
  createElementNS: () => mk(), createElement: () => mk(),
  createTextNode: (t) => ({ nodeValue: t }), body: mk(),
};
global.window = global; global.innerWidth = W; global.innerHeight = H;
const win = {};
global.addEventListener = (t, f) => (win[t] = win[t] || []).push(f);
let frames = 0, nextFrame = null;
global.requestAnimationFrame = (fn) => { nextFrame = fn; if (frames++ < 300) fn(); };

eval(js);  // throws on any load-time error (TDZ, typo, bad API use)

const fail = (m) => { console.error(m); process.exit(1); };

/* 1. the graph frames itself immediately, instead of sitting off-screen */
const m = /translate\(([-\d.e]+) ([-\d.e]+)\) scale\(([-\d.e]+)\)/
  .exec(els['scene'].getAttribute('transform') || '');
if (!m) fail('no transform applied — the graph would render off-screen');
const [, tx, ty, k] = m.map(Number);
const circles = els['nodes'].children;
if (!circles.length) fail('no nodes drawn');
const at = (c) => [Number(c.getAttribute('cx')), Number(c.getAttribute('cy'))];
if (!circles.every((c) => {
  const [x, y] = at(c);
  return x * k + tx > 0 && x * k + tx < W && y * k + ty > 0 && y * k + ty < H;
})) fail('nodes fall outside the viewport');

/* 2. dragging a node keeps the simulation warm so the others rearrange */
const gap = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const start = circles.map(at);
circles[0].listeners['pointerdown'][0](
  { stopPropagation(){}, clientX: 100, clientY: 100, target: circles[0] });
const hold = (from, count) => {
  for (let i = from; i < from + count; i++) {
    win['pointermove'].forEach((f) => f({ clientX: 100 + i * 3, clientY: 100 + i * 2 }));
    if (nextFrame) nextFrame();
  }
  return circles.map(at);
};
// Two stretches: the second one is well past the point where an unrefreshed
// simulation would have cooled to a stop, which is the failure being guarded.
const early = hold(0, 200);
const late = hold(200, 200);
if (gap(start[0], late[0]) < 50) fail('the dragged node did not follow the pointer');
const reacting = early.slice(1).filter((p, i) => gap(p, late[i + 1]) > 1).length;
if (reacting < Math.ceil((circles.length - 1) / 2)) {
  fail(`only ${reacting}/${circles.length - 1} neighbours moved late in the drag — `
     + 'the simulation cooled to a stop while the node was still held');
}
console.log(`ok: ${circles.length} nodes in view, ${reacting} rearranged on drag`);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_html_graph_boots_and_frames_itself(tmp_path):
    """Run the page's JS against a stub DOM.

    `node --check` only validates syntax; this catches load-time failures (a
    `const` used before its declaration killed the whole script once), verifies
    the graph is inside the viewport on the first frame, and simulates a drag to
    check the layout keeps reacting while a node is held.
    """
    from pyokf.graph import to_html

    page = tmp_path / "graph.html"
    page.write_text(to_html(_demo_bundle()), encoding="utf-8")
    harness = tmp_path / "dom.js"
    harness.write_text(DOM_HARNESS, encoding="utf-8")

    proc = subprocess.run(
        ["node", str(harness), str(page)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "nodes in view" in proc.stdout


def test_cli_view_writes_html(tmp_path, capsys):
    root = str(tmp_path / "b")
    _demo_bundle().save(root)
    assert cli_main(["-C", root, "view", "--no-open"]) == 0
    assert "graph.html" in capsys.readouterr().out
    assert (tmp_path / "b" / "graph.html").read_text().startswith("<!doctype html>")

    out = tmp_path / "elsewhere.html"
    assert cli_main(["-C", root, "view", "--no-open", "-o", str(out)]) == 0
    assert "tables/orders" in out.read_text()


def test_stats():
    b = _demo_bundle()
    b["tables/orders"].verify(by="human:x@y")
    s = b.stats()
    assert s["concepts"] == 2
    assert s["by_type"] == {"Table": 2}
    assert s["by_trust_tier"]["human-reviewed"] == 1
    assert s["broken_links"] == 1


def test_cli_graph_stats_export(tmp_path, capsys):
    root = str(tmp_path / "b")
    _demo_bundle().save(root)
    assert cli_main(["-C", root, "graph", "--format", "dot"]) == 0
    assert "digraph okf" in capsys.readouterr().out
    assert cli_main(["-C", root, "stats"]) == 0
    assert '"concepts": 2' in capsys.readouterr().out
    archive = str(tmp_path / "out.tar.gz")
    assert cli_main(["-C", root, "export", archive]) == 0
    assert Bundle.load_archive(archive).ids() == ["tables/customers", "tables/orders"]


# --------------------------------------------------------------------- #
# Readers, BM25 retrieval, directory ingestion, ask, MCP
# --------------------------------------------------------------------- #

from pyokf.llm import ask, ingest_dir  # noqa: E402
from pyokf.mcp import MCPServer  # noqa: E402
from pyokf.readers import ReaderError, iter_documents, read_text  # noqa: E402
from pyokf.search import Index  # noqa: E402


def test_readers(tmp_path):
    (tmp_path / "note.md").write_text("# Titre\n\nContenu markdown.")
    (tmp_path / "page.html").write_text(
        "<html><style>x{}</style><body><h1>Hello</h1><p>World</p></body></html>"
    )
    (tmp_path / "data.json").write_text('{"a": 1}')
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / ".hidden.md").write_text("nope")
    assert "Contenu markdown" in read_text(tmp_path / "note.md")
    html = read_text(tmp_path / "page.html")
    assert "Hello" in html and "x{}" not in html
    assert '"a": 1' in read_text(tmp_path / "data.json")
    with pytest.raises(ReaderError):
        read_text(tmp_path / "image.png")
    docs = dict(
        (p.name, t) for p, t in iter_documents(tmp_path) if not isinstance(t, ReaderError)
    )
    assert set(docs) == {"note.md", "page.html", "data.json"}


def _kb():
    b = Bundle()
    b.create(
        "metrics/chiffre-affaires",
        type="Metric",
        title="Chiffre d'affaires",
        description="CA reconnu selon la politique FY2026.",
        tags=["finance"],
        body="Somme des commandes livrées, net des retours.",
    )
    b.create(
        "tables/commandes",
        type="Table",
        title="Commandes",
        description="Une ligne par commande client.",
        tags=["ventes"],
        body="Colonnes: id, montant, statut de livraison.",
    )
    b.create(
        "playbooks/astreinte",
        type="Playbook",
        title="Astreinte data",
        description="Procédure en cas d'alerte fraîcheur.",
        tags=["oncall"],
        body="Vérifier le dashboard d'ingestion puis relancer le job.",
    )
    return b


def test_bm25_query():
    idx = Index(_kb())
    hits = idx.query("chiffre d'affaires")
    assert hits[0].concept_id == "metrics/chiffre-affaires"
    # accent-insensitive: 'procedure' matches 'Procédure'
    assert idx.query("procedure alerte")[0].concept_id == "playbooks/astreinte"
    assert idx.query("zzz-inconnu") == []


def test_ingest_dir(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "reunion.txt").write_text("Réunion: budget validé.")
    (tmp_path / "sub" / "notes.md").write_text("Autres notes.")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    b = Bundle()
    results = ingest_dir(b, tmp_path, complete=fake_complete, prefix="import")
    assert set(results) == {"reunion.txt", "sub/notes.md"}
    assert all(isinstance(v, list) for v in results.values())
    # provenance recorded on every generated concept
    c = b["import/notes/reunion"]
    assert c.sources and c.sources[0].resource == "reunion.txt"
    assert b.validate().conformant


def test_ask_uses_retrieved_context():
    b = _kb()
    seen = {}

    def answering(system, user):
        seen["user"] = user
        return "Le CA est la somme des commandes livrées. (metrics/chiffre-affaires)"

    answer, hits = ask(b, "comment est calculé le chiffre d'affaires ?", complete=answering)
    assert "metrics/chiffre-affaires" in seen["user"]  # retrieved into context
    assert hits[0].concept_id == "metrics/chiffre-affaires"
    assert "commandes livrées" in answer

    answer, hits = ask(b, "zzz-inconnu", complete=answering)
    assert hits == [] and "Aucun concept" in answer


def _rpc(server, method, msg_id=None, **params):
    return server.handle(
        {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
    )


def test_mcp_server():
    server = MCPServer(_kb(), name="test-kb")
    init = _rpc(
        server,
        "initialize",
        msg_id=1,
        protocolVersion="2025-06-18",
        capabilities={},
        clientInfo={"name": "claude"},
    )
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"]["name"] == "test-kb"
    assert _rpc(server, "notifications/initialized") is None

    tools = _rpc(server, "tools/list", msg_id=2)["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "search_knowledge",
        "read_concept",
        "list_concepts",
    }

    r = _rpc(
        server,
        "tools/call",
        msg_id=3,
        name="search_knowledge",
        arguments={"query": "astreinte"},
    )
    assert "playbooks/astreinte" in r["result"]["content"][0]["text"]

    r = _rpc(
        server,
        "tools/call",
        msg_id=4,
        name="read_concept",
        arguments={"id": "tables/commandes"},
    )
    assert r["result"]["content"][0]["text"].startswith("---\ntype: Table")

    r = _rpc(server, "tools/call", msg_id=5, name="read_concept", arguments={"id": "nope"})
    assert r["result"]["isError"] is True

    r = _rpc(
        server, "tools/call", msg_id=6, name="list_concepts", arguments={"type": "Metric"}
    )
    assert "metrics/chiffre-affaires" in r["result"]["content"][0]["text"]

    assert _rpc(server, "bogus/method", msg_id=7)["error"]["code"] == -32601


def test_cli_query(tmp_path, capsys):
    root = str(tmp_path / "kb")
    _kb().save(root)
    assert cli_main(["-C", root, "query", "alerte fraicheur"]) == 0
    assert "playbooks/astreinte" in capsys.readouterr().out
    assert cli_main(["-C", root, "query", "zzz-inconnu"]) == 1
