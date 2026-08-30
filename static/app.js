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

// ── league settings persist across refresh (localStorage, no accounts) ──
const SETTINGS_KEY = "draftday-settings-v1";

function saveSettings() {
  const s = {
    teams: $("[name=teams]").value, slot: $("[name=slot]").value,
    bench: $("[name=bench]").value, style: $("[name=style]").value,
    preset: $("#preset").value, lastw: $("#lastw").value,
    roster: {}, scoring: {},
  };
  document.querySelectorAll("#roster input").forEach(i => {
    s.roster[i.dataset.slot] = i.value;
  });
  document.querySelectorAll("[data-s]").forEach(i => {
    s.scoring[i.dataset.s] = i.value;
  });
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); }
  catch { /* private browsing or a full quota is not worth surfacing */ }
}

function loadSettings() {
  let s;
  try { s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null"); }
  catch { return; }
  if (!s) return;

  if (s.teams) $("[name=teams]").value = s.teams;
  if (s.slot) $("[name=slot]").value = s.slot;
  if (s.bench) $("[name=bench]").value = s.bench;
  if (s.style) $("[name=style]").value = s.style;
  if (s.preset) $("#preset").value = s.preset;
  if (s.lastw) {
    $("#lastw").value = s.lastw;
    $("#lastw").dispatchEvent(new Event("input"));
  }
  document.querySelectorAll("#roster input").forEach(i => {
    if (s.roster && s.roster[i.dataset.slot] != null) i.value = s.roster[i.dataset.slot];
  });
  let anyScoring = false;
  document.querySelectorAll("[data-s]").forEach(i => {
    const v = s.scoring && s.scoring[i.dataset.s];
    if (v != null && v !== "") { i.value = v; anyScoring = true; }
  });
  // Open the custom-scoring panel automatically if a saved value lives in
  // it — otherwise the numbers are back but hidden, which looks like they
  // did not actually restore.
  if (anyScoring) $("#customscoring").open = true;
}
loadSettings();

// Persist on every change rather than only at build time, so a refresh
// mid-edit does not throw away typing that never made it into a request.
$("#cfg").addEventListener("input", saveSettings);
$("#preset").addEventListener("change", saveSettings);

let ORDER = [];        // current list, user-editable
let PLAYERS = {};      // name -> player
let EXTRA_PLAYERS = []; // raw rows for players searched-and-added this session,
                        // sent on every request so they score and rank the
                        // same way as everyone else even if the shared-pool
                        // write (which /api/add-player already attempted)
                        // hasn't landed for some reason
let reorderTimer = null;

function cfg() {
  const roster = {};
  document.querySelectorAll("#roster input").forEach(i => {
    if (+i.value > 0) roster[i.dataset.slot] = +i.value;
  });
  const scoring = {};
  document.querySelectorAll("[data-s]").forEach(i => {
    if (i.value !== "") scoring[i.dataset.s] = +i.value;
  });
  return {
    teams: +$("[name=teams]").value, slot: +$("[name=slot]").value,
    bench: +$("[name=bench]").value, style: $("[name=style]").value,
    preset: $("#preset").value, roster, scoring,
    last_weight: +$("#lastw").value,
    order: ORDER,
    extra_players: EXTRA_PLAYERS,
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
    applyBuild(d);
    $("#listwrap").hidden = false;
    $("#rosterwrap").hidden = false;
    $("#simwrap").hidden = false;
    $("#simout").hidden = true;
    $("#msg").textContent = "";
    $("#listwrap").scrollIntoView({ behavior: "smooth" });
  } catch (err) { $("#msg").textContent = err.message; }
  finally { btn.disabled = false; }
});

function applyBuild(d) {
  ORDER = d.players.map(p => p.name);
  PLAYERS = {};
  d.players.forEach(p => { PLAYERS[p.name] = p; });
  renderBoard();
  renderTips(d.tips);
  renderRosterPreview(d.rosterPreview);
}

// ── search and add a player not on the pool ──
let searchTimer = null;
$("#searchbox").addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = $("#searchbox").value.trim();
  const box = $("#searchresults");
  if (q.length < 2) { box.replaceChildren(); return; }
  searchTimer = setTimeout(() => runSearch(q), 350);
});

async function runSearch(q) {
  const box = $("#searchresults");
  box.replaceChildren(el("p", "searchmsg", "Searching…"));
  try {
    const r = await post("/api/search-players", { ...cfg(), query: q });
    if (!r.players.length) {
      box.replaceChildren(el("p", "searchmsg", "No match on an NFL roster."));
      return;
    }
    box.replaceChildren(...r.players.map(p => {
      const row = el("div", "searchhit");
      row.append(el("span", "pos", p.pos), el("span", "nm", p.name),
                el("span", "team", p.team));
      const btn = el("button", null, "Add");
      btn.type = "button";
      btn.onclick = () => addPlayer(p.name, btn);
      row.append(btn);
      return row;
    }));
  } catch (err) {
    box.replaceChildren(el("p", "searchmsg err", err.message));
  }
}

