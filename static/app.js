const $ = s => document.querySelector(s);
const el = (t, cls, txt) => {
  const n = document.createElement(t);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};

const SLOTS = ["QB", "RB", "WR", "TE", "WR/RB/TE", "K", "DEF"];
const DEFAULTS = { QB: 1, RB: 2, WR: 2, TE: 1, "WR/RB/TE": 1, K: 1, DEF: 1 };
SLOTS.forEach(s => {
  const l = el("label"); l.append(document.createTextNode(s));
  const i = el("input"); i.type = "number"; i.min = 0; i.max = 6;
  i.value = DEFAULTS[s]; i.dataset.slot = s;
  l.append(i); $("#roster").append(l);
});

$("#lastw").addEventListener("input", () => {
  const v = +$("#lastw").value;
  $("#lastwv-proj").textContent = 100 - v;
  $("#lastwv-last").textContent = v;
});

let ORDER = [];        // current list, user-editable
let PLAYERS = {};      // name -> player
let reorderTimer = null;

function cfg() {
  const roster = {};
  document.querySelectorAll("#roster input").forEach(i => {
    if (+i.value > 0) roster[i.dataset.slot] = +i.value;
  });
  return {
    teams: +$("[name=teams]").value, slot: +$("[name=slot]").value,
    bench: +$("[name=bench]").value, style: $("[name=style]").value,
    preset: $("#preset").value, roster,
    last_weight: +$("#lastw").value,
    order: ORDER,
  };
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  const d = r.headers.get("content-type")?.includes("json")
    ? await r.json() : await r.text();
  if (!r.ok) throw new Error(d.error || "Something went wrong.");
  return d;
}

$("#cfg").addEventListener("submit", async e => {
  e.preventDefault();
  const btn = $("#go");
  btn.disabled = true; $("#msg").textContent = "Building…";
  try {
    const d = await post("/api/build", cfg());
    ORDER = d.players.map(p => p.name);
    PLAYERS = {};
    d.players.forEach(p => { PLAYERS[p.name] = p; });
    renderBoard();
    renderTips(d.tips);
    renderRosterPreview(d.rosterPreview);
    $("#listwrap").hidden = false;
    $("#rosterwrap").hidden = false;
    $("#availwrap").hidden = false;
    $("#simwrap").hidden = false;
    $("#simout").hidden = true;
    $("#msg").textContent = "";
    refreshAvailability();
    $("#listwrap").scrollIntoView({ behavior: "smooth" });
  } catch (err) { $("#msg").textContent = err.message; }
  finally { btn.disabled = false; }
});

function renderTips(tips) {
  $("#tiplist").replaceChildren(...tips.map(t => {
    const li = el("li");
    const kind = el("span", "kind", t.kind === "news" ? "News" : "Value");
    li.append(kind, el("b", null, `${t.name} (${t.pos})`),
              document.createTextNode(" " + t.headline));
    if (t.url) {
      const a = el("a"); a.href = t.url; a.target = "_blank";
      a.rel = "noopener"; a.textContent = "Read more →";
      li.append(a);
    }
    return li;
  }));
}

// ── list / detail ──
function renderBoard() {
  const starters = starterCount();
  const board = $("#board");
  board.replaceChildren();
  ORDER.forEach((name, i) => {
    const p = PLAYERS[name];
    const row = el("div", "row" + (i < starters ? " starter" : ""));
    row.append(el("span", "rk", i + 1),
               el("span", "pos", p.pos),
               el("span", "nm", `${p.name}${p.bye ? " · bye " + p.bye : ""}`),
               el("span", "pts", p.pts));
    const mv = el("span", "mv");
    const up = el("button", null, "▲"), dn = el("button", null, "▼");
    up.type = "button"; dn.type = "button";
    up.onclick = ev => { ev.stopPropagation(); move(i, -1); };
    dn.onclick = ev => { ev.stopPropagation(); move(i, +1); };
    mv.append(up, dn); row.append(mv);
    row.onclick = () => toggleDetail(row, p);
    board.append(row);
  });
}

function starterCount() {
  return Object.values(cfg().roster).reduce((a, b) => a + b, 0);
}

function move(i, d) {
  const j = i + d;
  if (j < 0 || j >= ORDER.length) return;
  [ORDER[i], ORDER[j]] = [ORDER[j], ORDER[i]];
  renderBoard();
  scheduleReorderRefresh();
}

// Reordering changes both the likely roster and the availability odds.
// Debounced so a burst of arrow clicks does not fire a request per click.
function scheduleReorderRefresh() {
  clearTimeout(reorderTimer);
  reorderTimer = setTimeout(() => {
    refreshRosterPreview();
    refreshAvailability();
  }, 500);
}

function toggleDetail(row, p) {
  if (row.nextSibling?.classList?.contains("detail")) {
    row.nextSibling.remove(); return;
  }
  document.querySelectorAll(".detail").forEach(d => d.remove());
  row.after(buildDetail(p));
}

