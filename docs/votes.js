"use strict";

// Système de vote par étoiles, sans login (confiance). Deux backends :
//  - supabase : votes partagés (table `votes`, API REST PostgREST) si config présente
//  - local    : repli localStorage (par navigateur) sinon
// Un vote porte sur un (bien, votant, critère). Critère "__overall__" = note globale ;
// les autres critères (optionnels) correspondent aux préférences du set.
// Identité ("qui es-tu") mémorisée en localStorage -> sélection unique par session.
const Votes = (() => {
  const LS_VOTER = "tetard_voter";
  const LS_LOCAL = "tetard_votes_v2";   // structure imbriquée par critère
  const OVERALL = "__overall__";
  let cfg = {};
  let backend = "local";
  let cache = {};          // { bienId: { critère: { votant: stars } } }
  let users = [];
  let voter = null;
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
    backend = cfg.SUPABASE_URL && cfg.SUPABASE_ANON_KEY ? "supabase" : "local";
    await reload();
  }

  function put(id, crit, who, stars) {
    ((cache[id] ||= {})[crit] ||= {})[who] = stars;
  }

  async function reload() {
    if (backend === "supabase") {
      try {
        const r = await fetch(`${cfg.SUPABASE_URL}/rest/v1/votes?select=bien_id,voter,stars,criterion`, { headers: hdr() });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const rows = await r.json();
        cache = {};
        for (const row of rows) put(row.bien_id, row.criterion || OVERALL, row.voter, row.stars);
      } catch (e) {
        console.warn("[votes] chargement Supabase échoué, repli local lecture seule :", e.message);
      }
    } else {
      try { cache = JSON.parse(localStorage.getItem(LS_LOCAL) || "{}"); }
      catch { cache = {}; }
    }
    emit();
  }

  function forBien(id, criterion) {
    const by = ((cache[id] || {})[criterion || OVERALL]) || {};
    const vals = Object.values(by);
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
    return { byUser: by, avg, count: vals.length, mine: voter ? (by[voter] ?? null) : null };
  }

  function setMine(id, stars, criterion) {
    if (!voter) return Promise.resolve();
    const crit = criterion || OVERALL;
    put(id, crit, voter, stars);
    emit();  // optimiste : l'UI reflète immédiatement
    if (backend === "supabase") {
      return fetch(`${cfg.SUPABASE_URL}/rest/v1/votes?on_conflict=bien_id,voter,criterion`, {
        method: "POST",
        headers: { ...hdr(), Prefer: "resolution=merge-duplicates,return=minimal" },
        body: JSON.stringify({ bien_id: id, voter, criterion: crit, stars, updated_at: new Date().toISOString() }),
      }).then((r) => { if (!r.ok) console.warn("[votes] enregistrement échoué :", r.status); });
    }
    localStorage.setItem(LS_LOCAL, JSON.stringify(cache));
    return Promise.resolve();
  }

  function setVoter(v) { voter = v; localStorage.setItem(LS_VOTER, v); emit(); }
  function emit() { listeners.forEach((f) => f()); }
  function onChange(f) { listeners.push(f); }

  return {
    init, reload, forBien, setMine, setVoter, onChange,
    OVERALL,
    get voter() { return voter; },
    get users() { return users; },
    get backend() { return backend; },
  };
})();
window.Votes = Votes;