async function addPlayer(name, btn) {
  btn.disabled = true; btn.textContent = "Adding…";
  try {
    const r = await post("/api/add-player", { name });
    // Only needed client-side if the shared-pool write didn't happen (a
    // network hiccup, or some future gate) — otherwise the very next
    // /api/build call already finds him in data/projections.csv. Sending
    // it either way costs nothing and guarantees this session sees him
    // immediately regardless.
    EXTRA_PLAYERS.push(r.row);
    const d = await post("/api/build", cfg());
    applyBuild(d);
    $("#searchbox").value = "";
    $("#searchresults").replaceChildren(
      el("p", "searchmsg", `Added ${name}. ${r.addedToSharedPool
        ? "Confirmed against ESPN's roster and saved for future visitors too."
        : "He's in your list now, but couldn't be confirmed for the shared list."}`));
  } catch (err) {
    btn.disabled = false; btn.textContent = "Add";
    $("#searchresults").append(el("p", "searchmsg err", err.message));
  }
}

// ── 20 to watch, three real sourced sections ──
function renderTips(tips) {
  const box = $("#tips");
  box.replaceChildren();
  const sections = [
    ["top", "tips-top-heading", "tips-top-hint"],
    ["value", "tips-value-heading", "tips-value-hint"],
    ["deep", "tips-deep-heading", "tips-deep-hint"],
  ];
  sections.forEach(([key, hKey, hintKey]) => {
    const entries = (tips && tips[key]) || [];
    const sec = el("div", "tipsection");
    sec.append(el("h3", null, COPY[hKey]));
    sec.append(el("p", "hint", COPY[hintKey]));
    if (!entries.length) {
      sec.append(el("p", "tipempty", "Nothing sourced in this range for this league."));
    } else {
      const list = el("ol", "tiplist");
      entries.forEach(t => {
        const li = el("li");
        li.append(el("b", null, `${t.name} (${t.pos})`));
        if (t.note) {
          li.append(document.createTextNode(" " + t.note));
          const src = el("span", "src");
          if (t.url) {
            const a = el("a"); a.href = t.url; a.target = "_blank";
            a.rel = "noopener"; a.textContent = t.source;
            src.append(document.createTextNode("Source: "), a);
          } else {
            src.textContent = "Source: " + t.source;
          }
          li.append(src);
        }
        list.append(li);
      });
      sec.append(list);
    }
    box.append(sec);
  });
}

// ── list / detail / inline round+odds ──
function renderBoard() {
  const starters = starterCount();
  const board = $("#board");
  board.replaceChildren();
  ORDER.forEach((name, i) => {
    const p = PLAYERS[name];
    const row = el("div", "row" + (i < starters ? " starter" : ""));
    row.draggable = true;
    row.dataset.name = name;
    row.append(el("span", "drag", "⠿"),
               el("span", "rk", i + 1),
               el("span", "pos", p.pos),
               el("span", "nm", `${p.name}${p.bye ? " · bye " + p.bye : ""}`),
               el("span", "pts", p.pts));
    const mv = el("span", "mv");
    const up = el("button", null, "▲"), dn = el("button", null, "▼");
    up.type = "button"; dn.type = "button";
    up.title = "Move up and check his round and odds";
    up.onclick = ev => { ev.stopPropagation(); bumpUp(i); };
    dn.onclick = ev => { ev.stopPropagation(); move(i, +1); };
    mv.append(up, dn); row.append(mv);
    row.onclick = () => toggleDetail(row, p);
    wireDrag(row);
    board.append(row);
  });
}

function starterCount() {
  return Object.values(cfg().roster).reduce((a, b) => a + b, 0);
}

// ── drag to reorder ──
// Native HTML5 drag-and-drop rather than a library: one file, no extra
// dependency, and a vertical reorder of plain rows is exactly what it is
// built for. The arrows still work too — drag is faster for a big jump,
// arrows are more precise for "one spot."
let dragName = null;

function wireDrag(row) {
  row.addEventListener("dragstart", e => {
    dragName = row.dataset.name;
    row.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    // Detail panels and odds rows are separate DOM siblings, not part of
    // the row being dragged — close them so a stale one is not left
    // pointing at whatever row happens to end up in that spot.
    document.querySelectorAll(".detail, .rowodds").forEach(n => n.remove());
  });
  row.addEventListener("dragend", () => {
    row.classList.remove("dragging");
    document.querySelectorAll(".row.dragover").forEach(n => n.classList.remove("dragover"));
    dragName = null;
  });
  row.addEventListener("dragover", e => {
    e.preventDefault();
    if (row.dataset.name === dragName) return;
    e.dataTransfer.dropEffect = "move";
    row.classList.add("dragover");
  });
  row.addEventListener("dragleave", () => row.classList.remove("dragover"));
  row.addEventListener("drop", e => {
    e.preventDefault();
    row.classList.remove("dragover");
    if (!dragName || row.dataset.name === dragName) return;
    const from = ORDER.indexOf(dragName);
    const to = ORDER.indexOf(row.dataset.name);
    if (from === -1 || to === -1) return;
    ORDER.splice(to, 0, ORDER.splice(from, 1)[0]);
    renderBoard();
    scheduleReorderRefresh();
  });
}

