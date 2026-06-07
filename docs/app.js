"use strict";

// Snapshot statique produit par le backend (app.services.export_static).
let DATA = null;
let SETS = [];        // [{id,name,parent_id,preferences:[...]}]
let SET_BY_ID = {};
let currentSetId = null;
let map = null, markerLayer = null;

const $ = (s) => document.querySelector(s);
const euros = (n) => (n == null ? "—" : Number(n).toLocaleString("fr-FR") + " €");
const fix1 = (n) => (n == null ? "—" : Number(n).toFixed(1));

async function boot() {
  DATA = await fetch("data/data.json").then((r) => r.json());
  SETS = DATA.sets || [];
  SET_BY_ID = Object.fromEntries(SETS.map((s) => [String(s.id), s]));
  currentSetId = SETS.length ? String(SETS[0].id) : null;

  const setSel = $("#setSelect");
  setSel.innerHTML = SETS.map((s) => {
    const prefix = s.parent_id ? "↳ " : "";
    return `<option value="${s.id}">${prefix}${s.name}</option>`;
  }).join("");
  setSel.value = currentSetId;

  $("#meta").textContent =
    `${DATA.stats.n_biens} biens · ${DATA.stats.n_searches} recherches · snapshot ${new Date(DATA.generated_at).toLocaleString("fr-FR")}`;

  setSel.addEventListener("change", (e) => { currentSetId = e.target.value; render(); });
  $("#sortSelect").addEventListener("change", render);
  $("#favOnly").addEventListener("change", render);
  $("#scoreMin").addEventListener("input", (e) => { $("#scoreOut").textContent = e.target.value; render(); });
  $("#modeScroll").addEventListener("click", () => setMode("scroll"));
  $("#modeMap").addEventListener("click", () => setMode("map"));
  $("#modal .modal-backdrop").addEventListener("click", closeModal);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  render();
}

// --- score helpers ------------------------------------------------------
function matchOf(bien, setId) {
  const s = (bien.scores_by_set || {})[String(setId)];
  return s && s.match_score != null ? s.match_score : null;
}
function sortValue(bien, mode) {
  if (mode === "prix") return bien.prix == null ? Infinity : bien.prix;
  if (mode === "score") return bien.score == null ? -1 : bien.score;
  const m = matchOf(bien, currentSetId);
  return m == null ? -1 : m;
}

function visibleBiens() {
  const favOnly = $("#favOnly").checked;
  const min = Number($("#scoreMin").value);
  const sortMode = $("#sortSelect").value;
  let list = (DATA.biens || []).filter((b) => {
    if (favOnly && !b.is_favori) return false;
    const ref = sortMode === "score" ? b.score : matchOf(b, currentSetId);
    if (min > 0 && (ref == null || ref < min)) return false;
    return true;
  });
  list.sort((a, b) => {
    const va = sortValue(a, sortMode), vb = sortValue(b, sortMode);
    return sortMode === "prix" ? va - vb : vb - va;
  });
  return list;
}

// --- rendering ----------------------------------------------------------
function render() {
  const list = visibleBiens();
  renderScroll(list);
  if (!$("#mapView").classList.contains("hidden")) renderMap(list);
}

function gallery(bien) {
  const photos = bien.photos || [];
  if (!photos.length) {
    const n = bien.n_photos_source ? ` (${bien.n_photos_source} non téléchargées)` : "";
    return `<div class="gallery"><div class="nophoto">pas de photo${n}</div></div>`;
  }
  const imgs = photos.map((p) => `<img loading="lazy" src="data/${p}" alt="" />`).join("");
  const dots = photos.map((_, i) => `<i class="${i === 0 ? "on" : ""}"></i>`).join("");
  const nav = photos.length > 1
    ? `<button class="gnav prev" data-d="-1">‹</button><button class="gnav next" data-d="1">›</button>`
    : "";
  return `<div class="gallery">${imgs}${nav}<div class="dots">${dots}</div></div>`;
}

function badges(bien) {
  const m = matchOf(bien, currentSetId);
  const parts = [];
  if (m != null) parts.push(`<span class="badge match">match ${fix1(m)}</span>`);
  if (bien.score != null) parts.push(`<span class="badge score">invest ${fix1(bien.score)}</span>`);
  return `<div class="badges">${parts.join("")}</div>` +
         (bien.is_favori ? `<div class="fav-star" title="favori">⭐</div>` : "");
}

