"use strict";

// Snapshot statique produit par le backend (app.services.export_static).
let DATA = null;
let SETS = [];        // [{id,name,parent_id,preferences:[...]}]
let SET_BY_ID = {};
let currentSetId = null;
let map = null, markerLayer = null;
let currentMode = "scroll";
let zoneFilter = null;   // bbox {n,s,e,w} de la zone carte filtrée, ou null
let openBien = null;   // bien actuellement ouvert dans la modale (pour rafraîchir les votes)
let modalMapInstance = null;   // carte Leaflet interactive de la fiche
let openCrit = null;           // critère ouvert dans le popup (vote/commentaire par critère)
let feedDirty = false, modalDirty = false;   // re-rendus différés (perf : pas de rebuild lourd à chaque vote)
let urlEntreeModale = false;   // l'ouverture de la fiche a-t-elle empilé une entrée d'historique ?
// Lentille de pondération : AVEC QUELS POIDS le classement est calculé. "set" = ceux du
// set (défaut publié), "moi" = les miens, "groupe" = la moyenne du groupe, "u:Prénom" =
// ceux de quelqu'un d'autre. Elle ne touche ni la collecte ni les sous-scores mesurés,
// seulement leur pondération -> le classement, pas les données.
const LS_LENS = "tetard_lens";
let lens = localStorage.getItem(LS_LENS) || "set";
let lensPoids = null;      // poids effectifs de la lentille (null = ceux du set)
let lensParams = null;     // seuils personnels de la lentille (null = ceux du set)
let poidsOuvert = false;   // panneau ⚖️ ouvert

const $ = (s) => document.querySelector(s);
const euros = (n) => (n == null ? "—" : Number(n).toLocaleString("fr-FR") + " €");
const fix1 = (n) => (n == null ? "—" : Number(n).toFixed(1));
const voteKey = (b) => `${b.source}__${b.external_id}`;
// Lien partageable d'un bien : la clé de vote (source + id de l'annonce) est stable
// d'un export à l'autre, contrairement à l'id interne — un lien envoyé au groupe
// survit donc au ré-export du snapshot.
const HASH_BIEN = "#bien=";
const lienBien = (b) => location.pathname + location.search + HASH_BIEN + encodeURIComponent(voteKey(b));
function bienDuHash() {
  if (!location.hash.startsWith(HASH_BIEN)) return null;
  const cle = decodeURIComponent(location.hash.slice(HASH_BIEN.length));
  return ((DATA && DATA.biens) || []).find((b) => voteKey(b) === cle) || null;
}
// Reflète la fiche ouverte dans la barre d'adresse. Première ouverture : on EMPILE une
// entrée (le retour arrière referme la fiche, réflexe mobile) ; les suivantes la
// remplacent, pour ne pas laisser une pile d'entrées derrière soi.
function majUrlBien(bien, opts) {
  if (opts && opts.viaHistorique) return;
  const st = { bien: voteKey(bien) };
  if (urlEntreeModale) history.replaceState(st, "", lienBien(bien));
  else { history.pushState(st, "", lienBien(bien)); urlEntreeModale = true; }
}
function effacerUrlBien() {
  if (location.hash.startsWith(HASH_BIEN)) history.replaceState(null, "", location.pathname + location.search);
}
const showLoader = () => { const l = $("#loader"); if (l) l.classList.remove("hidden"); };
const hideLoader = () => { const l = $("#loader"); if (l) l.classList.add("hidden"); };
// Affiche le loader, laisse-le peindre (double rAF), puis exécute le rendu et le masque.
const withLoader = (fn) => { showLoader(); requestAnimationFrame(() => requestAnimationFrame(() => { fn(); hideLoader(); })); };

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

  // Les témoins de zone sont comptés à part : ce sont des biens publiés pour comparer
  // les massifs, pas des pépites, et les mélanger au total prêterait à confusion.
  const nTemoins = DATA.stats.n_temoins_zone || 0;
  $("#meta").innerHTML =
    `${DATA.stats.n_biens} biens${nTemoins ? ` (dont ${nTemoins} témoins de massif)` : ""}`
    + ` · ${DATA.stats.n_searches} recherches · snapshot ${new Date(DATA.generated_at).toLocaleString("fr-FR")}`
    + `<span id="lensInfo"></span>`;

  remplirMassifs();
  setSel.addEventListener("change", (e) => {
    currentSetId = e.target.value;
    remplirMassifs();
    remplirPoidsSelect();   // les poids sont propres à un set : la lentille se recharge
    if (poidsOuvert) renderPoidsPanel();   // ...et le panneau, qui montre CE set
    withLoader(render);
  });
  $("#poidsSelect").addEventListener("change", (e) => {
    lens = e.target.value;
    localStorage.setItem(LS_LENS, lens);
    majLens();
    withLoader(render);
  });
  $("#poidsBtn").addEventListener("click", ouvrirPoids);
  $("#zoneSelect").addEventListener("change", render);
  $("#sortSelect").addEventListener("change", render);
  $("#favOnly").addEventListener("change", render);
  $("#hideRated").addEventListener("change", render);
  $("#scoreMin").addEventListener("input", (e) => { $("#scoreOut").textContent = e.target.value; render(); });
  $("#prixMin").addEventListener("input", (e) => { $("#prixOut").textContent = e.target.value; render(); });
  $("#modeScroll").addEventListener("click", () => setMode("scroll"));
  $("#modeMap").addEventListener("click", () => setMode("map"));
  $("#zoneBtn").addEventListener("click", toggleZoneFilter);
  $("#modal .modal-backdrop").addEventListener("click", closeModal);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (document.getElementById("mapPopup")) closeMapPopup();
    else if (openCrit) closeCritPopup();
    else if (poidsOuvert) fermerPoids();
    else { closeModal(); closeIdentityIfAllowed(); }
  });

  // Votes (étoiles) : init backend + identité de session.
  await Votes.init(window.APP_CONFIG || {});
  // Après le chargement des votes seulement : les poids du groupe arrivent par le même
  // canal, et le menu des lentilles ne se remplit qu'une fois qu'on les a lus.
  remplirPoidsSelect();
  // Sur un vote : ne re-rendre QUE l'overlay actif ; différer le feed/modale (sinon
  // triple rebuild lourd à chaque clic -> clics perdus sur mobile).
  Votes.onChange(() => {
    // Un poids qui change (le mien ou celui d'un autre, au chargement) invalide le
    // classement AVANT tout re-rendu.
    remplirPoidsSelect();
    if (poidsOuvert) renderPoidsPanel();
    if (openCrit) { renderCritPopup(); modalDirty = true; feedDirty = true; }
    else if (openBien) { refreshModal(); feedDirty = true; }
    else if (skipNextFeedRender) { skipNextFeedRender = false; }  // favori mis à jour en place
    else { render(); }
  });
  $("#whoami").addEventListener("click", openIdentity);
  $("#identity .id-backdrop").addEventListener("click", closeIdentityIfAllowed);
  renderWhoami();
  if (!Votes.voter) openIdentity();

  // Lien partagé (#bien=...) : ouvrir la fiche visée, et le retour arrière la referme.
  window.addEventListener("popstate", () => {
    urlEntreeModale = false;
    const b = bienDuHash();
    if (b) ouvrirDepuisLien(b);
    else if (openBien) closeModal({ viaHistorique: true });
  });

  render();
  const cible = bienDuHash();
  if (cible) ouvrirDepuisLien(cible);
  hideLoader();
}

