"use strict";

// Snapshot statique produit par le backend (app.services.export_static).
let DATA = null;
let SETS = [];        // [{id,name,parent_id,preferences:[...]}]
let SET_BY_ID = {};
let currentSetId = null;
let map = null, markerLayer = null;
let openBien = null;   // bien actuellement ouvert dans la modale (pour rafraîchir les votes)
let modalMapInstance = null;   // carte Leaflet interactive de la fiche

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
  // Relâche du clic/doigt n'importe où -> ferme la grande carte (appui maintenu).
  ["pointerup", "pointercancel"].forEach((ev) => window.addEventListener(ev, hideBigMap));

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
        <div class="body-main">
          <div class="price">${euros(b.prix)}</div>
          <h3>${b.commune || "?"} <span class="sub">(${b.departement || "—"})</span></h3>
          <div class="sub">${b.type_bien || "bien"} · ${b.nb_chambres ?? "?"} ch · terrain ${b.surface_terrain != null ? b.surface_terrain + " m²" : "—"}</div>
          <div class="chips">${(b.features || []).slice(0, 6).map((f) => `<span class="chip">${f}</span>`).join("")}</div>
          ${b.favori_note ? `<div class="note">⭐ ${b.favori_note}</div>` : ""}
          ${starsRow(b)}
        </div>
        <div class="minimap-col">${miniMap(b)}</div>
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
    const mm = card.querySelector(".minimap[data-lat]");
    if (mm) bindMiniMap(mm);
    card.addEventListener("click", (e) => {
      if (e.target.closest(".stars") || e.target.closest(".minimap")) return;
      openModal(b);
    });
  });
}

// ---------- Mini-carte (tuiles OSM statiques, centrée, sans Leaflet) ----------
function tileMapHTML(lat, lon, w, h, z) {
  const n = 2 ** z;
  const latR = lat * Math.PI / 180;
  const gx = (lon + 180) / 360 * n * 256;
  const gy = (1 - Math.log(Math.tan(latR) + 1 / Math.cos(latR)) / Math.PI) / 2 * n * 256;
  const left0 = gx - w / 2, top0 = gy - h / 2;   // coin haut-gauche pour centrer le point
  let imgs = "";
  for (let tx = Math.floor(left0 / 256); tx <= Math.floor((left0 + w) / 256); tx++) {
    for (let ty = Math.floor(top0 / 256); ty <= Math.floor((top0 + h) / 256); ty++) {
      if (ty < 0 || ty >= n) continue;
      const sx = tx * 256 - left0, sy = ty * 256 - top0;
      const txx = ((tx % n) + n) % n;
      imgs += `<img class="tile" alt="" loading="lazy" src="https://tile.openstreetmap.org/${z}/${txx}/${ty}.png" style="left:${sx}px;top:${sy}px">`;
    }
  }
  return `<div class="staticmap" style="width:${w}px;height:${h}px">${imgs}<span class="mm-dot"></span></div>`;
}
function miniMap(b) {
  if (b.latitude == null || b.longitude == null) {
    return `<div class="minimap minimap-empty" title="localisation indisponible">📍<span>n/c</span></div>`;
  }
  return `<div class="minimap" data-lat="${b.latitude}" data-lon="${b.longitude}" title="Maintenir pour agrandir">
    ${tileMapHTML(b.latitude, b.longitude, 104, 92, 12)}
    <span class="mm-hint">⤢</span>
  </div>`;
}
function bindMiniMap(mm) {
  // Appui MAINTENU (~350 ms) pour agrandir : un tap court ou un scroll ne déclenche rien.
  let timer = null, sx = 0, sy = 0, opened = false;
  const cancel = () => { if (timer) { clearTimeout(timer); timer = null; } };
  mm.addEventListener("pointerdown", (e) => {
    e.stopPropagation();
    sx = e.clientX; sy = e.clientY; opened = false;
    timer = setTimeout(() => { timer = null; opened = true; showBigMap(+mm.dataset.lat, +mm.dataset.lon); }, 350);
  });
  mm.addEventListener("pointermove", (e) => {
    if (timer && (Math.abs(e.clientX - sx) > 8 || Math.abs(e.clientY - sy) > 8)) cancel(); // c'est un scroll
  });
  mm.addEventListener("pointerup", cancel);
  mm.addEventListener("pointercancel", cancel);
  mm.addEventListener("pointerleave", cancel);
  // n'ouvre pas la fiche (clic = relâche d'un appui maintenu ou tap court)
  mm.addEventListener("click", (e) => { if (opened) { e.stopPropagation(); e.preventDefault(); } });
  mm.addEventListener("contextmenu", (e) => e.preventDefault());
}
function showBigMap(lat, lon) {
  hideBigMap();
  const w = Math.min(window.innerWidth * 0.92, 560);
  const h = Math.min(window.innerHeight * 0.6, 460);
  const el = document.createElement("div");
  el.id = "mapPopup";
  el.innerHTML = `<div class="bigmap-card">${tileMapHTML(lat, lon, Math.round(w), Math.round(h), 14)}
    <div class="bigmap-cap">Relâche pour fermer</div></div>`;
  document.body.appendChild(el);
}
function hideBigMap() { const el = $("#mapPopup"); if (el) el.remove(); }

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

