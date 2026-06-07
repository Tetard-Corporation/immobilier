"use strict";

// Snapshot statique produit par le backend (app.services.export_static).
let DATA = null;
let SETS = [];        // [{id,name,parent_id,preferences:[...]}]
let SET_BY_ID = {};
let currentSetId = null;
let map = null, markerLayer = null;
let openBien = null;   // bien actuellement ouvert dans la modale (pour rafraîchir les votes)
let modalMapInstance = null;   // carte Leaflet interactive de la fiche
let openCrit = null;           // critère ouvert dans le popup (vote/commentaire par critère)

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
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { if (openCrit) closeCritPopup(); else { closeModal(); closeIdentityIfAllowed(); } } });
  // Relâche du clic/doigt n'importe où -> ferme la grande carte (appui maintenu).
  ["pointerup", "pointercancel"].forEach((ev) => window.addEventListener(ev, hideBigMap));

  // Votes (étoiles) : init backend + identité de session.
  await Votes.init(window.APP_CONFIG || {});
  Votes.onChange(() => { render(); if (openBien) refreshModal(); if (openCrit) renderCritPopup(); });
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
    // Favoris : perso (Supabase) si identifié, sinon repli sur les favoris curatés du dataset.
    if (favOnly) {
      const fav = Votes.voter ? Votes.isFavori(voteKey(b)) : b.is_favori;
      if (!fav) return false;
    }
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
  return `<div class="badges">${parts.join("")}</div>`;
}
function favBtn(b) {
  const id = voteKey(b);
  const mine = Votes.isFavori && Votes.isFavori(id);
  const n = Votes.favCount ? Votes.favCount(id) : 0;
  return `<button class="fav-btn${mine ? " on" : ""}" data-bien="${escAttr(id)}" title="Favori" aria-label="Favori">`
    + `${mine ? "♥" : "♡"}${n > 0 ? `<span class="fav-n">${n}</span>` : ""}</button>`;
}