function buildDetail(p) {
  const d = el("div", "detail");

  const stats = el("div", "stats");
  const stat = (label, val) => {
    const s = el("div", "stat");
    s.append(el("b", null, val), el("span", null, label));
    stats.append(s);
  };
  stat("Points (blended)", p.pts);
  stat("Projection", p.proj);
  stat("Last season", p.actual != null ? p.actual : "no data");
  stat("Edge over replacement", p.vorp != null ? p.vorp : "—");
  stat(`${p.pos} rank`, p.posRank || "—");
  if (p.dropToNext) stat("Points above next " + p.pos, p.dropToNext);
  stat("Weekly variance", p.measured ? `±${p.sd}` : `±${p.sd} (estimated)`);
  d.append(stats);

  if (p.news && p.news.length) {
    const n = el("div", "news");
    n.append(el("h3", null, "Recent news"));
    p.news.forEach(a => {
      const row = el("div");
      const link = el("a"); link.href = a.url; link.target = "_blank";
      link.rel = "noopener"; link.textContent = a.headline;
      const time = el("time", null, a.published);
      row.append(link, time);
      n.append(row);
    });
    d.append(n);
  } else {
    const n = el("div", "news");
    n.append(el("h3", null, "Recent news"), el("p", null, "No recent ESPN coverage found."));
    d.append(n);
  }

  const avail = el("div", "avail");
  const btn = el("button", "ghost", "Check draft odds for this player");
  btn.type = "button";
  btn.onclick = async () => {
    btn.disabled = true; btn.textContent = "Checking…";
    try {
      const r = await post("/api/availability", cfg());
      const hit = r.players.find(a => a.name === p.name);
      btn.replaceWith(availSummary(hit));
    } catch (e) {
      btn.textContent = "Check draft odds for this player";
      btn.disabled = false;
    }
  };
  avail.append(btn);
  d.append(avail);

  return d;
}

function availSummary(hit) {
  if (!hit) return el("p", "hint", "Not tracked — outside your top targets.");
  const p = el("p", "hint");
  p.innerHTML = `At pick <b>${hit.atPick}</b> (usual ADP ${hit.adp}), he lasts that long in <b>${hit.pct}%</b> of drafts.`;
  return p;
}

// ── roster preview ──
function renderRosterPreview(roster) {
  const box = $("#rosterpreview");
  box.replaceChildren();
  if (!roster || !roster.length) {
    box.append(el("p", "hint", "Not enough players in the pool to fill this roster."));
    return;
  }
  roster.forEach(p => {
    const slot = el("div", "slot");
    slot.append(el("div", "lbl", p.slot));
    const nm = el("div", "nm");
    nm.append(document.createTextNode(`${p.name} (${p.pos})`),
              el("span", "pts", `${p.pts} pts${p.bye ? " · bye " + p.bye : ""}`));
    slot.append(nm);
    box.append(slot);
  });
}

async function refreshRosterPreview() {
  try {
    const d = await post("/api/roster-preview", cfg());
    renderRosterPreview(d.roster);
  } catch { /* the build already validated the league; a transient failure here is not worth surfacing */ }
}

// ── availability ──
function bandFor(pct) {
  if (pct >= 60) return "";
  if (pct >= 25) return "risky";
  return "long";
}

function renderAvailability(players) {
  const box = $("#availlist");
  box.replaceChildren();
  if (!players || !players.length) {
    box.append(el("p", "availmsg", "No contested picks in range yet."));
    return;
  }
  players.forEach(a => {
    const row = el("div", "availrow");
    const nm = el("span", "nm");
    nm.append(el("span", "pos", a.pos), document.createTextNode(a.name));
    const meta = el("span", "meta", `pick ${a.atPick} · ADP ${a.adp}`);
    const bar = el("span", "availbar " + bandFor(a.pct));
    const i = el("i"); i.style.width = a.pct + "%"; bar.append(i);
    const pct = el("span", "pct", a.pct + "%");
    row.append(nm, meta, bar, pct);
    box.append(row);
  });
}

async function refreshAvailability() {
  try {
    const d = await post("/api/availability", cfg());
    renderAvailability(d.players);
  } catch { /* stale odds beat a broken panel; leave the last good render */ }
}

// ── season simulation ──
$("#simgo").addEventListener("click", async () => {
  const btn = $("#simgo");
  btn.disabled = true; $("#simmsg").textContent = "Playing seasons…";
  try {
    const d = await post("/api/simulate", cfg());
    $("#winnum").textContent = d.meanWins;
    $("#winrange").textContent = COPY["results-range"]
      .replace("{low}", d.lowWins).replace("{high}", d.highWins)
      .replace("{weeks}", d.weeks);
    const bars = $("#winbars"); bars.replaceChildren();
    const max = Math.max(...Object.values(d.dist));
    for (let w = 0; w <= d.weeks; w++) {
      const n = d.dist[w] || 0;
      const b = el("div"); b.style.height = (100 * n / max) + "%";
      b.append(el("span", null, w));
      bars.append(b);
    }
    $("#simfacts").replaceChildren(
      el("li", null, COPY["results-points"].replace("{points}", d.meanPoints.toLocaleString())),
      el("li", null, COPY["results-injuries"].replace("{n}", d.injuredStartsPerSeason)),
      el("li", null, COPY["results-waivers"].replace("{n}", d.waiverAddsPerSeason)),
    );
    const manage = $("#managelist"); manage.replaceChildren();
    (d.tips || []).forEach(t => manage.append(el("li", null, t)));
    COPY["manage-static"].split("\n").forEach(line => {
      line = line.replace(/^- /, "").trim();
      if (line) manage.append(el("li", null, line));
    });
    $("#simout").hidden = false;
    $("#simmsg").textContent = "";
    $("#simout").scrollIntoView({ behavior: "smooth" });
  } catch (err) { $("#simmsg").textContent = err.message; }
  finally { btn.disabled = false; }
});

$("#dl").addEventListener("click", async () => {
  const r = await fetch("/api/export", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg()) });
  const blob = await r.blob();
  const a = el("a");
  a.href = URL.createObjectURL(blob);
  a.download = "draftday.csv";
  a.click();
  URL.revokeObjectURL(a.href);
});