// Construit les lignes d'une section (Match / Investissement / Risques) :
// { globalKey, globalLabel, globalAlgo, rows:[{key,label,algo}] }.
function sectionData(bien, section) {
  if (section === "match") {
    const set = SET_BY_ID[String(currentSetId)] || {};
    const sb = (bien.scores_by_set || {})[String(currentSetId)] || {};
    const algoBy = {};
    for (const d of (sb.details || [])) algoBy[d.label || d.kind] = d;
    const rows = (set.preferences || []).map((p) => {
      const label = p.label || p.kind;
      const d = algoBy[label];
      const algo = d ? (d.subscore != null ? Math.round(d.subscore * 100) + "%" : d.status) : "—";
      return { key: label, label, algo };
    });
    return { globalKey: Votes.OVERALL, globalLabel: `Score global (set ${set.name || "—"})`, globalAlgo: fix1(sb.match_score), rows };
  }
  if (section === "invest") {
    const rows = (bien.score_details || []).map((p) => ({
      key: "invest:" + (p.label || p.key), label: p.label || p.key,
      algo: p.score != null ? fix1(p.score) : "—",
    }));
    return { globalKey: "invest__global", globalLabel: "Score global", globalAlgo: fix1(bien.score), rows };
  }
  // risques
  const risks = (bien.risques || []).map((r) => (typeof r === "string" ? { label: r } : r));
  const rows = risks.map((r) => {
    const label = r.label || r.type || r.nom || JSON.stringify(r);
    const algo = r.niveau != null ? String(r.niveau) : (r.level != null ? String(r.level) : "⚠️");
    return { key: "risk:" + label, label, algo };
  });
  return {
    globalKey: "risk__global",
    globalLabel: risks.length ? "Risque global" : "Risque global (aucun signalé)",
    globalAlgo: risks.length ? String(risks.length) : "0", rows,
  };
}

// Tableau générique d'une section : colonnes = Algo + une par personne ;
// lignes = score global puis un sous-critère par ligne. La colonne de l'utilisateur
// courant est interactive (étoiles), les autres affichent leur note.
function voteTable(bien, section) {
  const id = voteKey(bien);
  const d = sectionData(bien, section);
  const persons = Votes.users;
  const head = `<tr><th>Critère</th><th class="num">Algo</th>${persons.map((u) =>
    `<th class="num pcol${u === Votes.voter ? " me" : ""}">${u}</th>`).join("")}</tr>`;
  const row = (key, label, algo, isGlobal) => {
    const cells = persons.map((u) => {
      if (u === Votes.voter) return `<td class="num">${starsWidget(id, "xs", key)}</td>`;
      const e = Votes.forBien(id, key).byUser[u];
      const has = e && typeof e.stars === "number";
      return `<td class="num">${has ? `<span class="ministars">${"★".repeat(e.stars)}</span>` : '<span class="detailtxt">·</span>'}</td>`;
    }).join("");
    const lab = isGlobal ? `<b>${label}</b>` : label;
    const alg = isGlobal ? `<b>${algo}</b>` : algo;
    return `<tr class="${isGlobal ? "global-row" : ""}"><td>${lab}</td><td class="num">${alg}</td>${cells}</tr>`;
  };
  const body = row(d.globalKey, d.globalLabel, d.globalAlgo, true) +
    d.rows.map((r) => row(r.key, r.label, r.algo, false)).join("");
  return `<div class="tablewrap"><table class="scores votegrid">${head}${body}</table></div>`;
}