// Un lien reçu peut viser un bien absent du set affiché (le set breton ne contient pas
// les biens montagne) : on bascule sur un set qui le contient, sinon la fiche s'ouvrirait
// sans son tableau de match.
function ouvrirDepuisLien(bien) {
  if (matchOf(bien, currentSetId) == null) {
    const s = SETS.find((x) => matchOf(bien, String(x.id)) != null);
    if (s) {
      currentSetId = String(s.id);
      $("#setSelect").value = currentSetId;
      remplirMassifs();
      remplirPoidsSelect();
      render();
    }
  }
  openModal(bien, { viaHistorique: true });
}

// Le sélecteur de massif est reconstruit à chaque changement de set : les zones sont
// déclarées par le set, et le set breton n'en a pas.
function remplirMassifs() {
  const sel = $("#zoneSelect");
  const zones = [...new Set((DATA.biens || [])
    .filter((b) => matchOf(b, currentSetId) != null && b.zone)
    .map((b) => b.zone))].sort((a, b) => a.localeCompare(b, "fr"));
  sel.innerHTML = `<option value="">Tous</option>`
    + zones.map((z) => `<option value="${z}">${z}</option>`).join("");
  sel.value = "";
  sel.closest(".ctl").classList.toggle("hidden", zones.length === 0);
}

// --- lentille de pondération --------------------------------------------
const setCourant = () => SET_BY_ID[String(currentSetId)] || null;
const lensId = () => `${lens}|${currentSetId}|${Votes.voter || ""}`;
function lensNom() {
  if (lens === "moi") return "tes poids";
  if (lens === "groupe") return "les poids du groupe";
  if (lens.startsWith("u:")) return `les poids de ${lens.slice(2)}`;
  return "les poids du set";
}
// Recalcule les poids effectifs de la lentille et jette le classement mémorisé.
function majLens() {
  const set = setCourant();
  lensPoids = null;
  lensParams = null;
  if (set && lens !== "set") {
    if (lens === "groupe") {
      lensPoids = Poids.groupe(set, Votes.users);
      lensParams = Poids.paramsGroupe(set, Votes.users);
    } else {
      const qui = lens === "moi" ? Votes.voter : lens.slice(2);
      if (qui) {
        lensPoids = Poids.pour(set, qui);
        lensParams = Poids.paramsPour(set, qui);
      }
    }
  }
  Poids.invalider();
}
// Le menu ne propose que des lentilles qui existent : « groupe » et « poids de X »
// n'apparaissent qu'une fois que quelqu'un a réglé ses poids sur ce set.
function remplirPoidsSelect() {
  const sel = $("#poidsSelect");
  if (!sel) return;
  const set = setCourant();
  const regles = set ? Poids.participants(set, Votes.users) : [];
  const opts = [`<option value="set">Set (défaut)</option>`, `<option value="moi">Mes poids</option>`];
  if (regles.length) opts.push(`<option value="groupe">Groupe (moyenne)</option>`);
  for (const u of regles) {
    if (u === Votes.voter) continue;
    opts.push(`<option value="u:${escAttr(u)}">Poids de ${escHtml(u)}</option>`);
  }
  const html = opts.join("");
  if (sel.innerHTML !== html) sel.innerHTML = html;
  if (![...sel.options].some((o) => o.value === lens)) lens = "set";
  sel.value = lens;
  majLens();
}

