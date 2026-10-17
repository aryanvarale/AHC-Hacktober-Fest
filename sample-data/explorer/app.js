const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const KINDS = {
  wellhead: "Wellhead",
  pump_jack: "Pump jack",
  rod_string: "Rod string",
  downhole_pump: "Downhole pump",
  flowline: "Flowline",
  separator: "Separator",
};
const NOTE_LABELS = {
  operator_note: "Operator note",
  operator_feedback: "Operator feedback",
  correction: "Correction",
};
const EVENT_LABELS = {
  spill_report_filed: "Spill report filed",
  notice_of_violation: "Notice of violation",
  site_inspection: "Site inspection",
  nov_response_filed: "Violation response filed",
};
const AREA_COLORS = {
  "SW-23": "#b8431f",
  "SW-24": "#c4843c",
  "NE-07": "#234e70",
  "NE-08": "#2f6f62",
};

const state = {
  catalog: null,
  q: "",
  type: "all",
  status: "all",
  area: "all",
  only: "all",
  sort: "id",
  wellId: null,
  wellToken: 0,
  wellCache: new Map(),
  inspMode: "all",
  inspPage: 1,
  inspQ: "",
  maintMode: "all",
  maintPage: 1,
};

const view = document.getElementById("view");
const nav = document.getElementById("nav");
const statsEl = document.getElementById("stats");
const searchInput = document.getElementById("q");

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function mark(value, q) {
  const text = String(value ?? "");
  const needle = q.trim();
  if (!needle) return esc(text);
  const at = text.toLowerCase().indexOf(needle.toLowerCase());
  if (at < 0) return esc(text);
  return `${esc(text.slice(0, at))}<mark>${esc(text.slice(at, at + needle.length))}</mark>${esc(text.slice(at + needle.length))}`;
}