function renderScroll(list) {
  const root = $("#scrollView");
  root.innerHTML = list.map((b, idx) => `
    <article class="card" data-idx="${idx}">
      <div class="galwrap" style="position:relative">${gallery(b)}${badges(b)}</div>
      <div class="body">
        <div class="price">${euros(b.prix)}</div>
        <h3>${b.commune || "?"} <span class="sub">(${b.departement || "—"})</span></h3>
        <div class="sub">${b.type_bien || "bien"} · ${b.nb_chambres ?? "?"} ch · terrain ${b.surface_terrain != null ? b.surface_terrain + " m²" : "—"}</div>
        <div class="chips">${(b.features || []).slice(0, 6).map((f) => `<span class="chip">${f}</span>`).join("")}</div>
        ${b.favori_note ? `<div class="note">⭐ ${b.favori_note}</div>` : ""}
      </div>
    </article>`).join("") || `<p class="meta">Aucun bien ne correspond aux filtres.</p>`;

  // listeners (galerie + ouverture détail)
  root.querySelectorAll(".card").forEach((card) => {
    const b = list[Number(card.dataset.idx)];
    const gal = card.querySelector(".gallery");
    card.querySelectorAll(".gnav").forEach((btn) =>
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        gal.scrollBy({ left: Number(btn.dataset.d) * gal.clientWidth, behavior: "smooth" });
      }));
    if (gal) gal.addEventListener("scroll", () => {
      const i = Math.round(gal.scrollLeft / gal.clientWidth);
      card.querySelectorAll(".dots i").forEach((d, k) => d.classList.toggle("on", k === i));
    });
    card.addEventListener("click", () => openModal(b));
  });
}

function renderMap(list) {
  if (!map) {
    map = L.map("map").setView([44.8, 4.6], 8);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: "© OpenStreetMap",
    }).addTo(map);
    markerLayer = L.layerGroup().addTo(map);
  }
  markerLayer.clearLayers();
  const pts = [];
  list.forEach((b) => {
    if (b.latitude == null || b.longitude == null) return;
    pts.push([b.latitude, b.longitude]);
    const m = matchOf(b, currentSetId);
    const mk = L.circleMarker([b.latitude, b.longitude], {
      radius: 9, color: "#04210f", weight: 1,
      fillColor: m != null && m >= 70 ? "#4ade80" : m != null && m >= 55 ? "#fbbf24" : "#38bdf8",
      fillOpacity: .9,
    });
    mk.bindPopup(
      `<b>${b.commune || "?"}</b> (${b.departement || "—"})<br>${euros(b.prix)}<br>` +
      `match ${fix1(m)} · invest ${fix1(b.score)}<br>` +
      `<a href="#" onclick="window.__open(${b.id});return false;">détails →</a>`);
    mk.addTo(markerLayer);
  });
  if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 11 });
}
window.__open = (id) => openModal((DATA.biens || []).find((b) => b.id === id));

function setMode(mode) {
  const scroll = mode === "scroll";
  $("#scrollView").classList.toggle("hidden", !scroll);
  $("#mapView").classList.toggle("hidden", scroll);
  $("#modeScroll").classList.toggle("active", scroll);
  $("#modeMap").classList.toggle("active", !scroll);
  if (!scroll) { renderMap(visibleBiens()); setTimeout(() => map.invalidateSize(), 50); }
}

// --- modal: tableau comparatif des scores -------------------------------
function statusCls(s) { return s === "pending" ? "st-pending" : s === "ok" ? "" : "st-na"; }

function pillarsTable(bien) {
  const pillars = bien.score_details || [];
  if (!pillars.length) return `<p class="detailtxt">Pas de détail de score d'investissement.</p>`;
  let rows = "";
  for (const p of pillars) {
    rows += `<tr><th colspan="2" class="setcol-head">${p.label} — ${fix1(p.score)}/100
      <span class="weighttag">(poids ${p.weight})</span></th></tr>`;
    for (const sp of (p.subpillars || [])) {
      const pct = sp.subscore != null ? Math.round(sp.subscore * 100) : 0;
      rows += `<tr>
        <td>${sp.label}<div class="detailtxt ${statusCls(sp.status)}">${sp.detail || sp.status}</div>
          <div class="bar"><span style="width:${pct}%"></span></div></td>
        <td class="num">${sp.subscore != null ? pct + "%" : "<span class='st-" + (sp.status==='pending'?'pending':'na') + "'>" + sp.status + "</span>"}
          <div class="weighttag">×${sp.weight}</div></td>
      </tr>`;
    }
  }
  return `<table class="scores">${rows}</table>`;
}