// --- score helpers ------------------------------------------------------
function matchOf(bien, setId) {
  const s = (bien.scores_by_set || {})[String(setId)];
  if (!s || s.match_score == null) return null;
  // Lentille active : mêmes sous-scores mesurés, autres poids -> autre match. Un bien
  // dont aucun critère pesé n'est mesuré redevient non classé (même règle qu'au backend).
  if (lensPoids && String(setId) === String(currentSetId)) {
    return Poids.matchMemo(bien, SET_BY_ID[String(setId)], lensPoids, lensParams, lensId());
  }
  return s.match_score;
}
// Score financier = pilier « Prix & opportunité » du score d'investissement (niveau de
// prix au m², écart au marché local, baisse constatée). null tant qu'aucun de ses
// sous-piliers n'est calculable (pas de prix, pas de surface, pas de comparables).
function prixScoreOf(bien) {
  const p = (bien.score_details || []).find((x) => x.key === "prix");
  return p && p.score != null ? p.score : null;
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
  const hideRated = $("#hideRated").checked;
  const min = Number($("#scoreMin").value);
  const minPrix = Number($("#prixMin").value);
  const sortMode = $("#sortSelect").value;
  let list = (DATA.biens || []).filter((b) => {
    // Appartenance au set : on ne montre (liste ET carte) que les biens rattachés au
    // set courant (= qui ont un score pour ce set). Ainsi le set "Pauline" (Finistère)
    // n'affiche pas les biens montagne de têtard, et inversement.
    if (matchOf(b, currentSetId) == null) return false;
    // Massif : sert à comparer les régions entre elles (« qu'est-ce que 250 k€ donnent
    // en Tarentaise, dans le Queyras, dans l'Ubaye ? »).
    const massif = $("#zoneSelect").value;
    if (massif && b.zone !== massif) return false;
    // Favoris : perso (Supabase) si identifié, sinon repli sur les favoris curatés du dataset.
    if (favOnly) {
      const fav = Votes.voter ? Votes.isFavori(voteKey(b)) : b.is_favori;
      if (!fav) return false;
    }
    // Masquer les biens que l'utilisateur courant a déjà notés.
    if (hideRated && Votes.hasRated(voteKey(b))) return false;
    // Filtre "zone carte" : ne garder que les biens dans la bbox capturée.
    if (!zoneInBounds(b)) return false;
    const ref = sortMode === "score" ? b.score : matchOf(b, currentSetId);
    if (min > 0 && (ref == null || ref < min)) return false;
    // Filtre "score financier" : indépendant du tri courant, il porte toujours sur le
    // pilier Prix (viser la bonne affaire, quel que soit le classement affiché).
    if (minPrix > 0) {
      const fin = prixScoreOf(b);
      if (fin == null || fin < minPrix) return false;
    }
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
  updateZoneBtn();   // garde le libellé/compteur du bouton zone à jour
  // Un classement recalculé doit se voir : sinon on croit lire le set commun.
  const li = $("#lensInfo");
  if (li) li.innerHTML = lensPoids ? ` · classé avec <b class="lenson">${lensNom()}</b>` : "";
}

function gallery(bien, _full = false) {
  const photos = bien.photos || [];
  if (!photos.length) {
    const n = bien.n_photos_source ? ` (${bien.n_photos_source} non téléchargées)` : "";
    return `<div class="gallery"><div class="nophoto">pas de photo${n}</div></div>`;
  }
  // Toutes les photos (galerie swipe). loading="lazy" -> dans une carte, seule la photo
  // visible se charge ; les suivantes au swipe. La mémoire est bornée par la
  // virtualisation CSS (content-visibility) : les cartes hors-écran ne décodent rien.
  const imgs = photos.map((p) => `<img loading="lazy" decoding="async" src="data/${p}" alt="" />`).join("");
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
  // Témoin de zone : publié parce qu'il est le meilleur de son massif, pas parce qu'il
  // tient le seuil des pépites. Sans ce badge, son score bas passerait pour une erreur.
  if (bien.zone_temoin) parts.push(`<span class="badge temoin" title="Meilleur bien de ce massif à ce budget — publié pour la comparaison entre régions, pas parce qu'il atteint le seuil">⛰ témoin</span>`);
  return `<div class="badges">${parts.join("")}</div>`;
}
function favBtn(b) {
  const id = voteKey(b);
  const mine = Votes.isFavori && Votes.isFavori(id);
  const n = Votes.favCount ? Votes.favCount(id) : 0;
  return `<button class="fav-btn${mine ? " on" : ""}" data-bien="${escAttr(id)}" title="Favori" aria-label="Favori">`
    + `${mine ? "♥" : "♡"}${n > 0 ? `<span class="fav-n">${n}</span>` : ""}</button>`;
}
// Le cœur est mis à jour EN PLACE (feed comme fiche) : il vit dans une partie qui n'est
// pas re-rendue au vote, et un rebuild complet ferait sauter le scroll.
function majFavBtn(btn) {
  const id = btn.dataset.bien;
  const mine = Votes.isFavori(id);
  const n = Votes.favCount ? Votes.favCount(id) : 0;
  btn.classList.toggle("on", mine);
  btn.innerHTML = `${mine ? "♥" : "♡"}${n > 0 ? `<span class="fav-n">${n}</span>` : ""}`;
}

// Rendu incrémental : on n'injecte qu'un lot de cartes, puis on étend au scroll.
// Sans ça, ~1700 images + 147 mini-cartes seraient construites d'un coup -> l'app rame.
const FEED_BATCH = 18;
let feedList = [];
let feedShown = 0;
let feedObserver = null;
let skipNextFeedRender = false;

function cardHTML(b, idx) {
  return `
    <article class="card" data-idx="${idx}">
      <div class="galwrap${(b.photos || []).length ? " has-photos" : ""}" style="position:relative">${gallery(b)}${badges(b)}${favBtn(b)}</div>
      <div class="body">
        <div class="body-main">
          <div class="price">${euros(b.prix)}</div>
          <h3>${b.commune || "?"} <span class="sub">(${b.departement || "—"})</span></h3>
          <div class="sub">${b.zone ? `⛰ ${b.zone} · ` : ""}${b.type_bien || "bien"} · ${faits(b)}</div>
          <div class="chips">${(b.features || []).slice(0, 6).map((f) => `<span class="chip">${featLabel(f)}</span>`).join("")}</div>
          ${starsRow(b)}
        </div>
        <div class="minimap-col">${miniMap(b)}</div>
      </div>
    </article>`;
}

function bindCard(card, b) {
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
  // Feed : cliquer une étoile pré-enregistre la note ET ouvre le popup (note + commentaire).
  const vr = card.querySelector(".voterow");
  if (vr) {
    vr.querySelectorAll(".star").forEach((st) => st.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!Votes.voter) { openIdentity(); return; }
      openCritPopup(b, "match", Votes.OVERALL);
      Votes.setMine(voteKey(b), Number(st.dataset.v), Votes.OVERALL);   // pré-enregistre
    }));
    vr.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!Votes.voter) { openIdentity(); return; }
      openCritPopup(b, "match", Votes.OVERALL);
    });
  }
  const mm = card.querySelector(".minimap[data-lat]");
  if (mm) bindMiniMap(mm);
  const fb = card.querySelector(".fav-btn");
  if (fb) fb.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!Votes.voter) { openIdentity(); return; }
    // Hors filtre "Favoris", on met à jour le cœur EN PLACE (pas de rebuild du feed,
    // qui ferait sauter le scroll avec beaucoup de biens).
    const inPlace = !$("#favOnly").checked;
    if (inPlace) skipNextFeedRender = true;
    Votes.toggleFavori(fb.dataset.bien);   // emit synchrone -> onChange
    if (inPlace) majFavBtn(fb);
  });
  card.addEventListener("click", (e) => {
    if (e.target.closest(".voterow") || e.target.closest(".minimap") || e.target.closest(".fav-btn")) return;
    openModal(b);
  });
}

function appendFeedBatch() {
  const root = $("#scrollView");
  const sentinel = root.querySelector(".scroll-sentinel");
  const frag = document.createDocumentFragment();
  const tpl = document.createElement("template");
  const end = Math.min(feedShown + FEED_BATCH, feedList.length);
  for (let idx = feedShown; idx < end; idx++) {
    tpl.innerHTML = cardHTML(feedList[idx], idx).trim();
    const card = tpl.content.firstElementChild;
    bindCard(card, feedList[idx]);
    frag.appendChild(card);
  }
  feedShown = end;
  if (sentinel) root.insertBefore(frag, sentinel); else root.appendChild(frag);
  if (feedShown >= feedList.length && sentinel) {
    if (feedObserver) feedObserver.disconnect();
    sentinel.remove();
  }
}

function renderScroll(list) {
  const root = $("#scrollView");
  if (feedObserver) { feedObserver.disconnect(); feedObserver = null; }
  feedList = list;
  feedShown = 0;
  if (!list.length) {
    root.innerHTML = `<p class="meta">Aucun bien ne correspond aux filtres.</p>`;
    return;
  }
  root.innerHTML = `<div class="scroll-sentinel" aria-hidden="true"></div>`;
  appendFeedBatch();
  const sentinel = root.querySelector(".scroll-sentinel");
  if (sentinel) {
    feedObserver = new IntersectionObserver((entries) => {
      if (entries.some((en) => en.isIntersecting)) appendFeedBatch();
    }, { root: null, rootMargin: "1000px 0px" });
    feedObserver.observe(sentinel);
  }
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
  // Simple clic -> popup carte navigable (n'ouvre pas la fiche).
  mm.addEventListener("click", (e) => { e.stopPropagation(); openMapPopup(+mm.dataset.lat, +mm.dataset.lon); });
}