function move(i, d) {
  const j = i + d;
  if (j < 0 || j >= ORDER.length) return null;
  [ORDER[i], ORDER[j]] = [ORDER[j], ORDER[i]];
  renderBoard();
  scheduleReorderRefresh();
  return j;
}

// The up arrow does two things: moves the player, and immediately shows
// where he'd actually go (round) and the odds he lasts there — the point
// isn't just reordering, it's seeing whether bumping him up was worth it.
async function bumpUp(i) {
  const newIndex = move(i, -1);
  if (newIndex == null) return;
  const name = ORDER[newIndex];
  const row = [...document.querySelectorAll(".row")][newIndex];
  if (!row) return;

  document.querySelectorAll(".rowodds").forEach(r => r.remove());
  const odds = el("div", "rowodds loading", "Checking round and odds…");
  row.after(odds);

  try {
    const r = await post("/api/availability", cfg());
    const hit = r.players.find(a => a.name === name);
    odds.replaceWith(oddsRow(hit, +$("[name=teams]").value));
  } catch {
    odds.textContent = "Couldn't check right now.";
    odds.classList.remove("loading");
  }
}

function oddsRow(hit, teams) {
  const row = el("div", "rowodds");
  if (!hit) {
    row.textContent = "Outside your top targets, likely a very late or very safe pick.";
    return row;
  }
  const round = Math.floor((hit.atPick - 1) / teams) + 1;
  const rnd = el("span", "rnd");
  rnd.innerHTML = `Round <b>${round}</b> (pick ${hit.atPick}) &middot; usual ADP ${hit.adp}`;
  const bar = el("span", "oddsbar " + bandFor(hit.pct));
  const i = el("i"); i.style.width = hit.pct + "%"; bar.append(i);
  const pct = el("span", "pct", hit.pct + "% lasts that long");
  row.append(rnd, bar, pct);
  return row;
}

function bandFor(pct) {
  if (pct >= 60) return "";
  if (pct >= 25) return "risky";
  return "long";
}

// Reordering changes both the likely roster and (if a detail panel with
// odds is open) the availability odds. Debounced so a burst of arrow
// clicks does not fire a request per click.
function scheduleReorderRefresh() {
  clearTimeout(reorderTimer);
  reorderTimer = setTimeout(refreshRosterPreview, 500);
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
  stat("Edge over replacement", p.vorp != null ? p.vorp : "n/a");
  stat(`${p.pos} rank`, p.posRank || "n/a");
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

  return d;
}

// ── roster preview, including bench ──
function renderRosterPreview(roster) {
  const box = $("#rosterpreview");
  box.replaceChildren();
  if (!roster || !roster.length) {
    box.append(el("p", "hint", "Not enough players in the pool to fill this roster."));
    return;
  }
  roster.forEach(p => {
    const slot = el("div", "slot" + (p.slot === "BENCH" ? " bench" : ""));
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

// ── season simulation, streamed and paced to feel like the real work it is ──
const SIM_SECONDS = 5;

$("#simgo").addEventListener("click", async () => {
  const btn = $("#simgo");
  btn.disabled = true;
  $("#simmsg").textContent = "";
  $("#simout").hidden = true;
  const prog = $("#simprogress"); prog.hidden = false;
  const text = $("#simprogresstext"); const fill = $("#progfill");
  fill.style.width = "0%";

  const started = performance.now();
  let last = null;

  try {
    const res = await fetch("/api/simulate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg()),
    });
    if (!res.ok || !res.body) throw new Error("Something went wrong.");

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buf += dec.decode(chunk.value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const d = JSON.parse(line);
        last = d;
        text.textContent = COPY["sim-progress"]
          .replace("{done}", d.done).replace("{total}", d.total);
        // Pace to SIM_SECONDS total, based on progress rather than frame
        // count, so it lands on time regardless of how fast the server is —
        // 200 real simulations finish in well under a second on their own,
        // which reads as broken (not fast) for a button that says
        // "simulating your season."
        const target = started + SIM_SECONDS * 1000 * (d.done / d.total);
        fill.style.width = (100 * d.done / d.total) + "%";
        const wait = target - performance.now();
        if (wait > 0) await new Promise(r => setTimeout(r, wait));
        else fill.style.width = (100 * d.done / d.total) + "%";
      }
    }
    if (!last || last.error) throw new Error(last?.error || "Simulation failed.");
    finishSim(last);
  } catch (err) {
    $("#simmsg").textContent = err.message;
  } finally {
    prog.hidden = true;
    btn.disabled = false;
  }
});

function finishSim(d) {
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
  $("#simout").scrollIntoView({ behavior: "smooth" });
}

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
