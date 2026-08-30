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

$("#lastw").addEventListener("input", () => { $("#lastwv").textContent = $("#lastw").value; });

let ORDER = [];        // current list, user-editable
let PLAYERS = {};      // name -> player

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
    $("#tiplist").replaceChildren(...d.tips.map(t => {
      const li = el("li");
      li.append(el("b", null, `${t.name} (${t.pos})`),
                document.createTextNode(" " + t.headline));
      return li;
    }));
    $("#listwrap").hidden = false;
    $("#simwrap").hidden = false;
    $("#simout").hidden = true;
    $("#msg").textContent = "";
    $("#listwrap").scrollIntoView({ behavior: "smooth" });
  } catch (err) { $("#msg").textContent = err.message; }
  finally { btn.disabled = false; }
});

function renderBoard() {
  const board = $("#board");
  board.replaceChildren();
  ORDER.forEach((name, i) => {
    const p = PLAYERS[name];
    const row = el("div", "row");
    row.append(el("span", "rk", i + 1),
               el("span", "pos", p.pos),
               el("span", "nm", `${p.name}${p.bye ? " · bye " + p.bye : ""}`),
               el("span", "pts", p.pts));
    const mv = el("span", "mv");
    const up = el("button", null, "▲"), dn = el("button", null, "▼");
    up.onclick = ev => { ev.stopPropagation(); move(i, -1); };
    dn.onclick = ev => { ev.stopPropagation(); move(i, +1); };
    mv.append(up, dn); row.append(mv);
    row.onclick = () => toggleTidbit(row, p);
    board.append(row);
  });
}

function move(i, d) {
  const j = i + d;
  if (j < 0 || j >= ORDER.length) return;
  [ORDER[i], ORDER[j]] = [ORDER[j], ORDER[i]];
  renderBoard();
}

function toggleTidbit(row, p) {
  if (row.nextSibling?.classList?.contains("tidbit")) {
    row.nextSibling.remove(); return;
  }
  document.querySelectorAll(".tidbit").forEach(t => t.remove());
  const t = el("div", "tidbit",
    p.tidbit || "No recent ESPN coverage.");
  row.after(t);
}

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