let popupMapInstance = null;
function openMapPopup(lat, lon) {
  closeMapPopup();
  const el = document.createElement("div");
  el.id = "mapPopup";
  el.innerHTML = `<div class="map-backdrop"></div>
    <div class="map-card"><button class="modal-close" id="mapClose">×</button><div id="popupMap" class="popup-map"></div></div>`;
  document.body.appendChild(el);
  el.querySelector(".map-backdrop").addEventListener("click", closeMapPopup);
  el.querySelector("#mapClose").addEventListener("click", closeMapPopup);
  popupMapInstance = L.map("popupMap").setView([lat, lon], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(popupMapInstance);
  L.circleMarker([lat, lon], { radius: 9, color: "#04210f", weight: 2, fillColor: "#f87171", fillOpacity: .95 }).addTo(popupMapInstance);
  setTimeout(() => { if (popupMapInstance) popupMapInstance.invalidateSize(); }, 60);
}
function closeMapPopup() {
  if (popupMapInstance) { popupMapInstance.remove(); popupMapInstance = null; }
  const el = document.getElementById("mapPopup"); if (el) el.remove();
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
    const mk = L.marker([b.latitude, b.longitude], { icon: scoreIcon(m) });
    // Photo principale : le HTML du popup n'est inséré qu'à l'ouverture (Leaflet),
    // donc l'image ne se charge qu'au clic sur le marqueur (pas de surcoût mémoire).
    const photo = (b.photos && b.photos.length)
      ? `<img class="popup-photo" src="data/${b.photos[0]}" alt="" loading="lazy" decoding="async" />`
      : "";
    mk.bindPopup(
      `<b>${b.commune || "?"}</b> (${b.departement || "—"})<br>${euros(b.prix)}<br>` +
      `match ${fix1(m)} · invest ${fix1(b.score)}<br>` +
      photo +
      `<a href="#" onclick="window.__open(${b.id});return false;">détails →</a>`);
    mk.addTo(markerLayer);
  });
  if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 11 });
}
// Couleur graduée selon le score (cohérent partout : rouge -> orange -> ambre -> vert).
function scoreColor(p) {
  if (p == null) return "#64748b";
  if (p >= 75) return "#4ade80";
  if (p >= 60) return "#a3e635";
  if (p >= 45) return "#fbbf24";
  if (p >= 30) return "#fb923c";
  return "#f87171";
}
// Marqueur = jauge circulaire (anneau de progression) du match, avec le score au centre.
function scoreIcon(m) {
  const pct = m == null ? 0 : Math.max(0, Math.min(100, m));
  const col = scoreColor(m);
  const html = `<div class="mapscore" style="background:conic-gradient(${col} ${pct * 3.6}deg, rgba(255,255,255,.18) ${pct * 3.6}deg)">`
    + `<span>${m != null ? Math.round(m) : "–"}</span></div>`;
  return L.divIcon({ className: "mapmarker", html, iconSize: [34, 34], iconAnchor: [17, 17], popupAnchor: [0, -16] });
}
window.__open = (id) => openModal((DATA.biens || []).find((b) => b.id === id));

function setMode(mode) {
  const scroll = mode === "scroll";
  currentMode = mode;
  $("#scrollView").classList.toggle("hidden", !scroll);
  $("#mapView").classList.toggle("hidden", scroll);
  $("#modeScroll").classList.toggle("active", scroll);
  $("#modeMap").classList.toggle("active", !scroll);
  if (!scroll) { renderMap(visibleBiens()); setTimeout(() => map.invalidateSize(), 50); }
  updateZoneBtn();
}

// --- Filtre "zone carte" -------------------------------------------------
// Capture la bbox affichée sur la carte ; le mode scroll n'affiche alors que ces biens.
function zoneInBounds(b) {
  if (!zoneFilter) return true;
  if (b.latitude == null || b.longitude == null) return false;
  return b.latitude <= zoneFilter.n && b.latitude >= zoneFilter.s
      && b.longitude <= zoneFilter.e && b.longitude >= zoneFilter.w;
}

function updateZoneBtn() {
  const btn = $("#zoneBtn");
  if (!btn) return;
  if (zoneFilter) {                       // filtre actif : proposer de le retirer (dans les 2 modes)
    btn.classList.remove("hidden");
    btn.classList.add("active");
    btn.textContent = `✕ Zone filtrée (${visibleBiens().length})`;
    btn.title = "Retirer le filtre de zone";
  } else if (currentMode === "map") {     // sur la carte : proposer de filtrer la vue
    btn.classList.remove("hidden");
    btn.classList.remove("active");
    btn.textContent = "▣ Filtrer cette zone";
    btn.title = "N'afficher que les biens visibles sur la carte";
  } else {
    btn.classList.add("hidden");
  }
}

function toggleZoneFilter() {
  if (zoneFilter) {                       // retirer le filtre
    zoneFilter = null;
    updateZoneBtn();
    withLoader(() => { render(); });
    return;
  }
  if (!map) return;
  const bb = map.getBounds();             // bbox actuellement visualisée
  zoneFilter = { n: bb.getNorth(), s: bb.getSouth(), e: bb.getEast(), w: bb.getWest() };
  setMode("scroll");                      // revient au scroll avec seulement la zone
  withLoader(() => { render(); updateZoneBtn(); });
}

