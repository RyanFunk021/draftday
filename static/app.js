const $ = s => document.querySelector(s);
const el = (t, cls, txt) => {
  const n = document.createElement(t);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};

const BOARD_VISIBLE_CAP = 200;

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
    roster: {}, scoring: {}, tiers: {},
  };
  document.querySelectorAll("#roster input").forEach(i => {
    s.roster[i.dataset.slot] = i.value;
  });
  document.querySelectorAll("[data-s]").forEach(i => {
    s.scoring[i.dataset.s] = i.value;
  });
  document.querySelectorAll("[data-tier]").forEach(i => {
    s.tiers[i.dataset.tier] = i.value;
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
  document.querySelectorAll("[data-tier]").forEach(i => {
    const v = s.tiers && s.tiers[i.dataset.tier];
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

// Yahoo's own defaults, so a tier a user leaves blank keeps the standard
// value rather than silently dropping out of the table (dst_points_per_game
// needs all seven tiers present, in order, to integrate correctly).
const DST_TIER_DEFAULTS = { 0: 10, 6: 7, 13: 4, 20: 1, 27: 0, 34: -1, 999: -4 };

function cfg() {
  const roster = {};
  document.querySelectorAll("#roster input").forEach(i => {
    if (+i.value > 0) roster[i.dataset.slot] = +i.value;
  });
  const scoring = {};
  document.querySelectorAll("[data-s]").forEach(i => {
    if (i.value !== "") scoring[i.dataset.s] = +i.value;
  });
  const tierInputs = document.querySelectorAll("[data-tier]");
  const anyTier = [...tierInputs].some(i => i.value !== "");
  if (anyTier) {
    scoring.dst_tiers = [...tierInputs].map(i => [
      +i.dataset.tier,
      i.value !== "" ? +i.value : DST_TIER_DEFAULTS[i.dataset.tier],
    ]);
  }
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
    $("#draftwrap").hidden = false;
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
  // A fresh build is a fresh optimal ranking — any manual reorder from
  // before is already gone (the server never reads a submitted order on
  // /api/build). Clear the leftover search box and its results too, so
  // nothing from before the rebuild is still sitting on screen.
  $("#searchbox").value = "";
  $("#searchresults").replaceChildren();
  // A live draft in progress tracks players by name, not by reference into
  // ORDER/PLAYERS, so a rebuild (e.g. adding a searched player) doesn't
  // invalidate it -- just refresh its best-available panel against the new
  // pool.
  if (DRAFT) renderDraft();
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
  const visible = ORDER.slice(0, BOARD_VISIBLE_CAP);
  visible.forEach((name, i) => {
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
  const hidden = ORDER.length - visible.length;
  if (hidden > 0) {
    board.append(el("div", "boardmore",
      `+ ${hidden} more ranked players in your pool, held back to keep this list scannable. ` +
      `Search for one by name above to pull him in, or he'll show up automatically in your ` +
      `roster preview and season sim if your list runs that deep.`));
  }
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
  });
}

function move(i, d) {
  const j = i + d;
  if (j < 0 || j >= ORDER.length) return null;
  [ORDER[i], ORDER[j]] = [ORDER[j], ORDER[i]];
  renderBoard();
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
    const cls = "slot" + (p.slot === "BENCH" ? " bench" : "") + (p.empty ? " empty" : "");
    const slot = el("div", cls);
    slot.append(el("div", "lbl", p.slot));
    const nm = el("div", "nm");
    if (p.empty) {
      nm.append(document.createTextNode("Empty"));
    } else {
      nm.append(document.createTextNode(`${p.name} (${p.pos})`),
                el("span", "pts", `${p.pts} pts${p.bye ? " · bye " + p.bye : ""}`));
    }
    slot.append(nm);
    box.append(slot);
  });
}

// Your ACTUAL roster from the live draft tracker, slotted the same greedy
// way engine.sim._slot_for fills a team (mandatory positions first, in the
// roster config's own order, then flex, then bench) -- except this reads
// your real logged picks, not a simulated draft, and shows an empty slot
// rather than guessing who'll eventually fill it.
function myLiveRoster() {
  const roster = cfg().roster;
  const mine = DRAFT.picks.filter(p => p.owner === DRAFT.slot)
    .map(p => PLAYERS[p.name]).filter(Boolean);
  const used = new Set();
  const result = [];

  Object.entries(roster).forEach(([slot, n]) => {
    if (slot.includes("/")) return;
    for (let i = 0; i < n; i++) {
      const p = mine.find(pl => !used.has(pl.name) && pl.pos === slot);
      if (p) { used.add(p.name); result.push({ ...p, slot }); }
      else result.push({ name: null, pos: slot, slot, empty: true });
    }
  });
  Object.entries(roster).forEach(([slot, n]) => {
    if (!slot.includes("/")) return;
    const parts = slot.split("/");
    for (let i = 0; i < n; i++) {
      const p = mine.find(pl => !used.has(pl.name) && parts.includes(pl.pos));
      if (p) { used.add(p.name); result.push({ ...p, slot }); }
      else result.push({ name: null, pos: slot, slot, empty: true });
    }
  });
  // Anything drafted beyond the starting slots is real bench depth --
  // show it, don't hide it just because it exceeds the configured bench
  // count (a real draft can end up deeper at one spot than planned).
  mine.filter(pl => !used.has(pl.name)).forEach(pl => result.push({ ...pl, slot: "BENCH" }));
  return result;
}

async function refreshRosterPreview() {
  const btn = $("#updateroster");
  btn.disabled = true; btn.textContent = "Updating…";
  try {
    const d = await post("/api/roster-preview", cfg());
    renderRosterPreview(d.roster);
  } catch (err) {
    alert(err.message || "Couldn't update right now.");
  } finally {
    btn.disabled = false; btn.textContent = COPY["roster-update-button"];
  }
}
$("#updateroster").addEventListener("click", refreshRosterPreview);

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

// ── live draft tracker ──
// Entirely client-side, same pattern as ORDER/PLAYERS above: the server is
// stateless, so "the draft so far" lives only in this tab, keyed by player
// name rather than array position, so it survives a rebuild (see applyBuild).
let DRAFT = null; // { teams, slot, style, rounds, totalPicks, picks: [] }

function ownerForPick(overall, teams, style) {
  // Same snake-order arithmetic as engine.sim._draft's owner calculation --
  // one implementation of "whose turn is it," not a second guess at it.
  const idx = overall - 1;
  const rnd = Math.floor(idx / teams);
  const i = idx % teams;
  const linear = (style || "snake").toLowerCase().startsWith("lin");
  return (linear || rnd % 2 === 0) ? i + 1 : teams - i;
}

$("#draftstart").addEventListener("click", () => {
  const c = cfg();
  const rounds = Object.values(c.roster).reduce((a, b) => a + b, 0) + c.bench;
  DRAFT = {
    teams: c.teams, slot: c.slot, style: c.style,
    rounds, totalPicks: c.teams * rounds,
    picks: [],
  };
  $("#draftstart").hidden = true;
  $("#draftlive").hidden = false;
  $("#rosterlivenote").hidden = false;
  $("#updateroster").hidden = true;
  renderDraft();
});

$("#draftrestart").addEventListener("click", () => {
  DRAFT = null;
  $("#draftlive").hidden = true;
  $("#draftstart").hidden = false;
  $("#rosterlivenote").hidden = true;
  $("#updateroster").hidden = false;
});

function draftedNames() {
  return new Set(DRAFT.picks.map(p => p.name));
}

// ── dynamic queue: value, adjusted for roster need and positional runs ──
// Static VORP order is "best player overall," not "best pick for THIS
// roster at THIS moment" -- a run on a position should pull its remaining
// good players up (they're about to disappear), and an unfilled starting
// slot should outrank a marginally-better luxury pick. Both are heuristics
// layered ON TOP of the server's VORP order, not a replacement for it.
const RUN_WINDOW = 8;        // how many recent picks count as "the room right now"
const RUN_SHARE_MIN = 0.4;   // fraction of those at one position before it's a "run"
const RUN_MIN_SAMPLE = 4;    // don't call it a run off 1-2 picks -- that's just noise
const QUEUE_SIZE = 12;

function myRosterCounts() {
  const counts = {};
  DRAFT.picks.filter(p => p.owner === DRAFT.slot).forEach(p => {
    const pos = PLAYERS[p.name] && PLAYERS[p.name].pos;
    if (pos) counts[pos] = (counts[pos] || 0) + 1;
  });
  return counts;
}

function recentPositionShare() {
  const recent = DRAFT.picks.slice(-RUN_WINDOW);
  if (recent.length < RUN_MIN_SAMPLE) return {};
  const counts = {};
  recent.forEach(p => {
    const pos = PLAYERS[p.name] && PLAYERS[p.name].pos;
    if (pos) counts[pos] = (counts[pos] || 0) + 1;
  });
  const share = {};
  Object.keys(counts).forEach(pos => { share[pos] = counts[pos] / recent.length; });
  return share;
}

function computeQueue() {
  const gone = draftedNames();
  const have = myRosterCounts();
  const runShare = recentPositionShare();

  // Fold flex slots ("WR/RB/TE") evenly across their eligible positions --
  // good enough for "do I still need at least one starter here," which is
  // the only thing this weighs; engine.vorp does the precise version
  // server-side for the base ranking itself.
  const need = {};
  Object.entries(cfg().roster).forEach(([slot, n]) => {
    const parts = slot.split("/");
    parts.forEach(pos => { need[pos] = (need[pos] || 0) + n / parts.length; });
  });

  const scored = ORDER.filter(n => !gone.has(n)).map(name => {
    const p = PLAYERS[name];
    const base = p.vorp ?? p.pts ?? 0;

    const openSlots = Math.max(0, (need[p.pos] || 0) - (have[p.pos] || 0));
    const needBoost = openSlots > 0 ? 30 * Math.min(openSlots, 2) : 0;

    const share = runShare[p.pos] || 0;
    const runBoost = share >= RUN_SHARE_MIN ? share * 55 : 0;

    const notes = [];
    if (openSlots > 0) {
      notes.push(`Fills your open ${p.pos} slot${openSlots >= 2 ? "s" : ""}`);
    }
    if (runBoost > 0) {
      const n = Math.min(RUN_WINDOW, DRAFT.picks.length);
      notes.push(`${Math.round(share * 100)}% of the last ${n} picks were `
        + `${p.pos} — depth is thinning fast`);
    }
    if (!notes.length) {
      notes.push(`Best value on the board (+${p.vorp ?? 0} over replacement)`);
    }

    return { name, p, score: base + needBoost + runBoost, notes };
  });

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, QUEUE_SIZE);
}

// ── comparison chart: floor / blended avg / ceiling, up to 4 players ──
// Colors are the dataviz skill's validated dark-surface categorical slots
// 1-4 (blue/orange/aqua/yellow), confirmed CVD-safe against this app's
// background via scripts/validate_palette.js before use.
const COMPARE_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500"];
const COMPARE_MAX = 4;
let COMPARE = [];

function toggleCompare(name) {
  const i = COMPARE.indexOf(name);
  if (i >= 0) COMPARE.splice(i, 1);
  else if (COMPARE.length < COMPARE_MAX) COMPARE.push(name);
  renderCompareChart();
}

function renderCompareChart() {
  const box = $("#draftchart");
  if (!box) return;
  box.replaceChildren();
  const gone = DRAFT ? draftedNames() : new Set();
  COMPARE = COMPARE.filter(n => !gone.has(n));

  const rows = COMPARE.map(n => PLAYERS[n])
    .filter(p => p && p.floor != null && p.weekly_avg != null && p.ceiling != null);
  if (!rows.length) {
    box.append(el("p", "hint", COPY["draft-compare-empty"]));
    return;
  }

  const NS = "http://www.w3.org/2000/svg";
  const W = 520, H = 220, M = { top: 18, right: 18, bottom: 26, left: 10 };
  const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;
  const cats = ["Floor", "Avg week", "Ceiling"];
  const step = plotW / (cats.length - 1);
  const xFor = i => M.left + step * i;

  let lo = Math.min(...rows.map(p => p.floor));
  let hi = Math.max(...rows.map(p => p.ceiling));
  const pad = Math.max(2, (hi - lo) * 0.15);
  lo = Math.max(0, lo - pad); hi += pad;
  const yFor = v => M.top + plotH - ((v - lo) / (hi - lo || 1)) * plotH;

  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "comparechart");
  svg.setAttribute("role", "img");

  cats.forEach((c, i) => {
    const x = xFor(i);
    const grid = document.createElementNS(NS, "line");
    grid.setAttribute("x1", x); grid.setAttribute("x2", x);
    grid.setAttribute("y1", M.top); grid.setAttribute("y2", M.top + plotH);
    grid.setAttribute("class", "chartgrid");
    svg.append(grid);
    const label = document.createElementNS(NS, "text");
    label.setAttribute("x", x); label.setAttribute("y", H - 6);
    label.setAttribute("text-anchor", i === 0 ? "start" : i === cats.length - 1 ? "end" : "middle");
    label.setAttribute("class", "charttick");
    label.textContent = c;
    svg.append(label);
  });

  const endLabels = [];
  rows.forEach((p, si) => {
    const color = COMPARE_COLORS[si % COMPARE_COLORS.length];
    const vals = [p.floor, p.weekly_avg, p.ceiling];
    const pts = vals.map((v, i) => [xFor(i), yFor(v)]);

    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", `M${pts.map(pt => pt.join(",")).join(" L")}`);
    path.setAttribute("class", "chartline");
    path.setAttribute("stroke", color);
    svg.append(path);

    pts.forEach(([x, y]) => {
      const dot = document.createElementNS(NS, "circle");
      dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.setAttribute("r", 4.5);
      dot.setAttribute("fill", color);
      dot.setAttribute("class", "chartdot");
      svg.append(dot);
    });

    endLabels.push({ y: pts[2][1], name: p.name });
  });

  // Direct end-labels at the ceiling column -- mandatory once >=4 series
  // share a chart -- nudged apart so close values don't collide.
  endLabels.sort((a, b) => a.y - b.y);
  for (let i = 1; i < endLabels.length; i++) {
    if (endLabels[i].y - endLabels[i - 1].y < 13) endLabels[i].y = endLabels[i - 1].y + 13;
  }
  endLabels.forEach(l => {
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", xFor(2) - 6); t.setAttribute("y", l.y - 8);
    t.setAttribute("text-anchor", "end");
    t.setAttribute("class", "chartendlabel");
    t.textContent = l.name.split(" ").slice(-1)[0];
    svg.append(t);
  });

  const tip = el("div", "charttooltip"); tip.hidden = true;
  cats.forEach((c, i) => {
    const hit = document.createElementNS(NS, "rect");
    hit.setAttribute("x", xFor(i) - step / 2); hit.setAttribute("y", M.top);
    hit.setAttribute("width", step); hit.setAttribute("height", plotH);
    hit.setAttribute("class", "charthit");
    hit.setAttribute("tabindex", "0");
    const show = () => {
      tip.replaceChildren(el("b", null, c));
      rows.forEach((p, si) => {
        const row = el("div", "charttiprow");
        const key = el("span", "chartkey");
        key.style.background = COMPARE_COLORS[si % COMPARE_COLORS.length];
        row.append(key, el("b", null, String([p.floor, p.weekly_avg, p.ceiling][i])),
                  document.createTextNode(" " + p.name));
        tip.append(row);
      });
      tip.hidden = false;
    };
    const hide = () => { tip.hidden = true; };
    hit.addEventListener("pointerenter", show);
    hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("blur", hide);
    svg.append(hit);
  });

  const legend = el("div", "chartlegend");
  rows.forEach((p, si) => {
    const row = el("div", "chartlegendrow");
    const key = el("span", "chartkey");
    key.style.background = COMPARE_COLORS[si % COMPARE_COLORS.length];
    row.append(key, el("b", null, p.name),
              document.createTextNode(` — Floor ${p.floor} · Avg ${p.weekly_avg} · Ceiling ${p.ceiling}`));
    legend.append(row);
  });

  box.append(svg, tip, legend);
}

