"use strict";

// Snapshot statique produit par le backend (app.services.export_static).
let DATA = null;
let SETS = [];        // [{id,name,parent_id,preferences:[...]}]
let SET_BY_ID = {};
let currentSetId = null;
let map = null, markerLayer = null;
let openBien = null;   // bien actuellement ouvert dans la modale (pour rafraîchir les votes)

const $ = (s) => document.querySelector(s);
const euros = (n) => (n == null ? "—" : Number(n).toLocaleString("fr-FR") + " €");
const fix1 = (n) => (n == null ? "—" : Number(n).toFixed(1));
const voteKey = (b) => `${b.source}__${b.external_id}`;

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
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModal(); closeIdentityIfAllowed(); } });

  // Votes (étoiles) : init backend + identité de session.
  await Votes.init(window.APP_CONFIG || {});
  Votes.onChange(() => { render(); if (openBien) refreshModal(); });
  $("#whoami").addEventListener("click", openIdentity);
  $("#identity .id-backdrop").addEventListener("click", closeIdentityIfAllowed);
  renderWhoami();
  if (!Votes.voter) openIdentity();

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
  if (mode === "note") { const v = Votes.forBien(voteKey(bien)).avg; return v == null ? -1 : v; }
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
  if (m != null) parts.push(`<span class="badge match" title="Match du set">🎯 ${fix1(m)}</span>`);
  if (bien.score != null) parts.push(`<span class="badge score" title="Score d'investissement">📈 ${fix1(bien.score)}</span>`);
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
        ${starsRow(b)}
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
    card.querySelectorAll(".star").forEach((st) =>
      st.addEventListener("click", (e) => { e.stopPropagation(); handleStar(st); }));
    card.addEventListener("click", (e) => { if (e.target.closest(".stars")) return; openModal(b); });
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

// Tableau unique pour le SET SÉLECTIONNÉ : par critère, le score algo, TON vote
// (étoiles, optionnel) et la moyenne du groupe.
function criteriaTable(bien) {
  const set = SET_BY_ID[String(currentSetId)];
  if (!set || !(set.preferences || []).length) return "";
  const id = voteKey(bien);
  const sb = (bien.scores_by_set || {})[String(currentSetId)] || {};
  const algoBy = {};
  for (const d of (sb.details || [])) algoBy[d.label || d.kind] = d;

  const rows = set.preferences.map((p) => {
    const label = p.label || p.kind;
    const d = algoBy[label];
    let algoCell = "—";
    if (d && d.subscore != null) algoCell = Math.round(d.subscore * 100) + "%";
    else if (d) algoCell = `<span class="${statusCls(d.status)}">${d.status}</span>`;
    const info = Votes.forBien(id, label);
    const grp = info.count ? `${info.avg.toFixed(1)} <span class="weighttag">(${info.count})</span>` : "—";
    const detailTxt = d && d.detail ? `<div class="detailtxt">${d.detail}</div>` : "";
    return `<tr>
      <td>${label}${detailTxt}</td>
      <td class="num">${algoCell}</td>
      <td class="num">${starsWidget(id, "sm", label)}</td>
      <td class="num">${grp}</td>
    </tr>`;
  }).join("");

  return `<div class="detailtxt" style="margin-bottom:6px">Match global ${set.name} : <b>${fix1(sb.match_score)}</b></div>
    <table class="scores crit-tbl">
      <tr><th>Critère</th><th class="num">Algo</th><th class="num">Ton vote</th><th class="num">Groupe</th></tr>
      ${rows}
    </table>`;
}