// --- libellés lisibles ---------------------------------------------------
const FEATURE_LABELS = {
  arbore: "Arboré", calme: "Calme", eau: "Eau (rivière/source/étang)",
  isole: "Isolé", vue: "Vue dégagée", vue_panoramique: "Vue panoramique",
  authentique: "Authentique / cachet", sans_vis_a_vis: "Sans vis-à-vis",
  cheminee: "Cheminée / poêle", terrasse: "Terrasse", garage: "Garage", piscine: "Piscine",
  jardin: "Jardin", ensoleille: "Exposé / ensoleillé",
};
// État du bâti : le volume de travaux, tel que `services/classify` le lit dans
// l'annonce. Affiché parce que le groupe le demande en premier — et parce que
// « on ne sait pas » est une réponse, pas un blanc à cacher.
const CONDITION_LABELS = {
  habitable: "Habitable", rafraichir: "À rafraîchir", renover: "À rénover",
  gros_travaux: "Gros travaux", ruine: "Ruine",
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

// --- Ligne de faits d'un bien : chambres · terrain · travaux ---------------
// Un champ vide veut dire deux choses très différentes, et la carte les affichait
// pareil (« terrain — »). Trois cas, donc : la valeur, l'absence CONSTATÉE
// (« sans terrain » : l'appartement de Morlaix n'en a pas, l'annonce le dit), et
// l'inconnu (« terrain ? »). Un champ SANS OBJET pour ce type de bien — les chambres
// d'un terrain à bâtir — ne s'affiche pas du tout : « ? ch » sur une parcelle se lit
// comme une donnée manquante alors qu'il n'y a rien à connaître.
const A_DES_PIECES = new Set(["maison", "appartement", "immeuble"]);
const conditionLabel = (b) => CONDITION_LABELS[b.condition] || (b.condition ? cap(b.condition) : null);
function faits(b, { pieces = false } = {}) {
  const out = [];
  if (A_DES_PIECES.has(b.type_bien)) {
    out.push(`${b.nb_chambres ?? "?"} ch`);
    if (pieces) out.push(`${b.nb_pieces ?? "?"} p`);
  }
  if (b.surface_terrain != null) {
    // Sur une parcelle, le type du bien est déjà écrit juste avant : « terrain ·
    // terrain 969 m² » répète le mot pour rien.
    const quoi = b.type_bien === "terrain" ? "" : "terrain ";
    out.push(b.surface_terrain === 0 ? "sans terrain"
      : `${quoi}${Number(b.surface_terrain).toLocaleString("fr-FR")} m²`);
  } else if (b.type_bien !== "appartement") {
    // Sur un appartement, l'absence de terrain est la règle : ne pas la signaler
    // comme une lacune. Sur une maison ou un terrain, elle en est une.
    out.push("terrain ?");
  }
  const etat = conditionLabel(b);
  if (etat) out.push(etat);
  return out.join(" · ");
}
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
    // Poids réellement utilisés pour le classement affiché : ceux de la lentille si
    // elle est active, ceux du set sinon. Sans ça, la fiche expliquerait un classement
    // avec les poids d'un autre calcul.
    const poids = lensPoids || Poids.defauts(set);
    const rows = (set.preferences || []).map((p) => {
      const label = p.label || p.kind;
      const d = algoBy[label];
      const id = Poids.cle(p);
      const w = poids[id];
      // Seuil personnel posé sur ce critère : la barre montre la mesure REFAITE, sinon la
      // fiche expliquerait le classement avec un chiffre qui n'est pas celui du calcul.
      let sub = d && d.subscore != null ? d.subscore : null;
      let seuil = "";
      const perso = lensParams && lensParams[id];
      if (perso && d && d.status === "ok") {
        const v = Mesures.subscore(id, bien, perso);
        if (v != null) {
          sub = v;
          const dits = Mesures.champs(id)
            .filter((ch) => perso[ch.cle] !== undefined && String(perso[ch.cle]) !== String((p.params || {})[ch.cle]))
            .map((ch) => `${ch.label} ${perso[ch.cle]}${ch.unite ? " " + ch.unite : ""}`);
          if (dits.length) seuil = `<div class="detailtxt">Seuil ${lensNom()} : ${escHtml(dits.join(", "))}</div>`;
        }
      }
      return {
        key: label, label,
        tag: `<span class="weighttag" title="Poids dans le classement (${lensNom()}) — poids par défaut : ${p.weight}">`
          + `${w > 0 ? "×" + fix1(w).replace(".0", "") : "∅"}</span>`,
        algoVal: sub != null ? to5(sub * 5) : null,
        algoDetail: (d ? (d.detail ? escHtml(d.detail) : `<span class="detailtxt">${d.status}</span>`) : "") + seuil,
      };
    });
    const match = matchOf(bien, currentSetId);
    return {
      global: {
        key: Votes.OVERALL,
        label: `Score global (set ${set.name || "—"}${lensPoids ? `, ${lensNom()}` : ""})`,
        algoVal: match != null ? to5(match / 20) : null, algoDetail: "",
      }, rows,
    };
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
    const lab = (isGlobal ? `<b>${r.label}</b>` : r.label) + (r.tag || "");
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
  // Sur un critère du set, on règle son poids là où on le juge : la note dit « ce bien
  // coche ce critère », le poids dit « ce critère compte pour moi ».
  const set = setCourant();
  const pref = section === "match" && set
    ? (set.preferences || []).find((p) => (p.label || p.kind) === key) : null;
  const couvPref = pref && pref.couverture != null
    ? ` Mesuré sur ${Math.round(pref.couverture * 100)} % du catalogue du set.` : "";
  const poidsBloc = pref && Votes.voter
    ? `<div class="myvote pw"><span>Ton poids</span>${echellePoids(Poids.cle(pref), Poids.pour(set, Votes.voter)[Poids.cle(pref)])}</div>
       <div class="detailtxt">∅ ignorer · 1 accessoire · 5 essentiel — poids par défaut : ${pref.weight}.${couvPref}</div>
       ${champsSeuil(pref, Poids.paramsPour(set, Votes.voter))}`
    : "";
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
    ${poidsBloc}
    <div class="myvote"><span>Ta note</span> ${starsWidget(id, "big", key)}</div>
    ${editor}
    <div class="section-title">Commentaires du critère</div>${comments}`;
  if (pending != null) { const ta = document.getElementById("critComment"); if (ta) ta.value = pending; }
  card.querySelector("#critClose").addEventListener("click", closeCritPopup);
  bindEchelles(card);
  bindSeuils(card);
  card.querySelectorAll(".star").forEach((st) => st.addEventListener("click", () => handleStar(st)));
  const cs = document.getElementById("critSave");
  if (cs) cs.addEventListener("click", () => {
    Votes.setComment(id, document.getElementById("critComment").value.trim(), key);  // optimiste
    closeCritPopup();   // Enregistrer -> ferme le popup
  });
}
function closeCritPopup() {
  openCrit = null;
  const el = document.getElementById("critPopup"); if (el) el.remove();
  if (openBien) { if (modalDirty) { refreshModal(); modalDirty = false; } }   // votes faits dans la fiche
  else if (feedDirty) { render(); feedDirty = false; }                         // votes faits depuis le feed
}

function openModal(bien, opts = {}) {
  if (!bien) return;
  openBien = bien;
  majUrlBien(bien, opts);   // le lien de la barre d'adresse désigne la fiche ouverte
  const card = $("#modalCard");
  // Partie statique (en-tête + photos + description) construite une fois.
  card.innerHTML = `
    <button class="modal-close" id="mclose">×</button>
    <h2>${bien.commune || "?"} <span class="sub">(${bien.departement || "—"})</span></h2>
    <div class="price" style="color:var(--accent);font-weight:700;font-size:18px">${euros(bien.prix)}</div>
    <div class="sub">${bien.type_bien || "bien"} · ${faits(bien, { pieces: true })}${
      bien.altitude != null ? ` · ${Math.round(bien.altitude)} m alt.` : ""}</div>
    <div class="modal-gallery galwrap${(bien.photos || []).length ? " has-photos" : ""}" style="position:relative">${gallery(bien, true)}${favBtn(bien)}</div>
    ${bien.description ? `<p class="descr">${escHtml(htmlToText(bien.description))}</p>` : ""}

    ${infoGrid(bien)}

    ${bien.latitude != null && bien.longitude != null
      ? `<div class="section-title">Carte</div><div id="modalMap" class="modal-map"></div>`
      : ""}

    <div id="modalDynamic"></div>

    <div class="modal-actions">
      ${bien.url ? `<a class="btn" href="${bien.url}" target="_blank" rel="noopener">Voir l'annonce ↗</a>` : ""}
      <button class="btn ghost" id="mshare" title="Copier le lien de cette fiche">🔗 Copier le lien</button>
      <button class="btn ghost" id="mclose2">Fermer</button>
    </div>`;
  $("#modal").classList.remove("hidden");
  $("#mclose").addEventListener("click", closeModal);
  $("#mclose2").addEventListener("click", closeModal);
  // Favori depuis la fiche : même geste que dans le feed, même bouton.
  const mfb = card.querySelector(".fav-btn");
  if (mfb) mfb.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!Votes.voter) { openIdentity(); return; }
    Votes.toggleFavori(mfb.dataset.bien);   // emit synchrone -> onChange (feed différé)
    majFavBtn(mfb);
  });
  // Partage : sur mobile, recopier la barre d'adresse est pénible.
  const sh = $("#mshare");
  if (sh) sh.addEventListener("click", async () => {
    const lien = location.origin + lienBien(bien);
    try { await navigator.clipboard.writeText(lien); sh.textContent = "✓ Lien copié"; }
    catch { window.prompt("Copie le lien de ce bien :", lien); }
    setTimeout(() => { if (sh.isConnected) sh.textContent = "🔗 Copier le lien"; }, 1800);
  });
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
    ["Travaux", conditionLabel(bien) || "état non précisé par l'annonce"],
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
  const id = voteKey(bien);
  const pending = $("#globalComment") ? $("#globalComment").value : null;   // préserve la saisie en cours
  // Note globale : le même vote /5 que dans le feed, posé ici à côté du commentaire —
  // on juge le bien et on dit pourquoi au même endroit.
  const infoG = Votes.forBien(id);
  const moyG = infoG.count
    ? `<span class="vavg">moyenne ${infoG.avg.toFixed(1)}/5 · ${infoG.count} vote${infoG.count > 1 ? "s" : ""}</span>`
    : `<span class="vavg">personne n'a encore noté</span>`;
  const noteG = Votes.voter
    ? `<div class="noteglobale"><span class="ng-lab">Ta note</span>${starsWidget(id, "big", Votes.OVERALL)}${moyG}</div>`
    : `<div class="noteglobale">${moyG}</div>`;
  const editor = Votes.voter
    ? `<div class="comment-edit">
        <textarea id="globalComment" rows="2" placeholder="Un commentaire général sur ce bien (optionnel)">${escHtml(Votes.forBien(id).mineComment || "")}</textarea>
        <div class="comment-actions"><span id="globalMsg" class="detailtxt"></span><button class="btn" id="saveGlobal">Enregistrer</button></div></div>`
    : `<p class="detailtxt">Choisis ton identité (en haut) pour commenter.</p>`;
  host.innerHTML = `
    <div class="section-title">Match</div>
    ${voteTable(bien, "match")}
    <div class="section-title">Investissement</div>
    ${voteTable(bien, "invest")}
    <div class="section-title">Risques</div>
    ${voteTable(bien, "risk")}
    <div class="section-title">Ta note &amp; ton commentaire</div>
    ${noteG}
    ${editor}
    <div class="section-title">Tous les commentaires</div>
    ${allCommentsSection(bien)}`;
  if (pending != null && $("#globalComment")) $("#globalComment").value = pending;
  host.querySelectorAll(".noteglobale .star").forEach((st) =>
    st.addEventListener("click", () => handleStar(st)));
  host.querySelectorAll(".critrow").forEach((tr) =>
    tr.addEventListener("click", () => openCritPopup(bien, tr.dataset.section, tr.dataset.key)));
  const sg = $("#saveGlobal");
  if (sg) sg.addEventListener("click", () => {
    Votes.setComment(id, $("#globalComment").value.trim(), Votes.OVERALL).then((res) => {
      const m = $("#globalMsg"); if (m) m.textContent = (res && res.ok) ? "✓ Commentaire global enregistré" : "Échec — réessaie";
    });
  });
}
function closeModal(opts = {}) {
  $("#modal").classList.add("hidden"); openBien = null;
  if (modalMapInstance) { modalMapInstance.remove(); modalMapInstance = null; }
  // L'URL redevient celle de la liste. Si l'ouverture avait empilé une entrée, on la
  // dépile (sinon le retour arrière rouvrirait la fiche qu'on vient de fermer).
  if (opts && opts.viaHistorique) urlEntreeModale = false;
  else if (urlEntreeModale) { urlEntreeModale = false; history.back(); }
  else effacerUrlBien();
  if (feedDirty) { render(); feedDirty = false; }   // applique les votes faits dans la fiche
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
  // Feedback visuel IMMÉDIAT (avant tout re-rendu) -> le clic ne semble jamais "raté".
  wrap.querySelectorAll(".star").forEach((s, i) => s.classList.toggle("on", i < v));
  Votes.setMine(wrap.dataset.bien, v, wrap.dataset.crit);
}