function currentOverall() {
  return DRAFT.picks.length + 1;
}

function submitPick(name) {
  if (!DRAFT || currentOverall() > DRAFT.totalPicks) return;
  const overall = currentOverall();
  const round = Math.floor((overall - 1) / DRAFT.teams) + 1;
  const owner = ownerForPick(overall, DRAFT.teams, DRAFT.style);
  DRAFT.picks.push({ overall, round, owner, name });
  renderDraft();
}

$("#draftundo").addEventListener("click", () => {
  if (!DRAFT || !DRAFT.picks.length) return;
  DRAFT.picks.pop();
  renderDraft();
});

$("#draftsearchbox").addEventListener("input", () => {
  if (!DRAFT) return;
  const q = $("#draftsearchbox").value.trim().toLowerCase();
  const box = $("#draftsearchresults");
  if (q.length < 2) { box.replaceChildren(); return; }
  const gone = draftedNames();
  const hits = ORDER.filter(n => !gone.has(n) && n.toLowerCase().includes(q))
                    .slice(0, 8);
  if (!hits.length) {
    box.replaceChildren(el("p", "searchmsg", "No match in your list."));
    return;
  }
  box.replaceChildren(...hits.map(n => {
    const p = PLAYERS[n];
    const row = el("div", "searchhit");
    row.append(el("span", "pos", p.pos), el("span", "nm", p.name),
              el("span", "team", p.team));
    const btn = el("button", null, "Log pick");
    btn.type = "button";
    btn.onclick = () => submitPick(n);
    row.append(btn);
    return row;
  }));
});

