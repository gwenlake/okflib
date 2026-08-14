"""Graph exports — render the concept link graph (SPEC §5.3).

The graph treats every concept as a node and every internal markdown
link as a directed, untyped edge. Broken-link targets appear as dashed
"ghost" nodes: per the spec they may simply represent not-yet-written
knowledge.

``to_dot`` / ``to_mermaid`` / ``to_json`` are text exports; ``to_html``
renders a self-contained, interactive page (no CDN, no build step) for
looking at a large, deeply nested bundle as a whole.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import PurePosixPath


def _split(bundle) -> tuple[dict[str, set[str]], set[str], set[str]]:
    edges = bundle.graph()
    known = set(bundle.ids())
    ghosts = {t for targets in edges.values() for t in targets} - known
    return edges, known, ghosts


def to_dot(bundle) -> str:
    """Graphviz DOT — render with e.g. ``dot -Tsvg graph.dot -o graph.svg``."""
    edges, known, ghosts = _split(bundle)
    lines = ["digraph okf {", "  rankdir=LR;", '  node [shape=box, fontname="Helvetica"];']
    for cid in sorted(known):
        c = bundle.get(cid)
        label = (c.title or cid).replace('"', r"\"")
        lines.append(f'  "{cid}" [label="{label}\\n({c.type})"];')
    for ghost in sorted(ghosts):
        lines.append(f'  "{ghost}" [style=dashed, color=gray, label="{ghost}?"];')
    for src in sorted(edges):
        for dst in sorted(edges[src]):
            lines.append(f'  "{src}" -> "{dst}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def to_mermaid(bundle) -> str:
    """Mermaid ``graph LR`` — renders natively in GitHub markdown."""
    edges, known, ghosts = _split(bundle)
    ids = {cid: f"n{i}" for i, cid in enumerate(sorted(known | ghosts))}
    lines = ["graph LR"]
    for cid in sorted(known):
        c = bundle.get(cid)
        title = (c.title or cid).replace('"', "'")
        lines.append(f'  {ids[cid]}["{title}"]')
    for ghost in sorted(ghosts):
        lines.append(f'  {ids[ghost]}("{ghost}?")')
    for src in sorted(edges):
        for dst in sorted(edges[src]):
            lines.append(f"  {ids[src]} --> {ids[dst]}")
    return "\n".join(lines) + "\n"


def to_json(bundle) -> str:
    """Nodes/edges JSON for programmatic consumers and graph viewers."""
    edges, known, ghosts = _split(bundle)
    nodes = []
    for cid in sorted(known):
        c = bundle.get(cid)
        nodes.append(
            {
                "id": cid,
                "type": c.type,
                "title": c.title,
                "status": c.effective_status,
                "trust_tier": c.trust_tier,
            }
        )
    nodes += [{"id": g, "missing": True} for g in sorted(ghosts)]
    links = [{"source": s, "target": t} for s in sorted(edges) for t in sorted(edges[s])]
    return json.dumps({"nodes": nodes, "edges": links}, indent=2, ensure_ascii=False) + "\n"


def _payload(bundle, groups: int = 3) -> dict:
    """Nodes/links plus the colour grouping used by the HTML view.

    Two kinds of edge are returned, because a bundle has two structures and
    only one of them is the OKF link graph:

    - ``kind: "tree"`` — containment. A directory node per path segment, so
      ``papers`` → ``papers/legislative-recommendations`` → the concepts in it.
      Freshly ingested concepts usually have no cross-links yet; without this
      the view is a scatter of isolated dots.
    - ``kind: "link"`` — the markdown links between concepts (SPEC §5), the
      actual knowledge graph.

    Directory IDs are prefixed ``dir:`` because a bundle may legitimately hold
    both ``papers.md`` (concept ``papers``) and ``papers/`` (a directory).

    Concepts are grouped by top-level directory. Only the ``groups`` largest get
    a categorical colour (the validated palette clears its all-pairs gates at
    three slots); the rest share a neutral, and every node carries a visible
    label, so identity is never colour-alone.
    """
    edges, known, ghosts = _split(bundle)
    counts: Counter = Counter(_top_dir(cid) for cid in known)
    top = [name for name, _ in counts.most_common(groups)]
    group_of = lambda d: d if d in top else "other"  # noqa: E731

    nodes = []
    tree: list[dict] = []
    directories: Counter = Counter()

    for cid in sorted(known):
        c = bundle.get(cid)
        parts = PurePosixPath(cid).parts[:-1]
        for depth in range(len(parts)):
            directories["/".join(parts[: depth + 1])] += 1
        nodes.append(
            {
                "id": cid,
                "kind": "concept",
                "label": c.title or PurePosixPath(cid).name,
                "type": c.type,
                "description": c.description or "",
                "tier": c.trust_tier,
                "status": c.effective_status,
                "stale": c.is_stale(),
                "dir": _top_dir(cid),
                "group": group_of(_top_dir(cid)),
            }
        )
        if parts:
            tree.append({"s": f"dir:{'/'.join(parts)}", "t": cid, "kind": "tree"})

    for path, size in sorted(directories.items()):
        parent = str(PurePosixPath(path).parent)
        nodes.append(
            {
                "id": f"dir:{path}",
                "kind": "directory",
                "label": PurePosixPath(path).name,
                "type": "directory",
                "description": f"{size} concept(s) under {path}/",
                "size": size,
                "dir": path.split("/", 1)[0],
                "group": group_of(path.split("/", 1)[0]),
            }
        )
        if parent != ".":
            tree.append({"s": f"dir:{parent}", "t": f"dir:{path}", "kind": "tree"})

    nodes += [
        {
            "id": g,
            "kind": "missing",
            "label": g,
            "type": "missing",
            "group": "missing",
            "missing": True,
        }
        for g in sorted(ghosts)
    ]
    links = [
        {"s": s, "t": t, "kind": "link"} for s in sorted(edges) for t in sorted(edges[s])
    ]
    return {"nodes": nodes, "links": links + tree, "groups": top}


def _top_dir(concept_id: str) -> str:
    parts = PurePosixPath(concept_id).parts
    return parts[0] if len(parts) > 1 else "(root)"


def to_html(bundle, title: str = "OKF knowledge graph") -> str:
    """A self-contained interactive page: force-directed graph + table view.

    Everything is inlined (no CDN, no dependency), so the output is a single
    file you can open, commit, or send to someone.
    """
    payload = json.dumps(_payload(bundle), ensure_ascii=False, indent=None)
    payload = payload.replace("</", "<\\/")  # never break out of the <script>
    return (
        HTML_TEMPLATE.replace("__TITLE__", _escape(title))
        .replace("__COUNTS__", _escape(_summary(bundle)))
        .replace("__DATA__", payload)
    )


def _summary(bundle) -> str:
    edges, known, ghosts = _split(bundle)
    links = sum(len(t) for t in edges.values())
    out = f"{len(known)} concepts · {links} links"
    if ghosts:
        out += f" · {len(ghosts)} broken"
    return out


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  /* Palette: validated categorical slots 1-3 (all-pairs, both modes) plus a
     neutral for the tail. Dark steps are chosen for the dark surface, not flipped. */
  :root {
    color-scheme: light;
    --surface-0: #f5f4f1; --surface-1: #fcfcfb; --border: #dcdbd5;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #7b7a75;
    --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;
    --series-other: #7b7a75; --missing: #a3a29a;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --surface-0: #131312; --surface-1: #1a1a19; --border: #3a3a37;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #8a897f;
      --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
      --series-other: #8a897f; --missing: #6f6e69;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface-0: #131312; --surface-1: #1a1a19; --border: #3a3a37;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #8a897f;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
    --series-other: #8a897f; --missing: #6f6e69;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--surface-0); color: var(--text-primary);
    font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    height: 100vh; display: flex; flex-direction: column;
  }
  header {
    display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
    padding: 10px 16px; background: var(--surface-1);
    border-bottom: 1px solid var(--border);
  }
  h1 { font-size: 15px; font-weight: 600; margin: 0; }
  .count { color: var(--text-secondary); font-variant-numeric: tabular-nums; }
  .spacer { flex: 1; }
  input[type=search], button {
    font: inherit; color: inherit; background: var(--surface-0);
    border: 1px solid var(--border); border-radius: 6px; padding: 5px 9px;
  }
  button { cursor: pointer; }
  button[aria-pressed=true] { background: var(--text-primary); color: var(--surface-1); }
  main { flex: 1; display: flex; min-height: 0; }
  #viz { flex: 1; display: block; touch-action: none; background: var(--surface-0); }
  aside {
    width: 300px; flex-shrink: 0; border-left: 1px solid var(--border);
    background: var(--surface-1); padding: 14px 16px; overflow-y: auto;
  }
  aside h2 { font-size: 13px; margin: 0 0 8px; text-transform: uppercase;
             letter-spacing: .06em; color: var(--text-secondary); }
  aside dl { margin: 0 0 14px; display: grid;
             grid-template-columns: auto 1fr; gap: 3px 10px; }
  aside dt { color: var(--text-muted); }
  aside dd { margin: 0; word-break: break-word; }
  aside ul { margin: 0 0 14px; padding-left: 18px; }
  aside a { color: inherit; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 14px;
            margin: 0 0 14px; padding: 0; list-style: none; }
  .legend li { display: flex; align-items: center; gap: 6px;
               color: var(--text-secondary); }
  .swatch { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .swatch.ghost { background: none; border: 2px dashed var(--missing); }
  .swatch.folder { background: none; border: 2px solid var(--text-secondary); }
  #tableview { display: none; overflow: auto; flex: 1; padding: 0 16px 16px; }
  body.table-open #tableview { display: block; }
  body.table-open main { display: none; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th { position: sticky; top: 0;
       background: var(--surface-0); color: var(--text-secondary); }
  #tip {
    position: fixed; pointer-events: none; opacity: 0; transition: opacity .1s;
    background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 6px; box-shadow: 0 2px 10px rgb(0 0 0 / .18);
    padding: 6px 9px; font-size: 13px; max-width: 280px;
  }
  #tip b { display: block; }
  #tip span { color: var(--text-secondary); }
  text { font-size: 11px; fill: var(--text-secondary); pointer-events: none;
         paint-order: stroke; stroke: var(--surface-0);
         stroke-width: 3px; stroke-linejoin: round; }
  circle { stroke: var(--surface-0); stroke-width: 2px; cursor: grab; }
  circle.dragging { cursor: grabbing; stroke: var(--text-primary); }
  circle.missing { fill: none; stroke: var(--missing);
                   stroke-width: 2px; stroke-dasharray: 3 3; }
  /* Directories are containers, not knowledge: hollow, so a filled dot always
     means a concept. */
  circle.dir { fill: var(--surface-0); stroke-width: 3px; }
  line { stroke: var(--text-muted); stroke-width: 1.5px; opacity: .45; }
  line.tree { stroke-width: 1px; opacity: .3; stroke-dasharray: 2 3; }
  text.dir { font-size: 12px; font-weight: 600; fill: var(--text-primary); }
  .dim { opacity: .12; }
  body.hide-tree .tree, body.hide-tree .is-dir { display: none; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <span class="count">__COUNTS__</span>
  <span class="spacer"></span>
  <input type="search" id="q" placeholder="Filter concepts…" autocomplete="off">
  <button id="fit">Fit</button>
  <button id="toggle-tree" aria-pressed="true">Folders</button>
  <button id="toggle-table" aria-pressed="false">Table</button>
</header>
<main>
  <svg id="viz"><g id="scene">
    <g id="edges"></g><g id="nodes"></g><g id="labels"></g>
  </g></svg>
  <aside>
    <h2>Directories</h2>
    <ul class="legend" id="legend"></ul>
    <h2>Selection</h2>
    <div id="details"><p style="color:var(--text-muted)">Click a concept to
      inspect it. Drag to move, scroll to zoom.</p></div>
  </aside>
</main>
<div id="tableview"></div>
<div id="tip" role="status"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const COLORS = ['--series-1', '--series-2', '--series-3'];
const groupColor = g =>
  g === 'missing' ? 'var(--missing)'
  : DATA.groups.indexOf(g) > -1 ? `var(${COLORS[DATA.groups.indexOf(g)]})`
  : 'var(--series-other)';

const svg = document.getElementById('viz');
const scene = document.getElementById('scene');
const nodes = DATA.nodes;
const byId = new Map(nodes.map(n => [n.id, n]));
const links = DATA.links.filter(l => byId.has(l.s) && byId.has(l.t));
// Size concepts by how connected they are — by real markdown links, not by the
// containment edges, which every concept has exactly one of.
const deg = new Map(nodes.map(n => [n.id, 0]));
links.filter(l => l.kind === 'link')
  .forEach(l => { deg.set(l.s, deg.get(l.s) + 1); deg.set(l.t, deg.get(l.t) + 1); });
const neighbours = new Map(nodes.map(n => [n.id, new Set([n.id])]));
links.forEach(l => { neighbours.get(l.s).add(l.t); neighbours.get(l.t).add(l.s); });
const radius = n => n.kind === 'directory'
  ? 7 + 3 * Math.sqrt(n.size || 1)
  : 5 + 3 * Math.sqrt(deg.get(n.id) || 0);

/* --- layout: spring-electrical, with each directory pulled to its own anchor,
   so nesting shows up as clusters instead of a hairball ------------------- */
const dirs = [...new Set(nodes.map(n => n.dir || n.group))];
const anchors = new Map(dirs.map((d, i) => {
  const a = (2 * Math.PI * i) / dirs.length;
  return [d, { x: Math.cos(a) * 260, y: Math.sin(a) * 260 }];
}));
nodes.forEach((n, i) => {
  const a = anchors.get(n.dir || n.group) || { x: 0, y: 0 };
  const t = (i * 2.39996);                       // golden-angle jitter, deterministic
  n.x = a.x + Math.cos(t) * 60; n.y = a.y + Math.sin(t) * 60; n.vx = 0; n.vy = 0;
});
const K = Math.max(40, 320 / Math.sqrt(nodes.length || 1));
function tick(alpha) {
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx * dx + dy * dy || 0.01;
      if (d2 > 640000) continue;                 // ignore far pairs: keeps big graphs fast
      const f = (K * K) / d2;
      const d = Math.sqrt(d2);
      a.vx += (dx / d) * f; a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
  }
  for (const l of links) {
    const a = byId.get(l.s), b = byId.get(l.t);
    const dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const f = (d - K) / d * 0.12;
    a.vx += dx * f; a.vy += dy * f;
    b.vx -= dx * f; b.vy -= dy * f;
  }
  for (const n of nodes) {
    const a = anchors.get(n.dir || n.group) || { x: 0, y: 0 };
    n.vx += (a.x - n.x) * 0.006; n.vy += (a.y - n.y) * 0.006;
    if (n.fixed) { n.vx = n.vy = 0; continue; }
    n.x += Math.max(-30, Math.min(30, n.vx * alpha));
    n.y += Math.max(-30, Math.min(30, n.vy * alpha));
    n.vx *= 0.55; n.vy *= 0.55;
  }
}

/* --- render ------------------------------------------------------------- */
const gEdges = document.getElementById('edges');
const gNodes = document.getElementById('nodes');
const gLabels = document.getElementById('labels');
const NS = 'http://www.w3.org/2000/svg';
const showAllLabels = nodes.length <= 120;
const lineOf = new Map(), circleOf = new Map(), labelOf = new Map();

for (const l of links) {
  const el = document.createElementNS(NS, 'line');
  if (l.kind === 'tree') el.classList.add('tree');
  gEdges.appendChild(el); lineOf.set(l, el);
}
for (const n of nodes) {
  const c = document.createElementNS(NS, 'circle');
  c.setAttribute('r', radius(n));
  if (n.missing) c.classList.add('missing');
  else if (n.kind === 'directory') {
    c.classList.add('dir');
    c.setAttribute('stroke', groupColor(n.group));
  } else c.setAttribute('fill', groupColor(n.group));
  c.addEventListener('pointerenter', e => showTip(e, n));
  c.addEventListener('pointerleave', hideTip);
  c.addEventListener('pointerdown', e => startDrag(e, n));
  // A drag ends with a click event too — don't hijack the panel on release.
  c.addEventListener('click', () => {
    if (suppressClick) { suppressClick = false; return; }
    select(n.id);
  });
  if (n.kind === 'directory') c.classList.add('is-dir');
  gNodes.appendChild(c); circleOf.set(n, c);

  const t = document.createElementNS(NS, 'text');
  t.textContent = n.label;
  // Directory names are the map's skeleton — always legible.
  if (n.kind === 'directory') t.classList.add('dir', 'is-dir');
  const named = n.kind === 'directory' || showAllLabels || deg.get(n.id) > 2;
  t.style.display = named ? '' : 'none';
  gLabels.appendChild(t); labelOf.set(n, t);
}
function draw() {
  for (const l of links) {
    const a = byId.get(l.s), b = byId.get(l.t), el = lineOf.get(l);
    el.setAttribute('x1', a.x); el.setAttribute('y1', a.y);
    el.setAttribute('x2', b.x); el.setAttribute('y2', b.y);
  }
  for (const n of nodes) {
    circleOf.get(n).setAttribute('cx', n.x); circleOf.get(n).setAttribute('cy', n.y);
    const t = labelOf.get(n);
    t.setAttribute('x', n.x + radius(n) + 4); t.setAttribute('y', n.y + 4);
  }
}
let alpha = 1, running = true;
function loop() {
  if (running) {
    for (let i = 0; i < 2; i++) tick(alpha);
    alpha *= 0.985;
    if (alpha < 0.02) running = false;
    draw();
    // Keep the graph framed while it settles — without this the scene has no
    // transform until the simulation ends and the first seconds look empty.
    if (!drag && !pan) fit();
  }
  requestAnimationFrame(loop);
}

/* --- zoom, pan, drag ---------------------------------------------------- */
let view = { x: 0, y: 0, k: 1 };
const applyView = () =>
  scene.setAttribute('transform', `translate(${view.x} ${view.y}) scale(${view.k})`);
function fit() {
  const pad = 60, r = svg.getBoundingClientRect();
  const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  view.k = Math.min(4, Math.max(0.1, Math.min(
    (r.width - pad * 2) / (maxX - minX || 1),
    (r.height - pad * 2) / (maxY - minY || 1))));
  view.x = r.width / 2 - ((minX + maxX) / 2) * view.k;
  view.y = r.height / 2 - ((minY + maxY) / 2) * view.k;
  applyView();
}
svg.addEventListener('wheel', e => {
  e.preventDefault();
  const r = svg.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const k = Math.min(6, Math.max(0.05, view.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
  view.x = mx - (mx - view.x) * (k / view.k); view.y = my - (my - view.y) * (k / view.k);
  view.k = k; applyView();
}, { passive: false });
let pan = null;
svg.addEventListener('pointerdown', e => {
  if (e.target === svg || e.target.tagName === 'line')
    pan = { x: e.clientX - view.x, y: e.clientY - view.y };
});
addEventListener('pointermove', e => {
  if (pan) { view.x = e.clientX - pan.x; view.y = e.clientY - pan.y; applyView(); }
  if (drag) {
    const r = svg.getBoundingClientRect();
    drag.node.x = (e.clientX - r.left - view.x) / view.k;
    drag.node.y = (e.clientY - r.top - view.y) / view.k;
    if (Math.hypot(e.clientX - drag.fromX, e.clientY - drag.fromY) > 4) drag.moved = true;
    // Hold the simulation warm while the node is held, so its neighbours keep
    // rearranging around it instead of freezing once the layout has settled.
    alpha = Math.max(alpha, 0.3); running = true;
    draw();
  }
});
addEventListener('pointerup', () => {
  pan = null;
  if (drag) {
    circleOf.get(drag.node).classList.remove('dragging');
    // Let go: the node rejoins the simulation and the graph re-settles.
    drag.node.fixed = false;
    suppressClick = drag.moved;
    drag = null;
    alpha = Math.max(alpha, 0.2); running = true;
  }
});
let drag = null, suppressClick = false;
function startDrag(e, n) {
  e.stopPropagation();
  n.fixed = true;
  drag = { node: n, moved: false, fromX: e.clientX, fromY: e.clientY };
  circleOf.get(n).classList.add('dragging');
  if (e.target.setPointerCapture) e.target.setPointerCapture(e.pointerId);
  alpha = Math.max(alpha, 0.3); running = true;
}
document.getElementById('fit').onclick = fit;

/* --- tooltip, selection, filter ----------------------------------------- */
const tip = document.getElementById('tip');
function showTip(e, n) {
  tip.innerHTML = `<b></b><span></span>`;
  tip.firstChild.textContent = n.id;
  tip.lastChild.textContent = n.missing
    ? 'broken link — not written yet'
    : n.kind === 'directory'
    ? `folder · ${n.size} concept${n.size === 1 ? '' : 's'}`
    : [n.type, n.tier, n.status !== 'stable' ? n.status : null, n.stale ? 'STALE' : null]
        .filter(Boolean).join(' · ');
  tip.style.opacity = 1;
  tip.style.left = Math.min(innerWidth - 300, e.clientX + 14) + 'px';
  tip.style.top = (e.clientY + 16) + 'px';
}
// A function declaration, not a const: it is referenced by the node-creation
// loop far above, which runs before this line.
function hideTip() { tip.style.opacity = 0; }

const details = document.getElementById('details');
function select(id) {
  const n = byId.get(id);
  const keep = neighbours.get(id);
  for (const m of nodes) circleOf.get(m).classList.toggle('dim', !keep.has(m.id));
  for (const m of nodes) labelOf.get(m).classList.toggle('dim', !keep.has(m.id));
  for (const l of links) lineOf.get(l).classList.toggle('dim', l.s !== id && l.t !== id);
  const pick = (kind, from, to) =>
    links.filter(l => l.kind === kind && l[from] === id).map(l => l[to]);
  const children = pick('tree', 's', 't');
  const folder = pick('tree', 't', 's')[0];
  const label = x => (byId.get(x) || {}).kind === 'directory' ? x.slice(4) : x;
  const list = a => a.length
    ? `<ul>${a.map(x =>
        `<li><a href="#" data-goto="${x}">${label(x)}</a></li>`).join('')}</ul>`
    : '<p style="color:var(--text-muted)">none</p>';

  if (n.kind === 'directory') {
    details.innerHTML = `<dl>
        <dt>Folder</dt><dd>${n.id.slice(4)}</dd>
        <dt>Holds</dt><dd>${n.size} concept${n.size === 1 ? '' : 's'}</dd>
      </dl>
      <h2>Contains</h2>${list(children)}`;
  } else {
    details.innerHTML = `<dl>
        <dt>ID</dt><dd>${n.id}</dd>
        <dt>Type</dt><dd>${n.type}</dd>
        <dt>Folder</dt><dd>${folder ? folder.slice(4) : '—'}</dd>
        <dt>Trust</dt><dd>${n.missing ? '—' : n.tier}</dd>
        <dt>Status</dt><dd>${n.missing ? 'broken link'
          : n.status + (n.stale ? ' · stale' : '')}</dd>
      </dl>
      ${n.description ? `<p>${n.description}</p>` : ''}
      <h2>Links to</h2>${list(pick('link', 's', 't'))}
      <h2>Linked from</h2>${list(pick('link', 't', 's'))}`;
  }
  details.querySelectorAll('[data-goto]').forEach(a =>
    a.onclick = ev => { ev.preventDefault(); select(a.dataset.goto); });
}
document.getElementById('q').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  for (const n of nodes) {
    const haystack = `${n.id} ${n.label} ${n.description || ''}`.toLowerCase();
    const hit = !q || haystack.includes(q);
    circleOf.get(n).classList.toggle('dim', !hit);
    labelOf.get(n).classList.toggle('dim', !hit);
    const named = showAllLabels || deg.get(n.id) > 2 || (q && hit);
    labelOf.get(n).style.display = named ? '' : 'none';
  }
  for (const l of links) lineOf.get(l).classList.toggle('dim', !!q);
});

/* --- legend + table view (identity is never colour-alone) ---------------- */
const legend = document.getElementById('legend');
if (nodes.some(n => n.kind === 'directory')) {
  const li = document.createElement('li');
  const sw = document.createElement('span');
  sw.className = 'swatch folder';
  li.append(sw, document.createTextNode('folder (hollow)'));
  legend.appendChild(li);
}
for (const g of [...DATA.groups, 'other', 'missing']) {
  if (g === 'other' && DATA.groups.length >= new Set(nodes.map(n => n.dir)).size) continue;
  if (g === 'missing' && !nodes.some(n => n.missing)) continue;
  const li = document.createElement('li');
  const sw = document.createElement('span');
  sw.className = 'swatch' + (g === 'missing' ? ' ghost' : '');
  if (g !== 'missing') sw.style.background = groupColor(g);
  const name = g === 'other' ? 'other directories'
    : g === 'missing' ? 'broken link' : g;
  li.append(sw, document.createTextNode(name));
  legend.appendChild(li);
}
const tv = document.getElementById('tableview');
tv.innerHTML = `<table><thead><tr>
    <th>Concept</th><th>Type</th><th>Directory</th><th>Trust</th><th>Status</th><th>Links</th>
  </tr></thead><tbody>${nodes.map(n => `<tr>
    <td>${n.id}</td><td>${n.type}</td><td>${n.dir || '—'}</td>
    <td>${n.missing ? '—' : n.tier}</td>
    <td>${n.missing ? 'broken link' : n.status + (n.stale ? ' · stale' : '')}</td>
    <td>${deg.get(n.id)}</td></tr>`).join('')}</tbody></table>`;
const tbtn = document.getElementById('toggle-table');
tbtn.onclick = () => {
  const open = document.body.classList.toggle('table-open');
  tbtn.setAttribute('aria-pressed', String(open));
};

// Folders off = the pure OKF link graph (markdown links only).
const fbtn = document.getElementById('toggle-tree');
fbtn.onclick = () => {
  const hidden = document.body.classList.toggle('hide-tree');
  fbtn.setAttribute('aria-pressed', String(!hidden));
  alpha = Math.max(alpha, 0.3); running = true;
};
addEventListener('resize', fit);

/* Start once everything above exists: `loop` reads `drag`/`pan`, which are
   `let` bindings declared later in this script. */
draw();
fit();
loop();
</script>
</body>
</html>
"""


FORMATS = {"dot": to_dot, "mermaid": to_mermaid, "json": to_json, "html": to_html}