// ---------- Poids des critères : réglage perso + convergence du groupe ----------
// Un seul panneau, trois questions : ce que JE veux (l'éditeur), ce sur quoi le GROUPE
// s'accorde ou se déchire (critère par critère), et ce que ça CHANGE concrètement (les
// biens qui montent, ceux qui tombent, ceux que le groupe ne voit pas pareil).
// Rien ici ne touche la collecte : les sous-scores sont mesurés une fois par le backend,
// on ne rejoue que leur pondération.

// Échelle à 5 niveaux (1 à 5) + « ignorer » (∅), le même barème que les poids du set.
function echellePoids(key, w) {
  return `<div class="pscale" data-key="${escAttr(key)}">` + Poids.NIVEAUX.map((n) =>
    `<button class="plvl${n === w ? " on" : ""}${n === 0 ? " zero" : ""}" data-w="${n}" `
    + `title="${escAttr(Poids.LIBELLES[n])}">${n === 0 ? "∅" : n}</button>`).join("") + `</div>`;
}
// Seuil personnel d'un critère : « 4 chambres minimum » plutôt que « 3 ». N'existe que
// pour les critères dont l'entrée est exportée bien par bien (cf. mesures.js) — ailleurs,
// le sous-score ne se recalcule pas dans le navigateur et le seuil reste celui du set.
// Sur quoi porte le pourcentage : le catalogue du set quand l'export l'a calculé, la
// sélection publiée sinon. La nuance compte — 48 % de 2 873 biens n'est pas 48 % de 170.
const couvTitre = (set) => set.n_catalogue
  ? `Part du catalogue du set (${set.n_catalogue} biens) sur laquelle ce critère est réellement mesuré. `
    + `Un critère non mesuré n'est pas pénalisé : le bien est classé sans lui.`
  : `Part des biens publiés sur laquelle ce critère est réellement mesuré.`;