function renderDraft() {
  if (!DRAFT) return;
  const overall = currentOverall();
  const done = overall > DRAFT.totalPicks;

  const status = $("#draftstatus");
  if (done) {
    status.textContent = COPY["draft-complete"];
    status.className = "";
  } else {
    const round = Math.floor((overall - 1) / DRAFT.teams) + 1;
    const owner = ownerForPick(overall, DRAFT.teams, DRAFT.style);
    const mine = owner === DRAFT.slot;
    status.textContent = `Pick ${overall} of ${DRAFT.totalPicks} · Round ${round} · `
      + (mine ? "Your pick" : `Team ${owner} on the clock`);
    status.className = mine ? "draftturn you" : "draftturn";
  }

  $("#draftundo").disabled = DRAFT.picks.length === 0;
  $("#draftsearchbox").value = "";
  $("#draftsearchresults").replaceChildren();

  const best = $("#draftbest");
  best.replaceChildren();
  if (!done) {
    best.append(el("h3", null, COPY["draft-best-heading"]));
    best.append(el("p", "hint", COPY["draft-queue-hint"]));
    const list = el("ol", "tiplist draftbestlist");
    computeQueue().forEach(({ name, p, notes }) => {
      const li = el("li");
      const top = el("div", "draftqueuetop");
      top.append(el("b", null, `${p.name} (${p.pos})`),
                document.createTextNode(` — ${p.pts} pts`));
      const btn = el("button", null, "Log pick");
      btn.type = "button";
      btn.onclick = () => submitPick(name);
      top.append(document.createTextNode(" "), btn);

      const chk = el("label", "comparechk");
      const cb = el("input"); cb.type = "checkbox";
      cb.checked = COMPARE.includes(name);
      cb.disabled = !cb.checked && COMPARE.length >= COMPARE_MAX;
      cb.onchange = () => toggleCompare(name);
      chk.append(cb, document.createTextNode(" Compare"));
      top.append(chk);

      li.append(top);
      notes.forEach(n => li.append(el("div", "queuenote", n)));
      list.append(li);
    });
    best.append(list);
  }
  renderCompareChart();

  const log = $("#draftlog");
  if (!DRAFT.picks.length) {
    log.replaceChildren(el("p", "hint", COPY["draft-log-empty"]));
  } else {
    log.replaceChildren(...[...DRAFT.picks].reverse().map(pk => {
      const row = el("div", "draftlogrow" + (pk.owner === DRAFT.slot ? " you" : ""));
      const who = pk.owner === DRAFT.slot ? "You" : `Team ${pk.owner}`;
      row.textContent = `#${pk.overall} (Rd ${pk.round}) — ${who}: ${pk.name}`;
      return row;
    }));
  }

  renderRosterPreview(myLiveRoster());
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