function openModal(bien) {
  if (!bien) return;
  openBien = bien;
  const card = $("#modalCard");
  card.innerHTML = `
    <button class="modal-close" id="mclose">×</button>
    <h2>${bien.commune || "?"} <span class="sub">(${bien.departement || "—"})</span></h2>
    <div class="price" style="color:var(--accent);font-weight:700;font-size:18px">${euros(bien.prix)}</div>
    <div class="sub">${bien.type_bien || "bien"} · ${bien.nb_chambres ?? "?"} ch · ${bien.nb_pieces ?? "?"} p ·
      terrain ${bien.surface_terrain != null ? bien.surface_terrain + " m²" : "—"} ·
      ${bien.altitude != null ? Math.round(bien.altitude) + " m alt." : ""}</div>
    ${bien.is_favori ? `<div class="note">⭐ ${bien.favori_note || "favori"}</div>` : ""}

    <div class="section-title">Note globale ⭐ <span class="detailtxt">(${Votes.backend === "supabase" ? "partagés" : "local"})</span></div>
    ${votesBlock(bien)}

    <div class="section-title">Critères <span class="detailtxt">— algo vs vos votes (set : ${(SET_BY_ID[String(currentSetId)] || {}).name || "—"})</span></div>
    ${criteriaTable(bien)}

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
  card.querySelectorAll(".star").forEach((st) =>
    st.addEventListener("click", () => handleStar(st)));
  const saveBtn = $("#saveComment");
  if (saveBtn) saveBtn.addEventListener("click", () => {
    const val = $("#myComment").value.trim();
    Votes.setComment(voteKey(bien), val).then((res) => {
      if (res && res.ok === false && res.reason === "no-stars") {
        $("#commentMsg").textContent = "Donne d'abord une note ⭐ pour commenter.";
      }
    });
  });
}
function closeModal() { $("#modal").classList.add("hidden"); openBien = null; }
function refreshModal() { if (openBien && !$("#modal").classList.contains("hidden")) openModal(openBien); }

// ---------- Votes (étoiles) ----------
const escAttr = (s) => String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
const escHtml = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function starsWidget(id, size, criterion) {
  const crit = criterion || Votes.OVERALL;
  const mine = Votes.forBien(id, crit).mine || 0;
  let s = "";
  for (let i = 1; i <= 5; i++) s += `<span class="star ${i <= mine ? "on" : ""}" data-v="${i}">★</span>`;
  return `<span class="stars ${size || ""}" data-bien="${escAttr(id)}" data-crit="${escAttr(crit)}">${s}</span>`;
}
function starsRow(b) {
  const info = Votes.forBien(voteKey(b));
  const meta = info.count
    ? `<span class="vavg">moy ${info.avg.toFixed(1)} · ${info.count} vote${info.count > 1 ? "s" : ""}</span>`
    : `<span class="vavg detailtxt">non noté</span>`;
  return `<div class="voterow">${starsWidget(voteKey(b))} ${meta}</div>`;
}
function votesBlock(b) {
  const id = voteKey(b);
  const info = Votes.forBien(id);
  const rows = Votes.users.map((u) => {
    const e = info.byUser[u];
    const cell = e ? `<span style="color:#fbbf24">${"★".repeat(e.stars)}</span><span class="detailtxt">${"☆".repeat(5 - e.stars)}</span>`
                   : `<span class="detailtxt">—</span>`;
    const com = (e && e.comment) ? `<div class="vcomment">“${escHtml(e.comment)}”</div>` : "";
    return `<tr><td>${u}${u === Votes.voter ? ' <span class="weighttag">(toi)</span>' : ""}${com}</td><td class="num">${cell}</td></tr>`;
  }).join("");
  const myLabel = Votes.voter ? "Ta note" : "Choisis ton identité pour noter";
  const editor = Votes.voter ? `
    <div class="comment-edit">
      <textarea id="myComment" rows="2" placeholder="Un mot sur ce bien ? (optionnel, avec ta note)">${escHtml(info.mineComment || "")}</textarea>
      <div class="comment-actions"><span id="commentMsg" class="detailtxt"></span><button class="btn" id="saveComment">Enregistrer</button></div>
    </div>` : "";
  return `<div class="votewrap">
    <div class="myvote"><span>${myLabel} :</span> ${starsWidget(id, "big")}</div>
    ${editor}
    <table class="scores votes-tbl"><tr><th>Qui</th><th class="num">Note</th></tr>${rows}
      <tr><th>Moyenne</th><td class="num"><b>${info.avg != null ? info.avg.toFixed(1) : "—"}</b> (${info.count})</td></tr></table>
  </div>`;
}
function handleStar(st) {
  if (!Votes.voter) { openIdentity(); return; }
  const wrap = st.closest(".stars");
  const v = Number(st.dataset.v);
  Votes.setMine(wrap.dataset.bien, v, wrap.dataset.crit);  // optimiste ; onChange -> render + refreshModal
}

// ---------- Identité de session ----------
function renderWhoami() {
  $("#whoami").textContent = Votes.voter ? `🙂 ${Votes.voter}` : "Qui es-tu ?";
}
function openIdentity() {
  const list = $("#idlist");
  list.innerHTML = Votes.users.map((u) =>
    `<button class="idbtn ${u === Votes.voter ? "cur" : ""}">${u}</button>`).join("");
  list.querySelectorAll(".idbtn").forEach((btn) =>
    btn.addEventListener("click", () => {
      Votes.setVoter(btn.textContent);
      $("#identity").classList.add("hidden");
      renderWhoami(); render(); if (openBien) refreshModal();
    }));
  $("#identity").classList.remove("hidden");
}
function closeIdentityIfAllowed() {
  if (Votes.voter) $("#identity").classList.add("hidden");  // fermable une fois identifié
}

boot();