function champsSeuil(pref, mesPar) {
  const id = Poids.cle(pref);
  const champs = Mesures.champs(id);
  if (!champs.length || !Votes.voter) return "";
  const perso = mesPar[id] || {};
  const html = champs.map((ch) => {
    const defaut = (pref.params || {})[ch.cle];
    const val = perso[ch.cle];
    const pose = val !== undefined && String(val) !== String(defaut);
    const attrs = `data-key="${escAttr(id)}" data-champ="${escAttr(ch.cle)}" data-defaut="${escAttr(defaut ?? "")}"`;
    const champ = ch.choix
      ? `<select class="pseuil${pose ? " pose" : ""}" ${attrs}>`
        + `<option value="">${defaut ?? "—"}</option>`
        + ch.choix.map((c) => `<option value="${c}"${String(val) === c ? " selected" : ""}>${c}</option>`).join("")
        + `</select>`
      : `<input type="number" class="pseuil${pose ? " pose" : ""}" ${attrs}`
        + ` value="${val !== undefined ? escAttr(val) : ""}" placeholder="${defaut ?? ""}"`
        + ` min="${ch.min}" max="${ch.max}" step="${ch.pas}" inputmode="numeric" />`;
    return `<label class="pseuil-l" title="Défaut du set : ${defaut ?? "—"} — vide = défaut">`
      + `${escHtml(ch.label)} ${champ}${ch.unite ? `<span class="unite">${ch.unite}</span>` : ""}</label>`;
  }).join("");
  return `<div class="pparams">${html}</div>`;
}
function bindSeuils(root) {
  root.querySelectorAll(".pseuil").forEach((el) => el.addEventListener("change", (e) => {
    e.stopPropagation();
    if (!Votes.voter) { openIdentity(); return; }
    const set = setCourant();
    if (!set) return;
    const brut = el.value.trim();
    // Vide = « je reprends le seuil du set » : on efface le réglage au lieu d'inscrire
    // la valeur par défaut, pour qu'un changement du set continue de s'appliquer.
    const val = brut === "" ? null : (el.tagName === "SELECT" ? brut : Number(brut));
    Poids.definirParam(set, el.dataset.key, el.dataset.champ, val);
  }));
}

function bindEchelles(root) {
  root.querySelectorAll(".pscale").forEach((sc) => {
    sc.querySelectorAll(".plvl").forEach((btn) => btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!Votes.voter) { openIdentity(); return; }
      const set = setCourant();
      if (!set) return;
      // Retour visuel immédiat : sur mobile, un clic qui attend le re-rendu semble perdu.
      sc.querySelectorAll(".plvl").forEach((b) => b.classList.toggle("on", b === btn));
      Poids.definir(set, sc.dataset.key, Number(btn.dataset.w));
    }));
  });
}

function ouvrirPoids() {
  poidsOuvert = true;
  let el = document.getElementById("poidsPanel");
  if (!el) {
    el = document.createElement("div");
    el.id = "poidsPanel";
    el.innerHTML = `<div class="poids-backdrop"></div><div class="poids-card" id="poidsCard"></div>`;
    document.body.appendChild(el);
    el.querySelector(".poids-backdrop").addEventListener("click", fermerPoids);
  }
  renderPoidsPanel();
}
function fermerPoids() {
  poidsOuvert = false;
  const el = document.getElementById("poidsPanel");
  if (el) el.remove();
  render();
}

const PUCE = { accord: "🟢", nuance: "🟡", desaccord: "🔴", peu: "⚪" };
const MOT = { accord: "accord", nuance: "nuance", desaccord: "désaccord", peu: "1 seul avis" };
const ORDRE = { desaccord: 0, nuance: 1, accord: 2, peu: 3 };

