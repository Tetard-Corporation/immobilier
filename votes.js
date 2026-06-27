"use strict";

// Système de vote par étoiles, sans login (confiance). Deux backends :
//  - supabase : votes partagés (table `votes`, API REST PostgREST) si config présente
//  - local    : repli localStorage (par navigateur) sinon
// Un vote porte sur un (bien, votant, critère) et vaut { stars, comment? }.
// Critère "__overall__" = note globale ; les autres = préférences du set.
// Le commentaire est optionnel et accompagne un vote (il faut une note).
// Identité ("qui es-tu") mémorisée en localStorage -> sélection unique par session.
const Votes = (() => {
  const LS_VOTER = "tetard_voter";
  const LS_LOCAL = "tetard_votes_v3";   // { bienId: { critère: { votant: {stars, comment} } } }
  const OVERALL = "__overall__";
  let cfg = {};
  let backend = "local";
  let cache = {};
  let users = [];
  let voter = null;
  const listeners = [];
  const errListeners = [];

  const hdr = () => ({
    apikey: cfg.SUPABASE_ANON_KEY,
    Authorization: `Bearer ${cfg.SUPABASE_ANON_KEY}`,
    "Content-Type": "application/json",
  });

  async function init(config) {
    cfg = config || {};
    users = cfg.USERS || [];
    voter = localStorage.getItem(LS_VOTER) || null;
    backend = cfg.SUPABASE_URL && cfg.SUPABASE_ANON_KEY ? "supabase" : "local";
    await reload();
  }

  function put(id, crit, who, stars, comment) {
    (((cache[id] ||= {})[crit] ||= {})[who]) = { stars, comment: comment ?? null };
  }

  async function reload() {
    if (backend === "supabase") {
      try {
        const r = await fetch(`${cfg.SUPABASE_URL}/rest/v1/votes?select=bien_id,voter,stars,criterion,comment`, { headers: hdr() });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const rows = await r.json();
        cache = {};
        for (const row of rows) put(row.bien_id, row.criterion || OVERALL, row.voter, row.stars, row.comment);
        try { localStorage.setItem(LS_LOCAL, JSON.stringify(cache)); } catch (e) { /* quota/private */ }  // miroir local
      } catch (e) {
        console.warn("[votes] chargement Supabase échoué, repli sur la copie locale :", e.message);
        try { cache = JSON.parse(localStorage.getItem(LS_LOCAL) || "{}"); } catch { cache = {}; }
        emitError("Serveur de votes injoignable — affichage de ta dernière copie locale.");
      }
    } else {
      try { cache = JSON.parse(localStorage.getItem(LS_LOCAL) || "{}"); }
      catch { cache = {}; }
    }
    emit();
  }

  function forBien(id, criterion) {
    const by = ((cache[id] || {})[criterion || OVERALL]) || {};   // { votant: {stars, comment} }
    const vals = Object.values(by).map((e) => e.stars).filter((s) => typeof s === "number");
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    const me = voter ? by[voter] : null;
    return { byUser: by, avg, count: vals.length, mine: me ? me.stars : null, mineComment: me ? me.comment : null };
  }

  // Enregistre la note (et, en option, le commentaire). Si comment === undefined,
  // on préserve le commentaire existant ; sinon on le remplace (null pour effacer).
  function setMine(id, stars, criterion, comment) {
    if (!voter) return Promise.resolve({ ok: false, reason: "no-voter" });
    const crit = criterion || OVERALL;
    const existing = ((cache[id] || {})[crit] || {})[voter];
    const com = comment !== undefined ? (comment || null) : (existing ? existing.comment : null);
    put(id, crit, voter, stars, com);
    emit();  // optimiste
    // Filet de sécurité : on garde TOUJOURS une copie locale (survit au refresh / autre
    // onglet), même en mode supabase où l'écriture distante peut échouer silencieusement.
    try { localStorage.setItem(LS_LOCAL, JSON.stringify(cache)); } catch (e) { /* quota/private */ }
    if (backend === "supabase") {
      const fail = (info) => {
        console.warn("[votes] enregistrement Supabase échoué :", info);
        emitError("Ta note n'a pas pu être enregistrée sur le serveur (gardée en local). Réessaie plus tard.");
        return { ok: false };
      };
      return fetch(`${cfg.SUPABASE_URL}/rest/v1/votes?on_conflict=bien_id,voter,criterion`, {
        method: "POST",
        headers: { ...hdr(), Prefer: "resolution=merge-duplicates,return=minimal" },
        body: JSON.stringify({ bien_id: id, voter, criterion: crit, stars, comment: com, updated_at: new Date().toISOString() }),
      }).then((r) => (r.ok ? { ok: true } : fail("HTTP " + r.status)))
        .catch((e) => fail(e.message));   // réseau/DNS down -> ne reste plus silencieux
    }
    return Promise.resolve({ ok: true });
  }

  // Commentaire indépendant de la note : on peut commenter sans noter (stars nullable).
  function setComment(id, comment, criterion) {
    if (!voter) return Promise.resolve({ ok: false, reason: "no-voter" });
    const crit = criterion || OVERALL;
    const existing = ((cache[id] || {})[crit] || {})[voter];
    const stars = existing ? existing.stars : null;
    return setMine(id, stars, crit, comment || null);
  }

  // Favoris : stockés comme un "vote" sur le critère réservé FAVORI (stars=1 = favori).
  const FAVORI = "__favori__";
  function isFavori(id) { return forBien(id, FAVORI).mine === 1; }
  function favCount(id) {
    const by = (cache[id] || {})[FAVORI] || {};
    return Object.values(by).filter((e) => e.stars === 1).length;
  }
  function toggleFavori(id) { return setMine(id, isFavori(id) ? null : 1, FAVORI); }

  // L'utilisateur courant a-t-il déjà noté ce bien (un critère quelconque, hors favori) ?
  function hasRated(id) {
    if (!voter) return false;
    const byCrit = cache[id] || {};
    return Object.keys(byCrit).some((crit) =>
      crit !== FAVORI && byCrit[crit][voter] && typeof byCrit[crit][voter].stars === "number");
  }

  // Tous les commentaires d'un bien, tous critères confondus.
  function allComments(id) {
    const out = [];
    const byCrit = cache[id] || {};
    for (const crit of Object.keys(byCrit)) {
      for (const u of Object.keys(byCrit[crit])) {
        const e = byCrit[crit][u];
        if (e && e.comment) out.push({ criterion: crit, voter: u, stars: e.stars, comment: e.comment });
      }
    }
    return out;
  }

  function setVoter(v) { voter = v; localStorage.setItem(LS_VOTER, v); emit(); }
  function emit() { listeners.forEach((f) => f()); }
  function onChange(f) { listeners.push(f); }
  function emitError(msg) { errListeners.forEach((f) => f(msg)); }
  function onError(f) { errListeners.push(f); }

  return {
    init, reload, forBien, setMine, setComment, setVoter, onChange, onError, allComments,
    isFavori, favCount, toggleFavori, hasRated, OVERALL,
    get voter() { return voter; },
    get users() { return users; },
    get backend() { return backend; },
  };
})();
window.Votes = Votes;