// Section finale : tous les commentaires (note globale) + l'éditeur de l'utilisateur.
function commentsSection(bien) {
  const id = voteKey(bien);
  const info = Votes.forBien(id);
  const list = Votes.users.map((u) => {
    const e = info.byUser[u];
    if (!e || !e.comment) return "";
    const stars = typeof e.stars === "number" ? `<span style="color:#fbbf24">${"★".repeat(e.stars)}</span>` : "";
    return `<div class="acomment"><b>${u}</b> ${stars}
      <div class="vcomment">“${escHtml(e.comment)}”</div></div>`;
  }).filter(Boolean).join("") || `<p class="detailtxt">Aucun commentaire pour l'instant.</p>`;
  const editor = Votes.voter ? `
    <div class="comment-edit">
      <textarea id="myComment" rows="2" placeholder="Ton commentaire (optionnel)">${escHtml(info.mineComment || "")}</textarea>
      <div class="comment-actions"><span id="commentMsg" class="detailtxt"></span><button class="btn" id="saveComment">Enregistrer</button></div>
    </div>` : "";
  return `${editor}<div class="comments-list">${list}</div>`;
}

function openModal(bien) {
  if (!bien) return;
  openBien = bien;
  const card = $("#modalCard");
  // Partie statique (en-tête + photos + description) construite une fois.
  card.innerHTML = `
    <button class="modal-close" id="mclose">×</button>
    <h2>${bien.commune || "?"} <span class="sub">(${bien.departement || "—"})</span></h2>
    <div class="price" style="color:var(--accent);font-weight:700;font-size:18px">${euros(bien.prix)}</div>
    <div class="sub">${bien.type_bien || "bien"} · ${bien.nb_chambres ?? "?"} ch · ${bien.nb_pieces ?? "?"} p ·
      terrain ${bien.surface_terrain != null ? bien.surface_terrain + " m²" : "—"} ·
      ${bien.altitude != null ? Math.round(bien.altitude) + " m alt." : ""}</div>
    ${bien.is_favori ? `<div class="note">⭐ ${bien.favori_note || "favori"}</div>` : ""}

    <div class="modal-gallery galwrap" style="position:relative">${gallery(bien)}</div>
    ${bien.description ? `<p class="descr">${escHtml(htmlToText(bien.description))}</p>` : ""}

    ${infoGrid(bien)}

    ${bien.latitude != null && bien.longitude != null
      ? `<div class="section-title">Carte</div><div id="modalMap" class="modal-map"></div>`
      : ""}

    <div id="modalDynamic"></div>

    <div class="modal-actions">
      ${bien.url ? `<a class="btn" href="${bien.url}" target="_blank" rel="noopener">Voir l'annonce ↗</a>` : ""}
      <button class="btn ghost" id="mclose2">Fermer</button>
    </div>`;
  $("#modal").classList.remove("hidden");
  $("#mclose").addEventListener("click", closeModal);
  $("#mclose2").addEventListener("click", closeModal);
  // Galerie photo (flèches + points)
  const gal = card.querySelector(".gallery");
  card.querySelectorAll(".gnav").forEach((btn) =>
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      gal.scrollBy({ left: Number(btn.dataset.d) * gal.clientWidth, behavior: "smooth" });
    }));
  if (gal) gal.addEventListener("scroll", () => {
    const i = Math.round(gal.scrollLeft / gal.clientWidth);
    card.querySelectorAll(".dots i").forEach((dd, k) => dd.classList.toggle("on", k === i));
  });
  // Carte interactive (pan/zoom) centrée sur le bien.
  if (modalMapInstance) { modalMapInstance.remove(); modalMapInstance = null; }
  if (bien.latitude != null && bien.longitude != null) {
    modalMapInstance = L.map("modalMap").setView([bien.latitude, bien.longitude], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(modalMapInstance);
    L.circleMarker([bien.latitude, bien.longitude], { radius: 9, color: "#04210f", weight: 2, fillColor: "#f87171", fillOpacity: .95 }).addTo(modalMapInstance);
    setTimeout(() => { if (modalMapInstance) modalMapInstance.invalidateSize(); }, 80);
  }
  renderModalDynamic(bien);
}

// Bloc "infos clés" : champs complémentaires non déjà affichés ailleurs.
function infoGrid(bien) {
  const items = [
    ["Code postal", bien.code_postal],
    ["Surface bâtie", bien.surface_bati != null ? bien.surface_bati + " m²" : null],
    ["DPE", bien.dpe_classe],
    ["Gare la + proche", bien.rail_time_min != null ? bien.rail_time_min + " min" : null],
    ["Population commune", bien.population_commune != null ? bien.population_commune + " hab." : null],
    ["Isolement", bien.isolement_score != null ? Math.round(bien.isolement_score * 100) + " %" : null],
  ].filter(([, v]) => v != null && v !== "");
  if (!items.length) return "";
  return `<div class="infogrid">${items.map(([k, v]) =>
    `<div><span class="ig-k">${k}</span><span class="ig-v">${v}</span></div>`).join("")}</div>`;
}

// Partie dynamique (tableaux de votes + commentaires), re-rendue à chaque vote
// sans toucher aux photos/description.
function renderModalDynamic(bien) {
  const host = $("#modalDynamic");
  const pending = $("#myComment") ? $("#myComment").value : null;   // préserve la saisie en cours
  host.innerHTML = `
    <div class="section-title">Match <span class="detailtxt">— algo + ${Votes.backend === "supabase" ? "votes partagés" : "votes locaux"}</span></div>
    ${voteTable(bien, "match")}
    <div class="section-title">Investissement</div>
    ${voteTable(bien, "invest")}
    <div class="section-title">Risques</div>
    ${voteTable(bien, "risk")}
    <div class="section-title">Commentaires</div>
    ${commentsSection(bien)}`;
  if (pending != null && $("#myComment")) $("#myComment").value = pending;
  host.querySelectorAll(".star").forEach((st) => st.addEventListener("click", () => handleStar(st)));
  const saveBtn = $("#saveComment");
  if (saveBtn) saveBtn.addEventListener("click", () => {
    Votes.setComment(voteKey(bien), $("#myComment").value.trim()).then((res) => {
      const msg = $("#commentMsg");   // re-requête : le DOM dynamique a pu être reconstruit
      if (msg) msg.textContent = (res && res.ok)
        ? "✓ Commentaire enregistré" : "Échec de l'enregistrement — réessaie";
    });
  });
}
function closeModal() {
  $("#modal").classList.add("hidden"); openBien = null;
  if (modalMapInstance) { modalMapInstance.remove(); modalMapInstance = null; }
}
function refreshModal() { if (openBien && !$("#modal").classList.contains("hidden")) renderModalDynamic(openBien); }

// ---------- Votes (étoiles) ----------
const escAttr = (s) => String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
const escHtml = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
// Convertit une description HTML (annonce) en texte : <br> -> saut de ligne, tags
// supprimés, entités décodées. DOMParser n'exécute aucun script ni ne charge d'image.
function htmlToText(html) {
  const src = String(html).replace(/<br\s*\/?>/gi, "\n");
  const doc = new DOMParser().parseFromString(src, "text/html");
  return (doc.body.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
}
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