function renderScroll(list) {
  const root = $("#scrollView");
  root.innerHTML = list.map((b, idx) => `
    <article class="card" data-idx="${idx}">
      <div class="galwrap" style="position:relative">${gallery(b)}${badges(b)}${favBtn(b)}</div>
      <div class="body">
        <div class="body-main">
          <div class="price">${euros(b.prix)}</div>
          <h3>${b.commune || "?"} <span class="sub">(${b.departement || "—"})</span></h3>
          <div class="sub">${b.type_bien || "bien"} · ${b.nb_chambres ?? "?"} ch · terrain ${b.surface_terrain != null ? b.surface_terrain + " m²" : "—"}</div>
          <div class="chips">${(b.features || []).slice(0, 6).map((f) => `<span class="chip">${featLabel(f)}</span>`).join("")}</div>
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
    const fb = card.querySelector(".fav-btn");
    if (fb) fb.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!Votes.voter) { openIdentity(); return; }
      Votes.toggleFavori(fb.dataset.bien);   // emit -> onChange -> render (le cœur se met à jour)
    });
    card.addEventListener("click", (e) => {
      if (e.target.closest(".stars") || e.target.closest(".minimap") || e.target.closest(".fav-btn")) return;
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

// --- libellés lisibles ---------------------------------------------------
const FEATURE_LABELS = {
  arbore: "Arboré", calme: "Calme", eau: "Eau (rivière/source/étang)",
  isole: "Isolé", vue: "Vue dégagée", vue_panoramique: "Vue panoramique",
  authentique: "Authentique / cachet", sans_vis_a_vis: "Sans vis-à-vis",
};
const RISK_LABELS = {
  inondation: "Inondation", remonteeNappe: "Remontée de nappe", seisme: "Séisme",
  retraitGonflementArgile: "Retrait-gonflement argiles", radon: "Radon",
  mouvementTerrain: "Mouvement de terrain", feuForet: "Feu de forêt",
  icpe: "Site industriel (ICPE)", nucleaire: "Risque nucléaire",
  pollutionSols: "Pollution des sols", ruptureBarrage: "Rupture de barrage",
  canalisationsMatieresDangereuses: "Canalisation matières dangereuses",
};
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const featLabel = (f) => FEATURE_LABELS[f] || cap(String(f).replace(/_/g, " "));
const riskLabel = (r) => RISK_LABELS[r] || cap(String(r).replace(/([A-Z])/g, " $1"));
function critLabel(key) {
  if (key === Votes.OVERALL) return "Note globale";
  if (key === "invest__global") return "Investissement global";
  if (key === "risk__global") return "Risque global";
  if (key.startsWith("invest:")) return key.slice(7);
  if (key.startsWith("risk:")) return riskLabel(key.slice(5));
  return key;
}

// --- modal: 3 sections au même format (Match / Investissement / Risques) ---
// Tout est ramené sur une échelle /5 affichée par une barre compacte uniforme.
const to5 = (x) => (x == null ? null : Math.max(0, Math.min(5, x)));
function bar5(v, kind) {
  if (v == null) return `<span class="cbar empty" title="—"></span>`;
  const pct = Math.max(6, Math.min(100, v / 5 * 100));
  return `<span class="cbar ${kind}" title="${v.toFixed(1)}/5"><i style="width:${pct}%"></i></span>`;
}

function sectionData(bien, section) {
  if (section === "match") {
    const set = SET_BY_ID[String(currentSetId)] || {};
    const sb = (bien.scores_by_set || {})[String(currentSetId)] || {};
    const algoBy = {};
    for (const d of (sb.details || [])) algoBy[d.label || d.kind] = d;
    const rows = (set.preferences || []).map((p) => {
      const label = p.label || p.kind;
      const d = algoBy[label];
      return {
        key: label, label,
        algoVal: d && d.subscore != null ? to5(d.subscore * 5) : null,
        algoDetail: d ? (d.detail ? escHtml(d.detail) : `<span class="detailtxt">${d.status}</span>`) : "",
      };
    });
    return { global: { key: Votes.OVERALL, label: `Score global (set ${set.name || "—"})`, algoVal: to5(sb.match_score / 20), algoDetail: "" }, rows };
  }
  if (section === "invest") {
    const rows = (bien.score_details || []).map((p) => {
      const subs = (p.subpillars || []).map((sp) =>
        `<div>${escHtml(sp.label)} — <b>${sp.subscore != null ? (sp.subscore * 5).toFixed(1) : "—"}/5</b>${sp.detail ? ` <span class="detailtxt">${escHtml(sp.detail)}</span>` : ""}</div>`).join("");
      return { key: "invest:" + (p.label || p.key), label: p.label || p.key, algoVal: to5(p.score / 20), algoDetail: subs };
    });
    return { global: { key: "invest__global", label: "Score global", algoVal: to5(bien.score / 20), algoDetail: "" }, rows };
  }
  // risques (codes -> libellés ; aléa présent = barre pleine)
  const codes = (bien.risques || []).map((r) => (typeof r === "string" ? r : (r.label || r.type || JSON.stringify(r))));
  const rows = codes.map((c) => ({ key: "risk:" + c, label: riskLabel(c), algoVal: 5, algoDetail: `<span class="detailtxt">Aléa signalé sur la commune.</span>` }));
  return {
    global: {
      key: "risk__global",
      label: codes.length ? `Niveau de risque (${codes.length})` : "Aucun risque signalé",
      algoVal: codes.length ? to5(codes.length) : 0,
      algoDetail: codes.length ? codes.map(riskLabel).join(", ") : `<span class="detailtxt">Aucun aléa signalé sur la commune.</span>`,
    },
    rows,
  };
}

// Tableau : colonnes = Algo + une par personne ; lignes = score global + sous-critères.
// Cellules = barres /5 (lecture seule) ; un clic sur la ligne ouvre le popup du critère.
function voteTable(bien, section) {
  const id = voteKey(bien);
  const d = sectionData(bien, section);
  const persons = Votes.users;
  const head = `<tr><th>Critère</th><th class="num">Algo</th>${persons.map((u) =>
    `<th class="num pcol${u === Votes.voter ? " me" : ""}">${escHtml(u.slice(0, 3))}</th>`).join("")}</tr>`;
  const rowHtml = (r, isGlobal) => {
    const cells = persons.map((u) => {
      const e = Votes.forBien(id, r.key).byUser[u];
      const v = (e && typeof e.stars === "number") ? e.stars : null;
      return `<td class="num">${bar5(v, u === Votes.voter ? "me" : "user")}</td>`;
    }).join("");
    const lab = isGlobal ? `<b>${r.label}</b>` : r.label;
    return `<tr class="critrow${isGlobal ? " global-row" : ""}" data-section="${section}" data-key="${escAttr(r.key)}">
      <td>${lab} <span class="critmore">›</span></td>
      <td class="num">${bar5(r.algoVal, section === "risk" ? "risk" : "algo")}</td>${cells}</tr>`;
  };
  return `<div class="tablewrap"><table class="scores votegrid">${head}${rowHtml(d.global, true)}${d.rows.map((r) => rowHtml(r, false)).join("")}</table></div>`;
}

// Section finale : tous les commentaires du bien, tous critères confondus (lecture).
function allCommentsSection(bien) {
  const all = (Votes.allComments ? Votes.allComments(voteKey(bien)) : []);
  if (!all.length) return `<p class="detailtxt">Aucun commentaire. Clique un critère pour en laisser un.</p>`;
  return all.map((c) => {
    const st = typeof c.stars === "number" ? `<span class="ministars">${"★".repeat(c.stars)}</span>` : "";
    return `<div class="acomment"><b>${c.voter}</b> · <span class="detailtxt">${critLabel(c.criterion)}</span> ${st}
      <div class="vcomment">“${escHtml(c.comment)}”</div></div>`;
  }).join("");
}

// --- popup d'un critère : détail algo + vote + commentaire + commentaires du critère ---
function openCritPopup(bien, section, key) {
  openCrit = { bien, section, key };
  let el = document.getElementById("critPopup");
  if (!el) {
    el = document.createElement("div");
    el.id = "critPopup";
    el.innerHTML = `<div class="crit-backdrop"></div><div class="crit-card" id="critCard"></div>`;
    document.body.appendChild(el);
    el.querySelector(".crit-backdrop").addEventListener("click", closeCritPopup);
  }
  renderCritPopup();
}
function renderCritPopup() {
  if (!openCrit) return;
  const card = document.getElementById("critCard");
  if (!card) return;
  const { bien, section, key } = openCrit;
  const id = voteKey(bien);
  const d = sectionData(bien, section);
  const row = key === d.global.key ? d.global : (d.rows.find((r) => r.key === key) || { label: critLabel(key), algoVal: null, algoDetail: "" });
  const info = Votes.forBien(id, key);
  const comments = Votes.users.map((u) => {
    const e = info.byUser[u];
    if (!e || !e.comment) return "";
    const st = typeof e.stars === "number" ? `<span class="ministars">${"★".repeat(e.stars)}</span>` : "";
    return `<div class="acomment"><b>${u}</b> ${st}<div class="vcomment">“${escHtml(e.comment)}”</div></div>`;
  }).filter(Boolean).join("") || `<p class="detailtxt">Aucun commentaire sur ce critère.</p>`;
  const pending = document.getElementById("critComment") ? document.getElementById("critComment").value : null;
  const editor = Votes.voter
    ? `<div class="comment-edit"><textarea id="critComment" rows="2" placeholder="Ton commentaire sur ce critère (optionnel)">${escHtml(info.mineComment || "")}</textarea>
        <div class="comment-actions"><span id="critMsg" class="detailtxt"></span><button class="btn" id="critSave">Enregistrer</button></div></div>`
    : `<p class="detailtxt">Choisis ton identité (en haut) pour voter et commenter.</p>`;
  card.innerHTML = `
    <button class="modal-close" id="critClose">×</button>
    <h3>${row.label}</h3>
    <div class="myvote"><span>Algo</span> ${bar5(row.algoVal, section === "risk" ? "risk" : "algo")}
      <span class="detailtxt">${row.algoVal != null ? row.algoVal.toFixed(1) + "/5" : "—"}</span></div>
    ${row.algoDetail ? `<div class="algo-detail">${row.algoDetail}</div>` : ""}
    <div class="myvote"><span>Ta note</span> ${starsWidget(id, "big", key)}</div>
    ${editor}
    <div class="section-title">Commentaires du critère</div>${comments}`;
  if (pending != null) { const ta = document.getElementById("critComment"); if (ta) ta.value = pending; }
  card.querySelector("#critClose").addEventListener("click", closeCritPopup);
  card.querySelectorAll(".star").forEach((st) => st.addEventListener("click", () => handleStar(st)));
  const cs = document.getElementById("critSave");
  if (cs) cs.addEventListener("click", () => {
    Votes.setComment(id, document.getElementById("critComment").value.trim(), key).then((res) => {
      const ta = document.getElementById("critComment"); if (ta) ta.value = "";   // champ vidé = enregistré
      const m = document.getElementById("critMsg"); if (m) m.textContent = (res && res.ok) ? "✓ Enregistré" : "Échec — réessaie";
    });
  });
}
function closeCritPopup() { openCrit = null; const el = document.getElementById("critPopup"); if (el) el.remove(); }

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
    ["Population commune", bien.population_commune != null ? bien.population_commune + " hab." : null],
    ["Isolement", bien.isolement_score != null ? Math.round(bien.isolement_score * 100) + " %" : null],
  ].filter(([, v]) => v != null && v !== "");
  if (!items.length) return "";
  return `<div class="infogrid">${items.map(([k, v]) =>
    `<div><span class="ig-k">${k}</span><span class="ig-v">${v}</span></div>`).join("")}</div>`;
}

// Partie dynamique (tableaux de votes + commentaires), re-rendue à chaque vote
// sans toucher aux photos/description. Un clic sur une ligne ouvre le popup du critère.
function renderModalDynamic(bien) {
  const host = $("#modalDynamic");
  host.innerHTML = `
    <div class="section-title">Match</div>
    ${voteTable(bien, "match")}
    <div class="section-title">Investissement</div>
    ${voteTable(bien, "invest")}
    <div class="section-title">Risques</div>
    ${voteTable(bien, "risk")}
    <div class="section-title">Tous les commentaires</div>
    ${allCommentsSection(bien)}`;
  host.querySelectorAll(".critrow").forEach((tr) =>
    tr.addEventListener("click", () => openCritPopup(bien, tr.dataset.section, tr.dataset.key)));
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