function fmtDate(iso, withTime) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return esc(iso);
  const date = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  if (!withTime || !String(iso).includes("T")) return date;
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${date}, ${hh}:${mm} UTC`;
}

function fmtNum(value, digits = 0) {
  if (value == null || value === "") return "—";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function fmtMoney(value) {
  if (value == null) return "—";
  return `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function fmtDelta(value) {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${Number(value).toFixed(1)} °C`;
}

function kindLabel(kind) {
  return KINDS[kind] || kind || "—";
}

function parseHash() {
  const parts = (location.hash || "#/wells").replace(/^#/, "").split("/").filter(Boolean);
  const head = parts[0] || "wells";
  if (head === "well" && parts[1]) return { view: "well", id: parts[1], tab: parts[2] || "overview", focus: parts[3] || null };
  if (head === "person" && parts[1]) return { view: "person", id: parts[1] };
  if (head === "doc" && parts[1]) return { view: "doc", id: parts[1] };
  if (head === "people") return { view: "people" };
  if (head === "docs") return { view: "docs" };
  if (head === "map") return { view: "map" };
  return { view: "wells" };
}

function filteredWells() {
  const q = state.q.trim().toLowerCase();
  const wells = state.catalog.wells.filter((well) => {
    if (state.type !== "all" && well.well_type !== state.type) return false;
    if (state.status !== "all" && well.status !== state.status) return false;
    if (state.area !== "all" && well.area_id !== state.area) return false;
    if (state.only === "separator" && !well.has_separator) return false;
    if (state.only === "violations" && !well.open_regulatory) return false;
    if (state.only === "spills" && !well.spill_count) return false;
    if (!q) return true;
    const hay = [
      well.asset_id,
      well.name,
      well.well_type,
      well.area_id,
      well.area_name,
      well.in_charge,
      well.status,
      ...well.equipment.flatMap((item) => [item.kind, item.model]),
    ].join(" ").toLowerCase();
    return hay.includes(q);
  });
  const byId = (a, b) => a.asset_id.localeCompare(b.asset_id);
  const sorters = {
    id: byId,
    spills: (a, b) => b.spill_count - a.spill_count || byId(a, b),
    violations: (a, b) => b.open_regulatory - a.open_regulatory || byId(a, b),
    corrective: (a, b) => b.corrective_count - a.corrective_count || byId(a, b),
    inspected: (a, b) => String(b.last_inspection_at || "").localeCompare(String(a.last_inspection_at || "")),
  };
  wells.sort(sorters[state.sort] || byId);
  return wells;
}

function collect(list, pred, limit) {
  const hits = [];
  let total = 0;
  for (const row of list) {
    if (!pred(row)) continue;
    total += 1;
    if (hits.length < limit) hits.push(row);
  }
  return { hits, total };
}

function recordHits() {
  const q = state.q.trim().toLowerCase();
  if (q.length < 2) return null;
  const has = (...fields) => fields.some((field) => String(field || "").toLowerCase().includes(q));
  const maintenance = collect(state.catalog.maintenance, (row) => has(row.issue, row.action, row.equipment, row.technician, row.id, row.type), 8);
  const context = collect(state.catalog.context, (row) => has(row.text, row.who, row.type, row.id), 6);
  const regulatory = collect(state.catalog.regulatory, (row) => has(row.detail, row.type, row.protocol, row.id, row.status), 6);
  const users = collect(state.catalog.users, (row) => has(row.name, row.role_label, row.id, row.team_name), 6);
  const docs = collect(state.catalog.docs, (row) => has(row.title, row.kind, row.search_text), 4);
  const total = maintenance.total + context.total + regulatory.total + users.total + docs.total;
  if (!total) return null;
  return { maintenance, context, regulatory, users, docs };
}

function paintNav(route) {
  const items = [
    ["wells", "Wells", "#/wells"],
    ["map", "Map", "#/map"],
    ["people", "People", "#/people"],
    ["docs", "Reference", "#/docs"],
  ];
  const current = route.view === "person" ? "people" : route.view === "doc" ? "docs" : route.view === "well" ? "wells" : route.view;
  nav.innerHTML = items.map(([id, label, href]) => `<a href="${href}" ${current === id ? 'aria-current="page"' : ""}>${label}</a>`).join("");
}

function paintStats() {
  const s = state.catalog.stats;
  const from = fmtDate(s.inspection_from);
  const to = fmtDate(s.inspection_to);
  document.getElementById("tagline").textContent = `${fmtNum(s.wells)} rod-lift wells · inspections ${from} – ${to}`;
  const chips = [
    ["wells", fmtNum(s.wells), "Wells", () => setFilter({ type: "all", status: "all", area: "all", only: "all" })],
    ["t200", fmtNum(s.t200), "T200 wells", () => setFilter({ type: "T200" })],
    ["t350", fmtNum(s.t350), "T350 wells", () => setFilter({ type: "T350" })],
    ["down", fmtNum(s.down), "Recorded down", () => setFilter({ status: "down" })],
    ["spills", fmtNum(s.spills), "Spill readings", () => setFilter({ only: "spills" })],
    ["open", fmtNum(s.open_regulatory), "Open agency items", () => setFilter({ only: "violations" })],
  ];
  statsEl.hidden = false;
  statsEl.innerHTML = chips.map(([key, num, label]) => `<button class="stat" type="button" data-stat="${key}"><b>${num}</b><span>${label}</span></button>`).join("");
  statsEl.querySelectorAll("[data-stat]").forEach((button, index) => {
    button.addEventListener("click", () => {
      chips[index][3]();
      if (parseHash().view !== "wells" && parseHash().view !== "map") location.hash = "#/wells";
      else render();
    });
  });
}

function setFilter(patch) {
  Object.assign(state, { type: "all", status: "all", area: "all", only: "all", ...patch });
}

function filterBar() {
  const areas = state.catalog.areas.map((area) => `<option value="${esc(area.id)}" ${state.area === area.id ? "selected" : ""}>${esc(area.name)}</option>`).join("");
  const only = (value, label) => `<button class="chip" type="button" data-only="${value}" aria-pressed="${state.only === value}">${label}</button>`;
  const dirty = state.type !== "all" || state.status !== "all" || state.area !== "all" || state.only !== "all" || state.sort !== "id" || state.q;
  return `
    <div class="filters">
      <select id="f-type" aria-label="Well class">
        <option value="all" ${state.type === "all" ? "selected" : ""}>All classes</option>
        <option value="T200" ${state.type === "T200" ? "selected" : ""}>T200</option>
        <option value="T350" ${state.type === "T350" ? "selected" : ""}>T350</option>
      </select>
      <select id="f-status" aria-label="Recorded status">
        <option value="all" ${state.status === "all" ? "selected" : ""}>Any status</option>
        <option value="active" ${state.status === "active" ? "selected" : ""}>Active</option>
        <option value="down" ${state.status === "down" ? "selected" : ""}>Down</option>
      </select>
      <select id="f-area" aria-label="Operating area">
        <option value="all" ${state.area === "all" ? "selected" : ""}>All areas</option>
        ${areas}
      </select>
      <select id="f-sort" aria-label="Sort wells">
        <option value="id" ${state.sort === "id" ? "selected" : ""}>Sort by asset id</option>
        <option value="spills" ${state.sort === "spills" ? "selected" : ""}>Most spill readings</option>
        <option value="violations" ${state.sort === "violations" ? "selected" : ""}>Open agency items</option>
        <option value="corrective" ${state.sort === "corrective" ? "selected" : ""}>Most corrective jobs</option>
        <option value="inspected" ${state.sort === "inspected" ? "selected" : ""}>Latest inspection</option>
      </select>
      ${only("separator", "Has separator")}
      ${only("spills", "Has spills")}
      ${only("violations", "Open agency item")}
      ${dirty ? '<button class="text-btn" type="button" id="clear-filters">Clear</button>' : ""}
    </div>`;
}

function bindFilterBar() {
  const bindSelect = (id, key) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("change", () => {
      state[key] = el.value;
      render();
    });
  };
  bindSelect("f-type", "type");
  bindSelect("f-status", "status");
  bindSelect("f-area", "area");
  bindSelect("f-sort", "sort");
  view.querySelectorAll("[data-only]").forEach((button) => {
    button.addEventListener("click", () => {
      state.only = state.only === button.dataset.only ? "all" : button.dataset.only;
      render();
    });
  });
  document.getElementById("clear-filters")?.addEventListener("click", () => {
    state.type = "all";
    state.status = "all";
    state.area = "all";
    state.only = "all";
    state.sort = "id";
    state.q = "";
    searchInput.value = "";
    render();
  });
}

function wellCard(well) {
  const status = well.status === "down" ? "down" : "active";
  const bits = [
    `<span class="badge ${status}">${esc(well.status)}</span>`,
    well.spill_count ? `<span class="badge spill">${fmtNum(well.spill_count)} spill readings</span>` : "",
    well.open_regulatory ? `<span class="badge open">${fmtNum(well.open_regulatory)} open</span>` : "",
    well.has_separator ? `<span class="badge">Separator</span>` : "",
  ].join("");
  return `
    <a class="card" href="#/well/${esc(well.asset_id)}">
      <div class="card-top">
        <span class="asset-id">${esc(well.asset_id)}</span>
        <span class="badge ${well.well_type.toLowerCase()}">${esc(well.well_type)}</span>
      </div>
      <h2>${mark(well.name, state.q)}</h2>
      <p>${esc(well.area_name)} · ${esc(well.in_charge || "Unassigned")}</p>
      <p>Last inspection ${fmtDate(well.last_inspection_at)}</p>
      <div class="card-foot">${bits}</div>
    </a>`;
}

function hitsHtml() {
  const hits = recordHits();
  if (!hits) return "";
  const q = state.q;
  const group = (title, bucket, inner) => {
    if (!inner) return "";
    const more = bucket.total > bucket.hits.length ? ` · ${bucket.hits.length} of ${fmtNum(bucket.total)}` : "";
    return `<section class="hit-group"><h2>${title}${more}</h2>${inner}</section>`;
  };
  const maintenance = hits.maintenance.hits.map((row) => `
    <a class="hit" href="#/well/${esc(row.asset_id)}/maintenance/${esc(row.id)}">
      <strong>${mark(row.issue, q)}</strong>
      <p>${esc(row.asset_id)} · ${esc(row.type)} · ${esc(kindLabel(row.equipment))} · ${fmtDate(row.at)} · ${fmtMoney(row.cost)}</p>
    </a>`).join("");
  const context = hits.context.hits.map((row) => `
    <a class="hit" href="#/well/${esc(row.asset_id)}/notes/${esc(row.id)}">
      <strong>${esc(NOTE_LABELS[row.type] || row.type)} · ${esc(row.who || "Unknown")}</strong>
      <p>${mark(row.text, q)}</p>
    </a>`).join("");
  const regulatory = hits.regulatory.hits.map((row) => `
    <a class="hit" href="#/well/${esc(row.asset_id)}/regulatory/${esc(row.id)}">
      <strong>${esc(EVENT_LABELS[row.type] || row.type)} · ${esc(row.status)}</strong>
      <p>${esc(row.asset_id)} · ${mark(row.detail, q)}</p>
    </a>`).join("");
  const users = hits.users.hits.map((row) => `
    <a class="hit" href="#/person/${esc(row.id)}">
      <strong>${mark(row.name, q)}</strong>
      <p>${esc(row.role_label)} · ${esc(row.team_name)}</p>
    </a>`).join("");
  const docs = hits.docs.hits.map((row) => `
    <a class="hit" href="#/doc/${esc(row.id)}">
      <strong>${mark(row.title, q)}</strong>
      <p>${esc(row.kind)}</p>
    </a>`).join("");
  return `<div class="hits">
    ${group("Maintenance", hits.maintenance, maintenance)}
    ${group("Notes", hits.context, context)}
    ${group("Regulatory", hits.regulatory, regulatory)}
    ${group("People", hits.users, users)}
    ${group("Reference", hits.docs, docs)}
  </div>`;
}

function renderWells() {
  const wells = filteredWells();
  view.innerHTML = `
    ${filterBar()}
    <p class="count-line">${fmtNum(wells.length)} ${wells.length === 1 ? "well" : "wells"}</p>
    ${hitsHtml()}
    ${wells.length ? `<div class="grid">${wells.map(wellCard).join("")}</div>` : `<p class="empty">No wells match this search. Clear a filter or try a maintenance term such as flowline, stuffing box, or violation.</p>`}
  `;
  bindFilterBar();
}

function renderMap() {
  const all = state.catalog.wells;
  const lats = all.map((well) => well.lat);
  const lons = all.map((well) => well.lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const width = 1000;
  const height = 620;
  const pad = 0.1;
  const sx = (lon) => pad * width + ((lon - minLon) / (maxLon - minLon || 1)) * (1 - 2 * pad) * width;
  const sy = (lat) => height - (pad * height + ((lat - minLat) / (maxLat - minLat || 1)) * (1 - 2 * pad) * height);
  const shown = new Set(filteredWells().map((well) => well.asset_id));
  const dots = all.map((well) => {
    const on = shown.has(well.asset_id);
    const color = AREA_COLORS[well.area_id] || "#221c16";
    return `<a href="#/well/${esc(well.asset_id)}"><circle cx="${sx(well.lon).toFixed(1)}" cy="${sy(well.lat).toFixed(1)}" r="${on ? 6 : 4}" fill="${color}" opacity="${on ? 0.92 : 0.18}"><title>${esc(well.asset_id)} · ${esc(well.name)}</title></circle></a>`;
  }).join("");
  const labels = state.catalog.areas.map((area) => `<text x="${sx(area.lon).toFixed(1)}" y="${(sy(area.lat) - 18).toFixed(1)}" text-anchor="middle" font-size="18" font-family="Georgia, serif" fill="#221c16">${esc(area.name)}</text>`).join("");
  const legend = state.catalog.areas.map((area) => `<span><i class="swatch" style="background:${AREA_COLORS[area.id]}"></i>${esc(area.name)}</span>`).join("");
  view.innerHTML = `
    ${filterBar()}
    <p class="count-line">${fmtNum(shown.size)} wells on the map. Dim dots are hidden by the current filter.</p>
    <div class="map-card">
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Well locations by operating area">
        ${labels}
        ${dots}
      </svg>
      <div class="legend">${legend}</div>
    </div>`;
  bindFilterBar();
}

function renderPeople() {
  const q = state.q.trim().toLowerCase();
  const blocks = state.catalog.teams.map((team) => {
    const people = state.catalog.users.filter((user) => {
      if (user.team_id !== team.id) return false;
      if (!q) return true;
      return [user.name, user.role_label, user.id].join(" ").toLowerCase().includes(q);
    });
    if (!people.length) return "";
    const cards = people.map((user) => `
      <a class="card" href="#/person/${esc(user.id)}">
        <span class="asset-id">${esc(user.id)}</span>
        <h2>${mark(user.name, state.q)}</h2>
        <p>${esc(user.role_label)}</p>
        <div class="card-foot">
          ${user.inspections ? `<span class="badge">${fmtNum(user.inspections)} inspections</span>` : ""}
          ${user.maintenance ? `<span class="badge">${fmtNum(user.maintenance)} jobs</span>` : ""}
          ${user.notes ? `<span class="badge">${fmtNum(user.notes)} notes</span>` : ""}
        </div>
      </a>`).join("");
    return `<section class="team-block"><h2>${esc(team.name)}</h2><p class="meta">Lead ${esc(team.lead)}</p><div class="people">${cards}</div></section>`;
  }).join("");
  view.innerHTML = blocks || `<p class="empty">No people match this search.</p>`;
}

function renderPerson(id) {
  const user = state.catalog.users.find((item) => item.id === id);
  if (!user) {
    view.innerHTML = `<p class="empty">No person with id ${esc(id)}.</p>`;
    return;
  }
  const charge = state.catalog.wells.filter((well) => well.in_charge_id === user.id);
  const leadOf = state.catalog.teams.filter((team) => team.lead_id === user.id);
  view.innerHTML = `
    <a class="back" href="#/people">All people</a>
    <div class="detail-head"><div>
      <p class="eyebrow">${esc(user.team_name)}</p>
      <h1>${esc(user.name)}</h1>
      <p class="meta">${esc(user.role_label)} · ${esc(user.id)}</p>
    </div></div>
    <div class="layout">
      <section class="panel">
        <h2>Wells in their charge</h2>
        ${charge.length ? `<div class="grid">${charge.map(wellCard).join("")}</div>` : `<p class="meta">This person is not the named lead on a well. Their work shows up as inspections, maintenance, or notes.</p>`}
      </section>
      <aside class="side">
        <dl class="kvs">
          <dt>Inspections filed</dt><dd>${fmtNum(user.inspections)}</dd>
          <dt>Maintenance jobs</dt><dd>${fmtNum(user.maintenance)}</dd>
          <dt>Notes written</dt><dd>${fmtNum(user.notes)}</dd>
          <dt>Leads</dt><dd>${leadOf.length ? leadOf.map((team) => esc(team.name)).join(", ") : "—"}</dd>
        </dl>
      </aside>
    </div>`;
}

function renderDocs() {
  const cards = state.catalog.docs.map((doc) => `
    <a class="card doc-link" href="#/doc/${esc(doc.id)}">
      <span>${esc(doc.kind)}</span>
      <h2>${esc(doc.title)}</h2>
    </a>`).join("");
  view.innerHTML = `
    <p class="count-line">Manuals and procedures the well records are meant to be read against.</p>
    <div class="docs">${cards}</div>`;
}

function renderDoc(id) {
  const doc = state.catalog.docs.find((item) => item.id === id);
  if (!doc) {
    view.innerHTML = `<p class="empty">No reference document named ${esc(id)}.</p>`;
    return;
  }
  view.innerHTML = `
    <a class="back" href="#/docs">All reference</a>
    <article class="doc doc-body">${doc.html}</article>`;
}

function badgeStatus(well) {
  return `<span class="badge ${well.status === "down" ? "down" : "active"}">${esc(well.status)}</span>`;
}

function readingBadge(row) {
  if (row.alarm) return `<span class="badge alarm">Alarm</span>`;
  if (row.producing) return `<span class="badge producing">Producing</span>`;
  return `<span class="badge idle">Not producing</span>`;
}

function spillBadge(row) {
  if (!row.spill) return "—";
  const bits = [`<span class="badge spill ${esc(row.spill_class || "")}">${esc(row.spill_class || "spill")}</span>`];
  if (row.corrected) bits.push(`<span class="badge correction">Corrected</span>`);
  if (row.spill_area_pct > 5) bits.push(`<span class="badge alarm">Over 5% of pad</span>`);
  const detail = `${row.spill_bbl == null ? "" : `${fmtNum(row.spill_bbl, 1)} bbl`}${row.spill_area_pct == null ? "" : ` · ${fmtNum(row.spill_area_pct, 1)}% of pad`}`;
  return `${bits.join(" ")}${detail ? `<div class="extra">${detail}</div>` : ""}`;
}

function paginate(items, page, size) {
  const pages = Math.max(1, Math.ceil(items.length / size) || 1);
  const current = Math.min(Math.max(page, 1), pages);
  const start = (current - 1) * size;
  return { items: items.slice(start, start + size), page: current, pages, total: items.length, start };
}

function pager(name, pageInfo) {
  if (pageInfo.total <= 25) return "";
  const end = Math.min(pageInfo.total, pageInfo.start + pageInfo.items.length);
  return `
    <div class="pager">
      <button type="button" data-pager="${name}" data-page="${pageInfo.page - 1}" ${pageInfo.page === 1 ? "disabled" : ""}>Previous</button>
      <span>${fmtNum(pageInfo.start + 1)}–${fmtNum(end)} of ${fmtNum(pageInfo.total)}</span>
      <button type="button" data-pager="${name}" data-page="${pageInfo.page + 1}" ${pageInfo.page === pageInfo.pages ? "disabled" : ""}>Next</button>
    </div>`;
}

function inspectionRows(well, data) {
  const q = state.inspQ.trim().toLowerCase();
  let rows = data.inspections;
  if (state.inspMode === "spills") rows = rows.filter((row) => row.spill && !row.corrected);
  if (state.inspMode === "corrected") rows = rows.filter((row) => row.corrected);
  if (state.inspMode === "idle") rows = rows.filter((row) => !row.producing);
  if (state.inspMode === "alarm") rows = rows.filter((row) => row.alarm);
  if (q) {
    rows = rows.filter((row) => [row.id, row.inspector, row.method, row.notes, row.spill_class].join(" ").toLowerCase().includes(q));
  }
  const page = paginate([...rows].reverse(), state.inspPage, 25);
  state.inspPage = page.page;
  const body = page.items.map((row) => {
    const extra = row.notes || (row.evidence || []).length;
    return `
      <tr id="focus-${esc(row.id)}">
        <td>${fmtDate(row.at, true)}</td>
        <td>${esc(row.method)}</td>
        <td>${row.inspector_id ? `<a href="#/person/${esc(row.inspector_id)}">${esc(row.inspector)}</a>` : "—"}</td>
        <td class="num">${fmtNum(row.ambient_c, 1)}</td>
        <td class="num">${fmtNum(row.wellhead_c, 1)}</td>
        <td class="num">${fmtDelta(row.delta_c)}</td>
        <td>${readingBadge(row)}</td>
        <td>${spillBadge(row)}</td>
        <td>${extra ? `<button class="expand" type="button" data-expand="extra-${esc(row.id)}" aria-expanded="false">Details</button>` : ""}</td>
      </tr>
      <tr id="extra-${esc(row.id)}" hidden>
        <td colspan="9" class="extra">
          ${row.notes ? `<p>${esc(row.notes)}</p>` : ""}
          ${(row.evidence || []).map((path) => `<div><code>${esc(path)}</code></div>`).join("") || ""}
        </td>
      </tr>`;
  }).join("");
  const empty = `<tr><td colspan="9">No inspections in this view.</td></tr>`;
  return `
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>When</th><th>Method</th><th>Inspector</th><th class="num">Air °C</th><th class="num">Wellhead °C</th><th class="num">Gap</th><th>Reading</th><th>Spill</th><th></th>
        </tr></thead>
        <tbody>${body || empty}</tbody>
      </table>
    </div>
    ${pager("insp", page)}`;
}

function maintenanceRows(data) {
  let rows = data.maintenance;
  if (state.maintMode === "corrective") rows = rows.filter((row) => row.type === "corrective");
  if (state.maintMode === "scheduled") rows = rows.filter((row) => row.type === "scheduled");
  const page = paginate([...rows].reverse(), state.maintPage, 25);
  state.maintPage = page.page;
  const body = page.items.map((row) => `
    <tr id="focus-${esc(row.id)}">
      <td>${fmtDate(row.started_at)}</td>
      <td><span class="badge ${esc(row.type)}">${esc(row.type)}</span></td>
      <td>${esc(kindLabel(row.equipment))}</td>
      <td>${esc(row.issue)}${row.triggered_by ? `<div class="extra">From ${esc(row.triggered_by)}</div>` : ""}</td>
      <td>${row.technician_id ? `<a href="#/person/${esc(row.technician_id)}">${esc(row.technician)}</a>` : "—"}${row.contractor ? `<div class="extra">${esc(row.contractor)}</div>` : ""}</td>
      <td class="num">${fmtNum(row.downtime_hours)}</td>
      <td class="num">${fmtMoney(row.cost)}</td>
      <td>${row.report_html ? `<a href="#/well/${esc(data.asset_id)}/reports/${esc(row.id)}">Report</a>` : "—"}</td>
    </tr>`).join("");
  return `
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Started</th><th>Type</th><th>Equipment</th><th>Issue</th><th>Crew</th><th class="num">Hours down</th><th class="num">Cost</th><th></th>
        </tr></thead>
        <tbody>${body || `<tr><td colspan="8">No maintenance in this view.</td></tr>`}</tbody>
      </table>
    </div>
    ${pager("maint", page)}`;
}

function panelTools(tab) {
  if (tab === "inspections") {
    const chip = (mode, label) => `<button class="chip" type="button" data-insp="${mode}" aria-pressed="${state.inspMode === mode}">${label}</button>`;
    return `<div class="tools">
      <input id="insp-q" type="search" placeholder="Filter by inspector, note, or id" value="${esc(state.inspQ)}">
      ${chip("all", "All")}
      ${chip("spills", "Spills")}
      ${chip("corrected", "Corrected")}
      ${chip("idle", "Not producing")}
      ${chip("alarm", "Alarms")}
    </div>`;
  }
  if (tab === "maintenance") {
    const chip = (mode, label) => `<button class="chip" type="button" data-maint="${mode}" aria-pressed="${state.maintMode === mode}">${label}</button>`;
    return `<div class="tools">${chip("all", "All")} ${chip("corrective", "Corrective")} ${chip("scheduled", "Scheduled")}</div>`;
  }
  return "";
}

function panelBody(well, data, tab) {
  if (tab === "inspections") return inspectionRows(well, data);
  if (tab === "maintenance") return maintenanceRows(data);
  if (tab === "reports") {
    const reports = [...data.maintenance].reverse().filter((row) => row.report_html);
    const extras = data.unlinked_reports || [];
    if (!reports.length && !extras.length) return `<p class="meta">No written report is filed for this well. Scheduled jobs usually do not get one.</p>`;
    const blocks = reports.map((row) => `
      <details class="report" id="focus-${esc(row.id)}">
        <summary><strong>${esc(row.id)}</strong> · ${esc(kindLabel(row.equipment))} · ${fmtDate(row.started_at)} · ${fmtMoney(row.cost)}</summary>
        <div class="doc-body">${row.report_html}</div>
      </details>`).join("");
    const more = extras.map((row) => `
      <details class="report"><summary><strong>${esc(row.file)}</strong></summary><div class="doc-body">${row.html}</div></details>`).join("");
    return blocks + more;
  }
  if (tab === "notes") {
    if (!data.context.length) return `<p class="meta">No context notes. Notes are how an operator explains a cold wellhead or cancels a false spill.</p>`;
    return [...data.context].reverse().map((note) => `
      <article class="note" id="focus-${esc(note.id)}">
        <h3>${esc(NOTE_LABELS[note.type] || note.type)}</h3>
        <p class="meta">${note.author_id ? `<a href="#/person/${esc(note.author_id)}">${esc(note.author)}</a>` : "Unknown"} · ${fmtDate(note.at, true)} · valid ${fmtDate(note.valid_from)}${note.valid_to ? ` to ${fmtDate(note.valid_to)}` : ""}</p>
        <p>${esc(note.text)}</p>
        ${note.inspection_id ? `<p class="extra">About ${esc(note.inspection_id)}</p>` : ""}
      </article>`).join("");
  }
  if (tab === "regulatory") {
    if (!data.regulatory.length) return `<p class="meta">No agency events on this well.</p>`;
    return [...data.regulatory].reverse().map((event) => `
      <article class="reg" id="focus-${esc(event.id)}">
        <div class="row-between">
          <h3>${esc(EVENT_LABELS[event.type] || event.type)}</h3>
          <span class="badge ${event.status === "open" ? "open" : "active"}">${esc(event.status)}</span>
        </div>
        <p class="meta">${fmtDate(event.date)} · ${esc(event.agency)} · ${esc(event.protocol || "no protocol")}</p>
        <p>${esc(event.detail)}</p>
        <p class="extra">${event.hours_to_report == null ? "" : `Written notice ${fmtNum(event.hours_to_report)} h after discovery. `}${event.fine_usd ? `Fine ${fmtMoney(event.fine_usd)}.` : "No fine."} ${event.inspection_id ? `Inspection ${esc(event.inspection_id)}.` : ""}</p>
      </article>`).join("");
  }
  const latest = data.inspections[data.inspections.length - 1];
  const equipment = well.equipment.map((item) => `<tr><td>${esc(kindLabel(item.kind))}</td><td>${esc(item.model)}</td><td class="extra">${esc(item.id)}</td></tr>`).join("");
  return `
    <div class="callout">
      A ${esc(well.well_type)} well counts as producing when the wellhead is at least <strong>${fmtNum(well.producing_gap_c, 0)} °C</strong> warmer than the air.
      Shut in and inspect if the wellhead is above <strong>${fmtNum(well.alarm_c, 0)} °C</strong>.
      ${latest ? `Latest reading: air ${fmtNum(latest.ambient_c, 1)} °C, wellhead ${fmtNum(latest.wellhead_c, 1)} °C, gap ${fmtDelta(latest.delta_c)}. ${latest.producing ? "That reading is producing." : "That reading is not producing."}` : ""}
    </div>
    <h2>Files in this folder</h2>
    <div class="tree">
      assets/${esc(well.asset_id)}/<br>
      &nbsp;&nbsp;metadata.json<br>
      &nbsp;&nbsp;inspections.jsonl <b>${fmtNum(well.inspection_count)}</b><br>
      &nbsp;&nbsp;context.jsonl <b>${fmtNum(well.context_count)}</b><br>
      &nbsp;&nbsp;maintenance.jsonl <b>${fmtNum(well.maintenance_count)}</b><br>
      &nbsp;&nbsp;maintenance-reports/ <b>${fmtNum(well.report_count)}</b><br>
      &nbsp;&nbsp;regulatory-events.jsonl <b>${fmtNum(well.regulatory_count)}</b>
    </div>
    <h2>Equipment</h2>
    <div class="table-wrap"><table><thead><tr><th>Kind</th><th>Model</th><th>Id</th></tr></thead><tbody>${equipment}</tbody></table></div>`;
}

function sideCard(well) {
  return `
    <aside class="side">
      <dl class="kvs">
        <dt>Area</dt><dd><a href="#/wells" data-area="${esc(well.area_id)}">${esc(well.area_name)}</a></dd>
        <dt>Class</dt><dd><a href="#/doc/${well.well_type === "T350" ? "t350" : "t200"}">${esc(well.well_type)} manual</a></dd>
        <dt>In charge</dt><dd>${well.in_charge_id ? `<a href="#/person/${esc(well.in_charge_id)}">${esc(well.in_charge)}</a>` : "—"}</dd>
        <dt>Installed</dt><dd>${fmtDate(well.installed_on)}</dd>
        <dt>Location</dt><dd>${fmtNum(well.lat, 5)}, ${fmtNum(well.lon, 5)}</dd>
        <dt>Recorded status</dt><dd>${badgeStatus(well)}</dd>
        <dt>Last inspection</dt><dd>${fmtDate(well.last_inspection_at, true)}<div class="extra">${esc(well.last_inspector || "")}</div></dd>
        <dt>Spill readings</dt><dd>${fmtNum(well.spill_count)}${well.max_spill_bbl == null ? "" : `<div class="extra">Largest ${fmtNum(well.max_spill_bbl, 1)} bbl</div>`}</dd>
        <dt>Corrective jobs</dt><dd>${fmtNum(well.corrective_count)} of ${fmtNum(well.maintenance_count)}</dd>
        <dt>Open agency items</dt><dd>${fmtNum(well.open_regulatory)}</dd>
      </dl>
    </aside>`;
}

function paintWell(well, data, route) {
  const tabs = [
    ["overview", "Overview"],
    ["inspections", `Inspections`],
    ["maintenance", `Maintenance`],
    ["reports", `Reports`],
    ["notes", `Notes`],
    ["regulatory", `Regulatory`],
  ];
  const tab = tabs.some(([id]) => id === route.tab) ? route.tab : "overview";
  view.innerHTML = `
    <a class="back" href="#/wells">All wells</a>
    <div class="detail-head">
      <div>
        <p class="eyebrow">${esc(well.asset_id)} · ${esc(well.area_id)}</p>
        <h1>${esc(well.name)}</h1>
        <p class="meta">${esc(well.well_type)} · ${badgeStatus(well)} ${well.has_separator ? `<span class="badge">Separator</span>` : ""}</p>
      </div>
    </div>
    <nav class="tabs" aria-label="Well records">
      ${tabs.map(([id, label]) => `<a href="#/well/${esc(well.asset_id)}/${id}" ${tab === id ? 'aria-current="page"' : ""}>${label}</a>`).join("")}
    </nav>
    <div class="layout">
      <section class="panel" id="panel">
        ${panelTools(tab)}
        <div id="panel-body">${panelBody(well, data, tab)}</div>
      </section>
      ${sideCard(well)}
    </div>`;
  const panel = document.getElementById("panel");
  panel.addEventListener("click", (event) => onPanelClick(event, well, data, tab));
  panel.addEventListener("input", (event) => {
    if (event.target.id !== "insp-q") return;
    state.inspQ = event.target.value;
    state.inspPage = 1;
    document.getElementById("panel-body").innerHTML = inspectionRows(well, data);
  });
  view.querySelector("[data-area]")?.addEventListener("click", (event) => {
    event.preventDefault();
    state.area = well.area_id;
    location.hash = "#/wells";
  });
  const focus = route.focus ? document.getElementById(`focus-${route.focus}`) : null;
  if (focus) {
    if (focus.tagName === "DETAILS") focus.open = true;
    focus.classList.add("focus-hit");
    focus.scrollIntoView({ block: "center" });
  }
}

function onPanelClick(event, well, data, tab) {
  const expand = event.target.closest("[data-expand]");
  if (expand) {
    const row = document.getElementById(expand.dataset.expand);
    const willOpen = row.hasAttribute("hidden");
    row.toggleAttribute("hidden", !willOpen);
    expand.setAttribute("aria-expanded", String(willOpen));
    return;
  }
  const insp = event.target.closest("[data-insp]");
  if (insp) {
    state.inspMode = insp.dataset.insp;
    state.inspPage = 1;
    paintWell(well, data, { ...parseHash(), tab: "inspections" });
    return;
  }
  const maint = event.target.closest("[data-maint]");
  if (maint) {
    state.maintMode = maint.dataset.maint;
    state.maintPage = 1;
    paintWell(well, data, { ...parseHash(), tab: "maintenance" });
    return;
  }
  const pageBtn = event.target.closest("[data-pager]");
  if (pageBtn && !pageBtn.disabled) {
    const next = Number(pageBtn.dataset.page);
    if (pageBtn.dataset.pager === "insp") state.inspPage = next;
    if (pageBtn.dataset.pager === "maint") state.maintPage = next;
    document.getElementById("panel-body").innerHTML = tab === "maintenance" ? maintenanceRows(data) : inspectionRows(well, data);
  }
}

function renderWellRoute(route) {
  const well = state.catalog.wells.find((item) => item.asset_id === route.id);
  if (!well) {
    view.innerHTML = `<p class="empty">No well named ${esc(route.id)}. Assets run from WELL-0001 to WELL-0200.</p>`;
    return;
  }
  if (state.wellId !== route.id) {
    state.wellId = route.id;
    state.inspMode = "all";
    state.inspPage = 1;
    state.inspQ = "";
    state.maintMode = "all";
    state.maintPage = 1;
  }
  const cached = state.wellCache.get(route.id);
  if (cached) {
    paintWell(well, cached, route);
    return;
  }
  view.innerHTML = `<p class="loading">Opening ${esc(route.id)}…</p>`;
  const token = ++state.wellToken;
  fetch(`data/wells/${route.id}.json`)
    .then((response) => {
      if (!response.ok) throw new Error("missing well file");
      return response.json();
    })
    .then((data) => {
      state.wellCache.set(route.id, data);
      if (token !== state.wellToken) return;
      if (parseHash().view !== "well" || parseHash().id !== route.id) return;
      paintWell(well, data, parseHash());
    })
    .catch(() => {
      if (token !== state.wellToken) return;
      view.innerHTML = `<p class="empty">Could not load ${esc(route.id)}. Start the catalog with <code>python explorer/serve.py</code> from the sample-data folder.</p>`;
    });
}

function render() {
  const route = parseHash();
  paintNav(route);
  if (!state.catalog) return;
  if (route.view === "map") return renderMap();
  if (route.view === "people") return renderPeople();
  if (route.view === "person") return renderPerson(route.id);
  if (route.view === "docs") return renderDocs();
  if (route.view === "doc") return renderDoc(route.id);
  if (route.view === "well") return renderWellRoute(route);
  renderWells();
}

document.getElementById("search-form").addEventListener("submit", (event) => event.preventDefault());
searchInput.addEventListener("input", () => {
  state.q = searchInput.value;
  const route = parseHash();
  if (route.view === "well" || route.view === "doc" || route.view === "person") {
    location.hash = "#/wells";
    return;
  }
  render();
});
window.addEventListener("hashchange", render);
document.addEventListener("keydown", (event) => {
  const typing = event.target.matches("input, textarea, select");
  if (event.key === "/" && !typing) {
    event.preventDefault();
    searchInput.focus();
  }
  if (event.key === "Escape" && document.activeElement === searchInput && searchInput.value) {
    searchInput.value = "";
    state.q = "";
    render();
  }
});

fetch("data/catalog.json")
  .then((response) => {
    if (!response.ok) throw new Error("catalog missing");
    return response.json();
  })
  .then((catalog) => {
    state.catalog = catalog;
    searchInput.disabled = false;
    paintStats();
    render();
  })
  .catch(() => {
    view.innerHTML = `
      <div class="empty">
        <p>The catalog has not been loaded. Opening this file directly blocks the browser from reading the data.</p>
        <p>From the <code>sample-data</code> folder, run:</p>
        <p><code>python explorer/build.py</code></p>
        <p><code>python explorer/serve.py</code></p>
        <p>Then use the page at <code>http://127.0.0.1:8765/</code>.</p>
      </div>`;
  });
