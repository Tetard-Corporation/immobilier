"use strict";

// Re-mesure d'un critère avec D'AUTRES paramètres que ceux du set.
//
// Pourquoi : le poids dit combien un critère compte, il ne dit pas ce qu'on veut.
// « 2 chambres minimum » et « 4 chambres minimum » sont deux exigences différentes, et
// deux personnes qui mettent toutes les deux 5 aux chambres peuvent ne pas chercher la
// même maison. Le poids seul ne peut pas porter ce désaccord-là.
//
// Ce que ça coûte : les sous-scores exportés ont été calculés avec les paramètres DU SET.
// Changer un paramètre oblige à recalculer le sous-score dans le navigateur, donc à
// réécrire ici une formule qui vit dans `backend/app/services/preferences.py`. Deux
// copies d'une même règle finissent toujours par diverger — d'où deux garde-fous :
//
//  1. On ne porte QUE les critères dont l'entrée est exportée bien par bien (le prix, les
//     chambres, les surfaces, l'altitude, le DPE). Pas d'ensoleillement, pas de distance
//     à la gare, pas de DVF : ces mesures-là ne se rejouent pas côté client, leurs
//     paramètres restent ceux du set.
//  2. La formule portée ne sert QUE si l'utilisateur a changé le paramètre. Tant qu'il
//     garde celui du set, c'est le sous-score du backend qui fait foi — toujours.
//
// Et `tests/test_mesures.mjs` vérifie, sur tout le catalogue, que chaque formule portée
// redonne le sous-score exporté quand on lui passe les paramètres du set. Une divergence
// se voit alors tout de suite, au lieu de se découvrir dans un classement.
const Mesures = (() => {
  const clamp = (x) => Math.max(0, Math.min(1, x));
  const nb = (x) => (x == null || x === "" || isNaN(Number(x)) ? null : Number(x));

  // Paramètres réglables, par critère. `pas`/`min`/`max` bornent le champ de saisie ;
  // `label` est ce qu'on lit à côté de la valeur.
  const PARAMS = {
    budget: [
      { cle: "budget_max", label: "prix max", unite: "€", min: 10000, max: 3000000, pas: 5000 },
      { cle: "budget_min", label: "plancher", unite: "€", min: 0, max: 3000000, pas: 5000 },
    ],
    chambres_min: [{ cle: "min", label: "chambres min", unite: "", min: 1, max: 10, pas: 1 }],
    logement_compact: [{ cle: "max", label: "chambres max", unite: "", min: 1, max: 12, pas: 1 }],
    surface_habitable: [{ cle: "min", label: "m² min", unite: "m²", min: 10, max: 500, pas: 5 }],
    has_terrain: [{ cle: "min_surface", label: "terrain souhaité", unite: "m²", min: 0, max: 20000, pas: 100 }],
    jardin: [{ cle: "min_surface", label: "jardin requis", unite: "m²", min: 0, max: 5000, pas: 50 }],
    relief_mountain: [{ cle: "ref_altitude", label: "altitude de référence", unite: "m", min: 100, max: 2500, pas: 50 }],
    dpe: [{ cle: "min_classe", label: "classe minimale", unite: "", choix: ["A", "B", "C", "D", "E", "F", "G"] }],
  };

  // --- constantes reprises du backend (services/preferences.py) --------------
  const BUDGET_CONFORT = 0.70;     // sous ce ratio du budget, note pleine
  const BUDGET_LIMITE = 0.80;      // note obtenue en consommant exactement le budget
  const BUDGET_DEPASSEMENT_NUL = 0.15;   // dépassement à partir duquel la note vaut zéro
  const BUDGET_PLANCHER_MIN = 0.15;   // note à mi-plancher (un prix trop bas cache un défaut)
  const BUDGET_SOUS_PLANCHER = 0.78;  // note juste sous le plancher
  const NOTE_LIMITE = 0.75;        // logement_compact : décote entre l'idéal et la limite
  const DPE_ECHELLE = { A: 1.0, B: 0.95, C: 0.85, D: 0.70, E: 0.50, F: 0.25, G: 0.10 };

  // --- formules portées ------------------------------------------------------
  // Chacune renvoie un sous-score [0,1], ou null si la donnée manque sur ce bien
  // (même convention que le backend : non mesuré = exclu de la moyenne, pas pénalisé).
  const FORMULES = {
    budget(b, p) {
      if (b.prix == null) return null;
      const budget = nb(p.budget_max);
      if (!budget) return null;
      const plancher = nb(p.budget_min);
      if (plancher && b.prix < plancher) {
        const bas = plancher / 2;
        const part = clamp((b.prix - bas) / (plancher - bas));
        return BUDGET_PLANCHER_MIN + (BUDGET_SOUS_PLANCHER - BUDGET_PLANCHER_MIN) * part;
      }
      const ratio = b.prix / budget;
      if (ratio <= BUDGET_CONFORT) return 1.0;
      if (ratio <= 1.0) return 1.0 - (ratio - BUDGET_CONFORT) / (1.0 - BUDGET_CONFORT) * (1.0 - BUDGET_LIMITE);
      return BUDGET_LIMITE * clamp(1 - (ratio - 1.0) / BUDGET_DEPASSEMENT_NUL);
    },

    chambres_min(b, p) {
      const mn = nb(p.min) ?? 1;
      let n = b.nb_chambres;
      if (n == null && b.nb_pieces) {
        n = Math.max(1, Math.trunc(b.nb_pieces) - 1);
        // Recoupement par la surface : une annonce peut annoncer 4 pièces dans 35 m².
        const parPiece = nb(p.m2_min_par_piece) ?? 20;
        if (b.surface_bati && parPiece) {
          const tenable = Math.max(1, Math.trunc(b.surface_bati / parPiece) - 1);
          if (tenable < n) n = tenable;
        }
      }
      if (n == null && b.surface_bati) {
        const m2 = nb(p.m2_par_chambre) ?? 35;
        n = Math.max(1, Math.trunc(b.surface_bati / m2));
      }
      if (n == null) return null;
      return n >= mn ? 1.0 : clamp(n / mn);
    },

    logement_compact(b, p) {
      if (b.type_bien === "terrain") return null;
      const limite = nb(p.max) ?? 4;
      // L'idéal ne peut pas dépasser la limite : quand quelqu'un descend « chambres max »
      // sous l'idéal du set, c'est la limite qu'il vient de dire.
      const ideal = Math.min(nb(p.ideal) ?? 3, limite);
      let ch = b.nb_chambres;
      if (ch == null && b.nb_pieces) ch = Math.max(1, b.nb_pieces - 1);
      if (ch == null) {
        if (b.surface_bati == null) return null;
        const petit = nb(p.m2_ok) ?? 120, grand = nb(p.m2_max) ?? 250;
        return b.surface_bati <= petit ? 1.0 : clamp(1 - (b.surface_bati - petit) / (grand - petit));
      }
      if (ch <= ideal) return 1.0;
      if (ch <= limite) return NOTE_LIMITE;
      return NOTE_LIMITE * Math.pow(0.5, ch - limite);
    },

    surface_habitable(b, p) {
      if (b.surface_bati == null) return null;
      const mn = nb(p.min) ?? 80;
      return b.surface_bati >= mn ? 1.0 : clamp(b.surface_bati / mn);
    },

    has_terrain(b, p) {
      if (b.surface_terrain == null) return null;
      const mn = nb(p.min_surface) ?? 1;
      return b.surface_terrain >= mn ? 1.0 : clamp(b.surface_terrain / mn);
    },

    jardin(b, p) {
      const mn = nb(p.min_surface) ?? 300;
      const st = b.surface_terrain;
      if (st) return st >= mn ? 1.0 : clamp(0.35 + 0.65 * st / mn);
      if ((b.features || []).includes("jardin")) return nb(p.note_mention) ?? 0.7;
      return null;
    },

    relief_mountain(b, p) {
      // Le backend distingue « altitude jamais relevée » (pending) et « relevée sans
      // valeur » — ce second cas vaut 0, pas « non mesuré ». On reproduit ce choix, sinon
      // 211 biens du set têtard changeraient de note en passant par ici.
      if (!("altitude" in b)) return null;
      const ref = nb(p.ref_altitude) ?? 600;
      return clamp((b.altitude || 0) / ref);
    },

    // Le seul paramètre du DPE est un SEUIL, et il ne change pas le barème : en dessous
    // de la classe demandée, le bien tombe à la note de la classe suivante — la mesure
    // reste la même, c'est l'exigence qui bouge.
    dpe(b, p) {
      const classe = String(b.dpe_classe || "").trim().toUpperCase()[0];
      if (!(classe in DPE_ECHELLE)) return null;
      const base = DPE_ECHELLE[classe];
      const seuil = String(p.min_classe || "").trim().toUpperCase()[0];
      if (!(seuil in DPE_ECHELLE)) return base;
      return classe <= seuil ? base : Math.min(base, DPE_ECHELLE[seuil] * 0.5);
    },
  };

  const parametrable = (id) => Object.prototype.hasOwnProperty.call(PARAMS, id) && id in FORMULES;
  const champs = (id) => PARAMS[id] || [];
  // Paramètres du set pour ce critère, réduits aux seuls champs réglables.
  function reglages(pref) {
    const out = {};
    for (const ch of champs(pref.id || pref.kind)) {
      const v = (pref.params || {})[ch.cle];
      if (v !== undefined) out[ch.cle] = v;
    }
    return out;
  }
  // Sous-score recalculé, ou null si la donnée manque / le critère n'est pas porté.
  function subscore(id, bien, params) {
    const f = FORMULES[id];
    if (!f) return null;
    const v = f(bien, params || {});
    return v == null ? null : Math.max(0, Math.min(1, v));
  }

  return { PARAMS, parametrable, champs, reglages, subscore };
})();
window.Mesures = Mesures;
