"use strict";

// Système de vote par étoiles, sans login (confiance). Deux backends :
//  - supabase : votes partagés (table `votes`, API REST PostgREST) si config présente
//  - local    : repli localStorage (par navigateur) sinon
// Identité ("qui es-tu") mémorisée en localStorage -> sélection unique par session.
const Votes = (() => {
  const LS_VOTER = "tetard_voter";
  const LS_LOCAL = "tetard_votes_local";
  const LS_SECRET = "tetard_vote_secret";   // code d'accès, jamais committé (par appareil)
  let cfg = {};
  let backend = "local";
  let cache = {};          // { bienId: { voter: stars } }
  let users = [];
  let voter = null;
  let secret = null;
  const listeners = [];

  const hdr = () => ({
    apikey: cfg.SUPABASE_ANON_KEY,
    Authorization: `Bearer ${cfg.SUPABASE_ANON_KEY}`,
    "Content-Type": "application/json",
  });

  async function init(config) {
    cfg = config || {};
    users = cfg.USERS || [];
    voter = localStorage.getItem(LS_VOTER) || null;
    secret = localStorage.getItem(LS_SECRET) || null;
    backend = cfg.SUPABASE_URL && cfg.SUPABASE_ANON_KEY ? "supabase" : "local";
    await reload();
  }

  async function reload() {
    if (backend === "supabase") {
      try {
        const r = await fetch(`${cfg.SUPABASE_URL}/rest/v1/votes?select=bien_id,voter,stars`, { headers: hdr() });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const rows = await r.json();
        cache = {};
        for (const row of rows) (cache[row.bien_id] ||= {})[row.voter] = row.stars;
      } catch (e) {
        console.warn("[votes] chargement Supabase échoué, repli local lecture seule :", e.message);
      }
    } else {
      try { cache = JSON.parse(localStorage.getItem(LS_LOCAL) || "{}"); }
      catch { cache = {}; }
    }
    emit();
  }

  function forBien(id) {
    const by = cache[id] || {};
    const vals = Object.values(by);
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    return { byUser: by, avg, count: vals.length, mine: voter ? (by[voter] ?? null) : null };
  }

  function setMine(id, stars) {
    if (!voter) return Promise.resolve({ ok: false, reason: "no-voter" });

    if (backend === "supabase") {
      if (!secret) return Promise.resolve({ ok: false, reason: "no-secret" });
      const prev = (cache[id] || {})[voter];           // pour rollback si refus
      (cache[id] ||= {})[voter] = stars;
      emit();                                            // optimiste
      // Écriture interdite en direct : on passe par la fonction RPC qui valide le code.
      return fetch(`${cfg.SUPABASE_URL}/rest/v1/rpc/cast_vote`, {
        method: "POST",
        headers: hdr(),
        body: JSON.stringify({ p_bien_id: id, p_voter: voter, p_stars: stars, p_secret: secret }),
      }).then((r) => {
        if (r.ok) return { ok: true };
        if (prev == null) delete cache[id][voter]; else cache[id][voter] = prev;  // rollback
        emit();
        return { ok: false, reason: r.status === 400 || r.status === 401 || r.status === 403 ? "bad-secret" : "http" };
      }).catch(() => {
        if (prev == null) delete cache[id][voter]; else cache[id][voter] = prev;
        emit();
        return { ok: false, reason: "http" };
      });
    }

    (cache[id] ||= {})[voter] = stars;
    emit();
    localStorage.setItem(LS_LOCAL, JSON.stringify(cache));
    return Promise.resolve({ ok: true });
  }

  function setVoter(v) { voter = v; localStorage.setItem(LS_VOTER, v); emit(); }
  function setSecret(s) { secret = s || null; if (secret) localStorage.setItem(LS_SECRET, secret); else localStorage.removeItem(LS_SECRET); emit(); }
  function emit() { listeners.forEach((f) => f()); }
  function onChange(f) { listeners.push(f); }

  return {
    init, reload, forBien, setMine, setVoter, setSecret, onChange,
    get voter() { return voter; },
    get users() { return users; },
    get backend() { return backend; },
    get hasSecret() { return !!secret; },
  };
})();
window.Votes = Votes;