function renderPoidsPanel() {
  const card = document.getElementById("poidsCard");
  if (!card) return;
  const set = setCourant();
  if (!set) { card.innerHTML = `<p>Aucun set sélectionné.</p>`; return; }
  const mesPoids = Poids.pour(set, Votes.voter);
  const conv = Poids.convergence(set, Votes.users);
  const convBy = Object.fromEntries(conv.map((c) => [c.key, c]));
  const part = Poids.participants(set, Votes.users);

  // --- 1. mes poids, rangés par famille ---
  const reg = (DATA && DATA.criteres) || null;
  const couv = Poids.couverture(DATA.biens || [], set);
  const mesSeuils = Poids.paramsPour(set, Votes.voter);
  const ligne = (p) => {
    const key = Poids.cle(p);
    const w = mesPoids[key];
    const c = convBy[key] || {};
    const infos = [`défaut ${p.weight}`];
    if (c.n) infos.push(`groupe ${fix1(c.moyenne)} sur ${c.n} avis`);
    // La couverture n'est pas un détail technique : un critère mesuré sur la moitié du
    // catalogue classe l'autre moitié sans lui. Signalée dès qu'elle passe sous 80 %.
    const cv = couv[key];
    const alerte = cv != null && cv < 0.8
      ? ` <span class="couv" title="${escAttr(couvTitre(set))}">mesuré sur ${Math.round(cv * 100)} % des biens</span>` : "";
    return `<div class="prow${w !== Number(p.weight) ? " modif" : ""}">
      <div class="pname" title="${escAttr(Poids.quoi(p, reg))}">${escHtml(p.label || p.kind)}<span class="detailtxt"> — ${infos.join(" · ")}</span>${alerte}</div>
      ${champsSeuil(p, mesSeuils)}
      ${echellePoids(key, w)}</div>`;
  };
  const editeur = Poids.groupes(set, reg).map((g) =>
    `<div class="pgroupe"><div class="colhead">${escHtml(g.label)}</div>${g.prefs.map(ligne).join("")}</div>`).join("");
  const bloc1 = Votes.voter
    ? `${editeur}
       <div class="comment-actions">
         <span class="detailtxt">∅ ignorer · 1 accessoire · 2 utile · 3 important · 4 très important · 5 essentiel</span>
         <button class="btn ghost" id="poidsReset">Remettre les réglages du set</button>
         <button class="btn" id="poidsAppliquer">Classer avec mes poids</button>
       </div>`
    : `<p class="detailtxt">Choisis ton identité (en haut) pour régler tes poids.</p>`;

  // --- 2. convergence ---
  // Dans cette section on nomme les critères par leur nom canonique (registre) et non
  // par le libellé du set : « Bruit (route, rail) » plutôt que « Loin d'une route
  // passante / autoroute / rail », et c'est le même mot d'un set à l'autre.
  const parId = Object.fromEntries((set.preferences || []).map((p) => [Poids.cle(p), p]));
  const nomCourt = (c) => (parId[c.key] ? Poids.court(parId[c.key], reg) : c.label);
  const chips = (c) => Object.keys(c.par)
    .sort((a, b) => c.par[b] - c.par[a])
    .map((u) => `<span class="wchip${u === Votes.voter ? " me" : ""}" title="${escAttr(u)} : ${escAttr(Poids.LIBELLES[c.par[u]])}">`
      + `${escHtml(u.slice(0, 3))}&nbsp;${c.par[u] === 0 ? "∅" : c.par[u]}</span>`).join("");
  const extremes = (c) => {
    const us = Object.keys(c.par).sort((a, b) => c.par[b] - c.par[a]);
    const h = us[0], b = us[us.length - 1];
    return `${escHtml(h)} ${c.par[h]} contre ${escHtml(b)} ${c.par[b]}`;
  };
  const nomme = (list, n) => list.slice(0, n).map((c) => escHtml(nomCourt(c))).join(", ");
  const accords = conv.filter((c) => c.statut === "accord" && c.moyenne >= 3.5).sort((a, b) => b.moyenne - a.moyenne);
  const laisses = conv.filter((c) => c.statut !== "peu" && c.moyenne != null && c.moyenne <= 1.5).sort((a, b) => a.moyenne - b.moyenne);
  const clivants = conv.filter((c) => c.statut === "desaccord").sort((a, b) => b.ecart - a.ecart);
  let resume;
  if (part.length < 2) {
    resume = `<p class="detailtxt">${part.length ? "Un seul réglage pour l'instant" : "Personne n'a encore réglé ses poids"} — `
      + `la convergence apparaît dès que vous êtes deux.</p>`;
  } else {
    const l = [];
    if (accords.length) l.push(`<li>🟢 <b>Vous voulez tous</b> ${nomme(accords, 4)}.</li>`);
    if (laisses.length) l.push(`<li>⚪ <b>Vous laissez tomber ensemble</b> ${nomme(laisses, 4)}.</li>`);
    if (clivants.length) l.push(`<li>🔴 <b>Ça coince sur</b> ${clivants.slice(0, 3).map((c) =>
      `${escHtml(nomCourt(c))} <span class="detailtxt">(${extremes(c)})</span>`).join(", ")}.</li>`);
    if (!l.length) l.push(`<li>Aucun accord ni désaccord net : vos poids se ressemblent.</li>`);
    resume = `<ul class="convsum">${l.join("")}</ul>`;
  }
  const lignesConv = [...conv]
    .sort((a, b) => (ORDRE[a.statut] - ORDRE[b.statut]) || ((b.ecart ?? -1) - (a.ecart ?? -1))
      || ((b.moyenne ?? -1) - (a.moyenne ?? -1)))
    .filter((c) => c.n > 0)
    .map((c) => `<tr>
      <td title="${escAttr(c.label)}">${escHtml(nomCourt(c))}</td>
      <td class="num">${c.defaut}</td>
      <td class="num">${fix1(c.moyenne)}</td>
      <td class="st-${c.statut}">${PUCE[c.statut]} ${MOT[c.statut]}${c.statut === "desaccord" ? ` <span class="detailtxt">(${c.etendue} d'écart)</span>` : ""}</td>
      <td>${chips(c)}</td></tr>`).join("");
  // Les seuils se lisent à part : deux personnes peuvent mettre 5 aux chambres et ne pas
  // vouloir la même maison. C'est le désaccord que le poids ne sait pas dire.
  const seuils = Poids.convergenceParams(set, Votes.users).filter((x) => x.distinctes > 0);
  const blocSeuils = seuils.length
    ? `<div class="section-title">Ce que chacun demande</div>
       <div class="tablewrap"><table class="scores conv">
       <tr><th>Critère</th><th>Seuil</th><th class="num" title="Valeur du set">Défaut</th><th>Qui demande quoi</th></tr>
       ${seuils.map((x) => `<tr>
         <td title="${escAttr(x.label)}">${escHtml(parId[x.key] ? Poids.court(parId[x.key], reg) : x.label)}</td>
         <td class="detailtxt">${escHtml(x.champ.label)}</td>
         <td class="num">${x.defaut ?? "—"}</td>
         <td>${Object.keys(x.par).sort((a, b) => (Number(x.par[b]) || 0) - (Number(x.par[a]) || 0))
            .map((u) => `<span class="wchip${u === Votes.voter ? " me" : ""}">${escHtml(u.slice(0, 3))}&nbsp;${escHtml(x.par[u])}</span>`).join("")}
           ${x.distinctes > 1 ? `<span class="st-desaccord">🔴</span>` : ""}</td></tr>`).join("")}
       </table></div>`
    : "";
  const tableConv = lignesConv
    ? `<div class="tablewrap"><table class="scores conv">
        <tr><th>Critère</th><th class="num" title="Poids par défaut du set">Défaut</th><th class="num">Groupe</th><th>Statut</th><th>Qui a dit quoi</th></tr>
        ${lignesConv}</table></div>`
    : `<p class="detailtxt">Aucun critère réglé sur ce set pour l'instant.</p>`;
  // Proximité deux à deux : qui cherche la même maison que qui.
  const prox = Poids.proximites(set, Votes.users).filter((x) => x.n >= 3);
  const blocProx = prox.length
    ? `<div class="proxlist">${prox.map((x) => `<span class="proxpair" title="${x.n} critères réglés par les deux">`
      + `${escHtml(x.a)} ↔ ${escHtml(x.b)} <b>${Math.round(x.proximite * 100)} %</b></span>`).join("")}</div>`
    : "";

  const alerteLocale = Votes.backend === "local"
    ? `<p class="detailtxt">⚠ Supabase n'est pas configuré : tes poids restent sur ce navigateur, le groupe ne les voit pas.</p>`
    : "";
  card.innerHTML = `
    <button class="modal-close" id="poidsClose">×</button>
    <h2>⚖️ Poids des critères</h2>
    <p class="sub detailtxt">Set « ${escHtml(set.name)} » · ${(set.preferences || []).length} critères.
      Les poids ne changent ni la collecte ni les mesures : ils reclassent les biens déjà là.</p>
    ${alerteLocale}
    <div class="section-title">Tes poids</div>
    ${bloc1}
    <div class="section-title">Convergence du groupe</div>
    ${resume}
    ${tableConv}
    ${blocProx}
    ${blocSeuils}`;

  card.querySelector("#poidsClose").addEventListener("click", fermerPoids);
  bindEchelles(card);
  bindSeuils(card);
  const rst = card.querySelector("#poidsReset");
  if (rst) rst.addEventListener("click", () => Poids.remettreDefauts(set));
  const app = card.querySelector("#poidsAppliquer");
  if (app) app.addEventListener("click", () => {
    lens = "moi";
    localStorage.setItem(LS_LENS, lens);
    remplirPoidsSelect();
    fermerPoids();
    withLoader(render);
  });
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

boot().catch((err) => {
  console.error("[boot] échec :", err);
  hideLoader();
  const meta = document.querySelector("#meta");
  if (meta) meta.innerHTML = `<span style="color:#f87171">Erreur de chargement : ${String(err && err.message || err)}. Réessaie (recharge la page).</span>`;
});
