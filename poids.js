"use strict";

// Pondération PERSONNELLE des critères d'un set, et lecture de ce que le groupe en dit.
//
// Le principe : la collecte et les sous-scores par critère ne bougent pas (c'est le
// backend qui les mesure, une fois pour toutes, à l'export). Ce qui bouge, c'est le
// POIDS de chaque critère dans la moyenne — donc le classement. Chacun règle les siens,
// le classement se recalcule dans le navigateur, et personne ne casse le set commun.
//
// Échelle à 5 niveaux (1 à 5, comme les poids du set), plus « ignorer » (0) pour sortir
// un critère du calcul. Un critère jamais réglé garde le poids du set.
//
// Le poids ne dit pas tout : il dit COMBIEN un critère compte, pas CE QU'ON VEUT.
// « 2 chambres minimum » et « 4 chambres minimum » sont deux exigences différentes, et
// deux personnes qui mettent 5 aux chambres ne cherchent pas forcément la même maison.
// Sur les critères dont l'entrée est exportée bien par bien, chacun peut donc aussi
// régler le SEUIL (voir mesures.js) — et là, le sous-score est recalculé.
//
// Stockage : la table `votes` existante, sous un bien fictif `__poids__:<set>` — un
// « vote » par (set, personne, critère). Aucune migration SQL à faire, et les poids sont
// partagés par le même canal que les notes. La contrainte SQL interdisant stars=0,
// « ignorer » est stocké en `stars = null` (une ligne existe : c'est un choix, pas un
// silence). Les scripts qui lisent la table doivent écarter `bien_id like '__poids__%'`.
const Poids = (() => {
  const NIVEAUX = [0, 1, 2, 3, 4, 5];
  const LIBELLES = {
    0: "Ignorer", 1: "Accessoire", 2: "Utile", 3: "Important",
    4: "Très important", 5: "Essentiel",
  };
  // Ancres du contraste, identiques au backend (services/preferences.py) : la moyenne
  // pondérée d'une vingtaine de critères se concentre au centre, on l'étire entre la
  // valeur basse et la valeur haute réellement atteignables.
  const ANCRE_BASSE = 0.20, ANCRE_HAUTE = 0.90;
  // Lignes de détail qui ne sont pas des critères pondérables : l'exigence (plafond) et
  // le disqualifiant (viager, sous compromis…) sont des verdicts, pas des préférences.
  const HORS_CRITERES = new Set(["exigence", "disqualifiant"]);

  const bienId = (setId) => `__poids__:${setId}`;

  // --- identité d'un critère ------------------------------------------------
  // C'est l'`id` du registre (backend/app/services/criteres.py), pas le libellé : le
  // libellé encode les paramètres (« Prix entre 180 000 € et 250 000 € ») et change à
  // chaque tour de table, l'id non. Les poids réglés survivent donc à une reformulation.
  // Repli pour un instantané antérieur au registre : on redérive la même identité.
  const cle = (p) => p.id
    || (p.kind === "feature" && p.params && p.params.name ? `feature:${p.params.name}` : p.kind);

  // Les lignes de détail d'un bien ne portent que kind + libellé : on les rattache aux
  // préférences du set (qui, elles, portent l'id) par leur libellé, sinon par leur kind.
  const _index = new Map();
  function index(set) {
    let ix = _index.get(set.id);
    if (!ix) {
      ix = { parLabel: {}, parKind: {} };
      for (const p of set.preferences || []) {
        const id = cle(p);
        ix.parLabel[p.label || p.kind] = id;
        if (!(p.kind in ix.parKind)) ix.parKind[p.kind] = id;
      }
      _index.set(set.id, ix);
    }
    return ix;
  }
  const idDeDetail = (d, ix) => ix.parLabel[d.label || d.kind] || ix.parKind[d.kind] || d.kind;
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const contraste = (x) => clamp01((x - ANCRE_BASSE) / (ANCRE_HAUTE - ANCRE_BASSE));
  const arrondi1 = (x) => Math.round(x * 10) / 10;

  // --- lecture des réglages ------------------------------------------------
  // Poids explicitement réglés : { critère: { personne: 0..5 } }.
  function explicites(set) {
    const out = {};
    const rows = Votes.entriesFor(bienId(set.id));
    for (const crit of Object.keys(rows)) {
      for (const u of Object.keys(rows[crit])) {
        const e = rows[crit][u];
        if (!e) continue;
        (out[crit] ||= {})[u] = e.stars == null ? 0 : Number(e.stars);
      }
    }
    return out;
  }

  // --- paramètres personnels ------------------------------------------------
  // Rangés dans le `comment` de la ligne du poids (JSON) : une ligne par (set, personne,
  // critère) porte les deux, sans colonne ni table supplémentaire.
  function parametres(set) {
    const out = {};
    const rows = Votes.entriesFor(bienId(set.id));
    for (const crit of Object.keys(rows)) {
      for (const u of Object.keys(rows[crit])) {
        const brut = rows[crit][u] && rows[crit][u].comment;
        if (!brut) continue;
        try {
          const p = JSON.parse(brut);
          if (p && typeof p === "object" && !Array.isArray(p)) (out[crit] ||= {})[u] = p;
        } catch { /* un commentaire qui n'est pas du JSON n'est pas un réglage */ }
      }
    }
    return out;
  }

  function definirParam(set, key, champ, valeur) {
    const actuel = { ...((parametres(set)[key] || {})[Votes.voter] || {}) };
    if (valeur === null || valeur === undefined || valeur === "") delete actuel[champ];
    else actuel[champ] = valeur;
    const reste = Object.keys(actuel).length ? JSON.stringify(actuel) : null;
    // Poser un seuil ne répond pas à la question du poids. Si la personne n'a pas encore
    // réglé ce critère, on réinscrit le poids du set : sans ça la ligne s'écrirait avec
    // `stars = null`, qui veut dire « ignoré » — dire « je veux 5 chambres » sortirait
    // les chambres du calcul.
    const dejaRegle = (explicites(set)[key] || {})[Votes.voter];
    const pref = (set.preferences || []).find((p) => cle(p) === key);
    const w = dejaRegle !== undefined ? dejaRegle
      : Math.max(0, Math.min(5, Math.round(Number((pref && pref.weight) ?? 1))));
    return Votes.setMine(bienId(set.id), w === 0 ? null : w, key, reste);
  }

  // Seuils effectifs d'une personne. Ne contient QUE les critères qu'elle a
  // personnalisés : partout ailleurs, c'est le sous-score du backend qui fait foi.
  function paramsPour(set, user) {
    const out = {};
    if (!user) return out;
    const ex = parametres(set);
    for (const p of (set.preferences || [])) {
      const id = cle(p);
      const perso = (ex[id] || {})[user];
      if (perso && Object.keys(perso).length) out[id] = { ...(p.params || {}), ...perso };
    }
    return out;
  }

  // Seuils du groupe : la MÉDIANE des seuils réglés (une moyenne ferait « 3,5 chambres »,
  // et un seul budget très haut déplacerait celui de tout le monde).
  function paramsGroupe(set, users) {
    const out = {};
    const ex = parametres(set);
    const part = participants(set, users);
    for (const p of (set.preferences || [])) {
      const id = cle(p);
      const dits = part.map((u) => (ex[id] || {})[u]).filter(Boolean);
      if (!dits.length) continue;
      const fusion = { ...(p.params || {}) };
      const champs = new Set(dits.flatMap((d) => Object.keys(d)));
      for (const c of champs) {
        const vals = dits.map((d) => d[c]).filter((v) => v !== undefined);
        const nums = vals.filter((v) => typeof v === "number").sort((a, b) => a - b);
        if (nums.length === vals.length && nums.length) {
          fusion[c] = nums[Math.floor((nums.length - 1) / 2)];
        } else if (vals.length) {
          fusion[c] = vals[0];
        }
      }
      out[id] = fusion;
    }
    return out;
  }

  // Poids du set : ce que tout le monde a par défaut.
  function defauts(set) {
    const out = {};
    for (const p of (set.preferences || [])) out[cle(p)] = Number(p.weight ?? 1);
    return out;
  }

  // Les personnes qui ont réglé au moins un critère sur ce set.
  function participants(set, users) {
    const ex = explicites(set);
    const vus = new Set();
    for (const c of Object.keys(ex)) for (const u of Object.keys(ex[c])) vus.add(u);
    return (users || []).filter((u) => vus.has(u));
  }

  // Poids effectifs d'une personne = poids du set, écrasés par ses réglages.
  function pour(set, user) {
    const w = defauts(set);
    if (!user) return w;
    const ex = explicites(set);
    for (const c of Object.keys(ex)) if (ex[c][user] !== undefined) w[c] = ex[c][user];
    return w;
  }

  // Profil du groupe = moyenne des poids RÉGLÉS (on ne fait pas voter les absents :
  // un critère que personne n'a touché garde le poids du set).
  function groupe(set, users) {
    const w = defauts(set);
    const ex = explicites(set);
    const part = participants(set, users);
    for (const c of Object.keys(ex)) {
      const vals = part.map((u) => ex[c][u]).filter((v) => v !== undefined);
      if (vals.length) w[c] = vals.reduce((a, b) => a + b, 0) / vals.length;
    }
    return w;
  }

  // --- écriture ------------------------------------------------------------
  // `poids` dans [0,5] ; 0 (« ignorer ») est stocké en stars=null (contrainte SQL 1..5).
  function definir(set, key, poids) {
    const w = Math.max(0, Math.min(5, Math.round(Number(poids))));
    return Votes.setMine(bienId(set.id), w === 0 ? null : w, key);
  }

  // « Remettre les réglages du set » : on RÉÉCRIT les valeurs du set au lieu d'effacer
  // les lignes — la policy Supabase n'autorise pas le DELETE, et un poids remis à sa
  // valeur par défaut reste une position assumée, pas un silence. Les seuils personnels,
  // eux, sont bel et bien effacés (comment = null) : on revient à ceux du set.
  function remettreDefauts(set) {
    return Promise.all((set.preferences || []).map((p) => {
      const w = Math.max(0, Math.min(5, Math.round(Number(p.weight ?? 1))));
      return Votes.setMine(bienId(set.id), w === 0 ? null : w, cle(p), null);
    }));
  }

  // --- recalcul du match ---------------------------------------------------
  // Reproduit `services/preferences.evaluate` : moyenne des sous-scores MESURÉS pondérée
  // par les poids, étirée entre les deux ancres, puis plafonnée par les exigences du set.
  function agrege(bien, details, set, poids, params) {
    let acc = 0, tot = 0;
    const ix = index(set);
    // Sous-scores refaits avec les seuils personnels : ils servent AUSSI aux paliers du
    // set. Dire « mon budget est 150 k€ » doit vraiment plafonner un bien à 250 k€ —
    // sinon le seuil ne pèserait que dans la moyenne, où il se rattrape ailleurs.
    const remesures = {};
    for (const d of details) {
      if (HORS_CRITERES.has(d.kind)) continue;
      if (d.status !== "ok" || d.subscore == null) continue;
      const k = idDeDetail(d, ix);
      const w = poids && poids[k] !== undefined ? Number(poids[k]) : Number(d.weight || 0);
      if (!(w > 0)) continue;   // poids 0 = critère ignoré : il sort de la moyenne
      // Seuil personnel : on re-mesure. Sinon — et c'est le cas par défaut — on garde
      // le sous-score du backend, qui reste la seule autorité.
      let sub = d.subscore;
      if (params && params[k]) {
        const remesure = Mesures.subscore(k, bien, params[k]);
        if (remesure != null) { sub = remesure; remesures[d.kind] = sub; }
      }
      acc += w * sub;
      tot += w;
    }
    if (tot <= 0) return null;
    return exigences(arrondi1(contraste(acc / tot) * 100), details, set.exigences, remesures);
  }

  // Paliers du set : au-delà d'un certain score, certains critères ne sont plus
  // optionnels (cf. `appliquer_exigences`). Le score est ramené au palier, jamais annulé.
  function exigences(score, details, exigs, remesures) {
    if (score == null || !exigs || !exigs.length) return score;
    const parKind = {};
    for (const d of details) parKind[d.kind] = d;   // le dernier gagne, comme côté Python
    for (const k of Object.keys(remesures || {})) {
      if (parKind[k]) parKind[k] = { ...parKind[k], subscore: remesures[k] };
    }
    for (const e of [...exigs].sort((a, b) => Number(a.above || 0) - Number(b.above || 0))) {
      const palier = Number(e.above || 0);
      if (score <= palier || remplie(e, parKind)) continue;
      score = palier;
    }
    return score;
  }
  function remplie(exig, parKind) {
    const seuil = exig.min_subscore != null ? Number(exig.min_subscore) : 0.5;
    let remplis = 0, manquants = 0;
    for (const k of (exig.requires || [])) {
      const d = parKind[k];
      if (d && d.status === "ok" && (d.subscore || 0) >= seuil) remplis++;
      else manquants++;   // un critère non mesuré ne peut pas valider une exigence
    }
    return (exig.mode || "any") === "all" ? manquants === 0 : remplis > 0;
  }

  // Pénalité des biens déclassés (viager, sous compromis, mobil-home…) : le facteur
  // n'est pas exporté, on le retrouve comme le rapport entre le match publié et le match
  // recalculé aux poids du set — puis on l'applique au match personnalisé.
  function penalite(bien, sb, set) {
    if (!sb.details.some((d) => d.kind === "disqualifiant")) return 1;
    const ref = agrege(bien, sb.details, set, null, null);
    if (!ref) return 1;
    return Math.min(1, sb.match_score / ref);
  }

  // Match d'un bien recalculé avec des poids donnés. null = non évaluable (aucun des
  // critères pesés n'est mesuré sur ce bien) — même convention que le backend.
  function match(bien, set, poids, params) {
    const sb = (bien.scores_by_set || {})[String(set.id)];
    if (!sb || sb.match_score == null) return null;
    const brut = agrege(bien, sb.details, set, poids, params);
    if (brut == null) return null;
    return arrondi1(brut * penalite(bien, sb, set));
  }

  // Mémo par lentille : le feed rappelle matchOf() sur des milliers de biens à chaque
  // tri/filtre. `invalider()` à chaque changement de poids.
  const memo = new Map();
  function matchMemo(bien, set, poids, params, lentille) {
    let m = memo.get(lentille);
    if (!m) { m = new Map(); memo.set(lentille, m); }
    const k = `${bien.source}__${bien.external_id}`;
    if (m.has(k)) return m.get(k);
    const v = match(bien, set, poids, params);
    m.set(k, v);
    return v;
  }
  const invalider = () => memo.clear();

  // --- convergence / désaccord --------------------------------------------
  // Par critère : qui a dit quoi, la moyenne du groupe, la dispersion (écart-type des
  // valeurs RÉGLÉES). Un critère réglé par une seule personne n'est ni un accord ni un
  // désaccord : il est « peu » renseigné.
  function convergence(set, users) {
    const ex = explicites(set);
    const def = defauts(set);
    const part = participants(set, users);
    return (set.preferences || []).map((p) => {
      const key = cle(p);
      const par = {};
      for (const u of part) if (ex[key] && ex[key][u] !== undefined) par[u] = ex[key][u];
      const vals = Object.values(par);
      const n = vals.length;
      const moyenne = n ? vals.reduce((a, b) => a + b, 0) / n : null;
      const ecart = n > 1 ? Math.sqrt(vals.reduce((a, v) => a + (v - moyenne) ** 2, 0) / n) : null;
      return {
        key, kind: p.kind, label: p.label || p.kind, defaut: def[key], par, n, moyenne, ecart,
        etendue: n ? Math.max(...vals) - Math.min(...vals) : null,
        // Seuils : un écart-type d'un demi-niveau (4 / 5 / 4) reste un accord ; au-delà
        // d'un niveau et demi (5 / 1), les gens ne parlent plus du même bien.
        statut: n < 2 ? "peu" : ecart <= 0.5 ? "accord" : ecart <= 1.2 ? "nuance" : "desaccord",
      };
    });
  }

  // Convergence des SEUILS (et non des poids) : qui demande quoi. C'est là que se lit
  // « Max veut 4 chambres, Léo en veut 2 » — un désaccord que le poids ne peut pas dire.
  function convergenceParams(set, users) {
    const ex = parametres(set);
    const part = participants(set, users);
    const out = [];
    for (const p of (set.preferences || [])) {
      const id = cle(p);
      const champs = Mesures.champs(id);
      if (!champs.length) continue;
      for (const ch of champs) {
        const par = {};
        for (const u of part) {
          const v = (ex[id] || {})[u];
          if (v && v[ch.cle] !== undefined) par[u] = v[ch.cle];
        }
        const vals = Object.values(par);
        if (!vals.length) continue;
        const distinctes = new Set(vals.map(String));
        out.push({
          key: id, label: p.label || p.kind, champ: ch, par,
          defaut: (p.params || {})[ch.cle],
          accord: distinctes.size === 1 && (vals.length === part.length),
          distinctes: distinctes.size,
        });
      }
    }
    return out;
  }

  // Proximité deux à deux : 100 % = mêmes poids partout, 0 % = opposés sur toute
  // l'échelle. Calculée sur les seuls critères que les deux ont réglés.
  function proximites(set, users) {
    const ex = explicites(set);
    const part = participants(set, users);
    const out = [];
    for (let i = 0; i < part.length; i++) {
      for (let j = i + 1; j < part.length; j++) {
        const a = part[i], b = part[j];
        let n = 0, som = 0;
        for (const k of Object.keys(ex)) {
          const va = ex[k][a], vb = ex[k][b];
          if (va === undefined || vb === undefined) continue;
          n++; som += Math.abs(va - vb);
        }
        out.push({ a, b, n, ecart: n ? som / n : null, proximite: n ? 1 - (som / n) / 5 : null });
      }
    }
    return out.sort((x, y) => (y.proximite ?? -1) - (x.proximite ?? -1));
  }

  // --- lecture du registre --------------------------------------------------
  // Part des biens du set sur lesquels le critère est effectivement MESURÉ. C'est la
  // question que la pondération rend urgente : mettre 5 à un critère mesuré sur la
  // moitié du catalogue, c'est classer l'autre moitié sans lui — et `evaluate`
  // renormalise sur les critères notés, donc le bien non mesuré n'est pas pénalisé,
  // il est jugé sans. Le panneau l'affiche pour que le poids soit donné en connaissance.
  const _couv = new Map();
  function couverture(biens, set) {
    if (_couv.has(set.id)) return _couv.get(set.id);
    const ix = index(set);
    const vus = {}, mesures = {};
    let n = 0;
    for (const b of biens) {
      const sb = (b.scores_by_set || {})[String(set.id)];
      if (!sb || sb.match_score == null) continue;
      n++;
      for (const d of (sb.details || [])) {
        if (HORS_CRITERES.has(d.kind)) continue;
        const k = idDeDetail(d, ix);
        vus[k] = (vus[k] || 0) + 1;
        if (d.status === "ok" && d.subscore != null) mesures[k] = (mesures[k] || 0) + 1;
      }
    }
    const out = {};
    for (const p of (set.preferences || [])) {
      const k = cle(p);
      out[k] = n ? (mesures[k] || 0) / n : null;
    }
    _couv.set(set.id, out);
    return out;
  }

  // Critères du set rangés par famille, dans l'ordre du registre. Vingt-trois critères
  // en liste plate ne se lisent pas ; sept groupes, oui.
  function groupes(set, registre) {
    const fams = (registre && registre.familles) || [];
    const idx = (registre && registre.index) || {};
    const paquets = new Map();
    for (const p of (set.preferences || [])) {
      const f = (idx[cle(p)] || {}).famille || "autres";
      if (!paquets.has(f)) paquets.set(f, []);
      paquets.get(f).push(p);
    }
    const out = fams.filter((f) => paquets.has(f.id))
      .map((f) => ({ id: f.id, label: f.label, prefs: paquets.get(f.id) }));
    const restes = [...paquets.keys()].filter((f) => !fams.some((x) => x.id === f));
    for (const f of restes) out.push({ id: f, label: "Autres critères", prefs: paquets.get(f) });
    return out;
  }

  // Nom canonique de la mesure (identique d'un set à l'autre) ; à défaut, le libellé du set.
  function court(p, registre) {
    const f = ((registre || {}).index || {})[cle(p)];
    return (f && f.court) || p.label || p.kind;
  }
  const quoi = (p, registre) => (((registre || {}).index || {})[cle(p)] || {}).quoi || "";

  return {
    NIVEAUX, LIBELLES, cle, bienId, couverture, groupes, court, quoi,
    explicites, defauts, participants, pour, groupe, definir, remettreDefauts,
    parametres, definirParam, paramsPour, paramsGroupe, convergenceParams,
    match, matchMemo, invalider, convergence, proximites,
  };
})();
window.Poids = Poids;