function prefsComparativeTable(bien) {
  // Colonnes = sets (têtard, ↳ Léo). Lignes = critères (union des préférences).
  const setsWithPrefs = SETS.filter((s) => (s.preferences || []).length);
  if (!setsWithPrefs.length) return "";
  // index détails par set: key = kind|label
  const detailIndex = {};   // setId -> {critKey -> entry}
  const critOrder = [];     // [{key,label}]
  const seen = new Set();
  for (const s of setsWithPrefs) {
    const sb = (bien.scores_by_set || {})[String(s.id)] || {};
    detailIndex[s.id] = {};
    for (const d of (sb.details || [])) {
      const key = (d.kind || "") + "|" + (d.label || "");
      detailIndex[s.id][key] = d;
      if (!seen.has(key)) { seen.add(key); critOrder.push({ key, label: d.label || d.kind }); }
    }
  }
  const head = `<tr><th>Critère</th>${setsWithPrefs.map((s) =>
    `<th class="setcol-head">${s.parent_id ? "↳ " : ""}${s.name}</th>`).join("")}</tr>`;
  const totalRow = `<tr><th>Match global</th>${setsWithPrefs.map((s) => {
    const m = ((bien.scores_by_set || {})[String(s.id)] || {}).match_score;
    return `<td class="num"><b>${fix1(m)}</b></td>`;
  }).join("")}</tr>`;
  const rows = critOrder.map(({ key, label }) => {
    const cells = setsWithPrefs.map((s) => {
      const d = detailIndex[s.id][key];
      if (!d) return `<td class="num detailtxt">—</td>`;
      if (d.subscore == null) return `<td class="num"><span class="${statusCls(d.status)}">${d.status}</span><div class="detailtxt">${d.detail || ""}</div></td>`;
      const pct = Math.round(d.subscore * 100);
      return `<td class="num">${pct}% <span class="weighttag">×${d.weight}</span>
        <div class="detailtxt">${d.detail || ""}</div></td>`;
    }).join("");
    return `<tr><td>${label}</td>${cells}</tr>`;
  }).join("");
  return `<table class="scores">${head}${totalRow}${rows}</table>`;
}

function openModal(bien) {
  if (!bien) return;
  const card = $("#modalCard");
  card.innerHTML = `
    <button class="modal-close" id="mclose">×</button>
    <h2>${bien.commune || "?"} <span class="sub">(${bien.departement || "—"})</span></h2>
    <div class="price" style="color:var(--accent);font-weight:700;font-size:18px">${euros(bien.prix)}</div>
    <div class="sub">${bien.type_bien || "bien"} · ${bien.nb_chambres ?? "?"} ch · ${bien.nb_pieces ?? "?"} p ·
      terrain ${bien.surface_terrain != null ? bien.surface_terrain + " m²" : "—"} ·
      ${bien.altitude != null ? Math.round(bien.altitude) + " m alt." : ""}</div>
    ${bien.is_favori ? `<div class="note">⭐ ${bien.favori_note || "favori"}</div>` : ""}

    <div class="section-title">Match par critère et par set</div>
    ${prefsComparativeTable(bien)}

    <div class="section-title">Score d'investissement (piliers)</div>
    ${pillarsTable(bien)}

    ${(bien.risques && bien.risques.length) ? `<div class="section-title">Risques</div>
      <div class="chips">${bien.risques.map((r) => `<span class="chip">${typeof r === "string" ? r : (r.label || r.type || JSON.stringify(r))}</span>`).join("")}</div>` : ""}

    <div class="modal-actions">
      ${bien.url ? `<a class="btn" href="${bien.url}" target="_blank" rel="noopener">Voir l'annonce ↗</a>` : ""}
      <button class="btn ghost" id="mclose2">Fermer</button>
    </div>`;
  $("#modal").classList.remove("hidden");
  $("#mclose").addEventListener("click", closeModal);
  $("#mclose2").addEventListener("click", closeModal);
}
function closeModal() { $("#modal").classList.add("hidden"); }

boot();
