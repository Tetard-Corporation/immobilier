// Usage : node tests/test_poids.mjs   (SNAPSHOT=chemin/data.json pour un autre instantané)
//
// Vérifie la pondération personnelle contre l'instantané produit par le backend :
// le recalcul côté navigateur doit retomber sur le score publié quand on lui donne les
// poids du set, et bouger de façon prévisible quand on les change.
import fs from "node:fs";
import vm from "node:vm";

const root = new URL("..", import.meta.url).pathname;
const DATA = JSON.parse(fs.readFileSync(process.env.SNAPSHOT || `${root}data/data.json`, "utf8"));

// Faux backend de votes : le cache que Votes.entriesFor expose au module.
const cache = {};
const Votes = {
  entriesFor: (id) => cache[id] || {},
  voter: "Léo",
  users: ["Henri", "Max", "Mathurin", "Juliette", "Léo", "Timothé", "Pauline"],
  setMine: (id, stars, crit, comment) => {
    const ex = ((cache[id] ||= {})[crit] ||= {})[Votes.voter];
    cache[id][crit][Votes.voter] = {
      stars, comment: comment !== undefined ? comment : (ex ? ex.comment : null),
    };
    return Promise.resolve({ ok: true });
  },
  setComment: (id, comment, crit) => {
    const ex = ((cache[id] || {})[crit] || {})[Votes.voter];
    return Votes.setMine(id, ex ? ex.stars : null, crit, comment);
  },
};
const ctx = { Votes, window: {}, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(`${root}mesures.js`, "utf8"), ctx);
vm.runInContext(fs.readFileSync(`${root}poids.js`, "utf8"), ctx);
const { Poids, Mesures } = ctx.window;

let ok = 0, ko = 0;
const t = (nom, cond, info = "") => { cond ? ok++ : (ko++, console.log("  ✗", nom, info)); };

// 1. Aux poids du set, le recalcul doit redonner le score publié.
let pires = 0, n = 0, maxEcart = 0;
for (const set of DATA.sets) {
  for (const b of DATA.biens) {
    const sb = (b.scores_by_set || {})[String(set.id)];
    if (!sb || sb.match_score == null) continue;
    n++;
    const d = Math.abs(Poids.match(b, set, null, null) - sb.match_score);
    maxEcart = Math.max(maxEcart, d);
    if (d > 0.15) pires++;   // tolérance = l'arrondi du sous-score à 3 décimales
  }
}
console.log(`1) ${n} scores rejoués aux poids du set — écart max ${maxEcart.toFixed(2)}, hors tolérance : ${pires}`);
t("recalcul fidèle au backend", pires === 0, `${pires} écarts > 0,15`);

const set = DATA.sets[0];
const sid = String(set.id);
const biens = DATA.biens.filter((b) => (b.scores_by_set || {})[sid]?.match_score != null);
// Un bien plafonné par une exigence ou déclassé ne bouge pas quand les poids changent :
// le plafond tient dans les deux sens, c'est la règle du backend.
const bloque = (b) => b.scores_by_set[sid].details.some((d) => d.kind === "exigence" || d.kind === "disqualifiant");
const detail = (b, kind) => b.scores_by_set[sid].details.find((d) => d.kind === kind) || {};

// 2. Ignorer un critère le sort de la moyenne : les biens qui le ratent remontent.
const sansRelief = { ...Poids.defauts(set), relief_mountain: 0 };
const plaine = biens.filter((b) => detail(b, "relief_mountain").subscore < 0.3).slice(0, 50);
const rates = plaine.filter((b) => !bloque(b)
  && Poids.match(b, set, sansRelief, null) <= Poids.match(b, set, null, null));
console.log(`2) ${plaine.length - rates.length}/${plaine.length} biens de plaine remontent quand on ignore la montagne`);
t("ignorer un critère remonte les biens qui le ratent", rates.length === 0, rates.map((b) => b.commune).join(", "));

// 3. Monter le poids d'un critère que le bien coche ne peut pas le faire baisser.
const soleilFort = { ...Poids.defauts(set), ensoleillement: 5 };
const plein_sud = biens.filter((b) => detail(b, "ensoleillement").subscore > 0.9).slice(0, 40);
const baissent = plein_sud.filter((b) => Poids.match(b, set, soleilFort, null) < Poids.match(b, set, null, null));
const montent = plein_sud.filter((b) => Poids.match(b, set, soleilFort, null) > Poids.match(b, set, null, null));
console.log(`3) ${montent.length}/${plein_sud.length} biens plein sud montent quand l'exposition passe de 4 à 5`);
t("un critère coché qui pèse plus ne fait jamais baisser", baissent.length === 0);
t("et il fait monter la plupart", montent.length >= plein_sud.filter((b) => !bloque(b)).length * 0.8);

// 4. Stockage : 0 -> stars null (la contrainte SQL interdit 0), relecture correcte.
await Poids.definir(set, "relief_mountain", 0);
await Poids.definir(set, "ensoleillement", 5);
const ex = Poids.explicites(set);
t("« ignorer » stocké en stars null", cache[Poids.bienId(set.id)].relief_mountain["Léo"].stars === null);
t("0 relu comme 0", ex.relief_mountain["Léo"] === 0);
t("5 relu comme 5", ex.ensoleillement["Léo"] === 5);
const mien = Poids.pour(set, "Léo");
const poidsDuSet = (id) => Number(set.preferences.find((p) => Poids.cle(p) === id).weight);
t("critère non réglé = poids du set", mien.budget === poidsDuSet("budget"));
t("critère réglé = mon poids", mien.relief_mountain === 0 && mien.ensoleillement === 5);
t("participants", JSON.stringify(Poids.participants(set, Votes.users)) === '["Léo"]');

// 5. Convergence entre deux personnes.
Votes.voter = "Max";
await Poids.definir(set, "relief_mountain", 5);   // Max veut la montagne, Léo l'ignore
await Poids.definir(set, "ensoleillement", 4);
const conv = Poids.convergence(set, Votes.users);
const cRelief = conv.find((c) => c.key === "relief_mountain");
const cSoleil = conv.find((c) => c.key === "ensoleillement");
t("désaccord détecté", cRelief.statut === "desaccord", JSON.stringify(cRelief));
t("étendue du désaccord", cRelief.etendue === 5);
t("accord détecté", cSoleil.statut === "accord", JSON.stringify(cSoleil));
t("moyenne du groupe", Math.abs(cSoleil.moyenne - 4.5) < 1e-9);
const prox = Poids.proximites(set, Votes.users);
t("proximité calculée", prox.length === 1 && prox[0].n === 2 && Math.abs(prox[0].proximite - (1 - 3 / 5)) < 1e-9);
const g = Poids.groupe(set, Votes.users);
t("groupe : moyenne sur critère réglé", g.relief_mountain === 2.5);
t("groupe : poids du set sinon", g.jardin === poidsDuSet("jardin"));

// 6. Tout ignorer = plus de classement (même convention que le backend).
const rien = Object.fromEntries(Object.keys(Poids.defauts(set)).map((k) => [k, 0]));
t("tout ignorer = non classé", Poids.match(biens[0], set, rien, null) === null);

// 7. Un bien déclassé (viager, sous compromis) le reste quels que soient les poids.
for (const b of DATA.biens) {
  const s = Object.keys(b.scores_by_set || {}).find((k) => b.scores_by_set[k].details.some((d) => d.kind === "disqualifiant"));
  if (!s) continue;
  const st = DATA.sets.find((x) => String(x.id) === s);
  t("pénalité conservée", Poids.match(b, st, Poids.pour(st, "Max"), null) < 40);
  break;
}

// 8. Seuils personnels : « 4 chambres minimum » n'est pas « 3 chambres minimum ».
Votes.voter = "Juliette";
const prefCh = set.preferences.find((p) => p.kind === "chambres_min");
if (prefCh) {
  const defautMin = prefCh.params.min;
  await Poids.definirParam(set, "chambres_min", "min", defautMin + 2);
  const seuils = Poids.paramsPour(set, "Juliette");
  t("seuil stocké dans le commentaire de la ligne",
    JSON.parse(cache[Poids.bienId(set.id)].chambres_min["Juliette"].comment).min === defautMin + 2);
  t("seuil fusionné avec ceux du set", seuils.chambres_min.min === defautMin + 2
    && seuils.chambres_min.m2_min_par_piece === prefCh.params.m2_min_par_piece);
  t("les autres critères ne sont pas personnalisés", Object.keys(seuils).length === 1);
  // Poser un seuil sur un critère jamais pondéré ne doit pas l'éteindre : la ligne
  // s'écrirait avec stars = null, c'est-à-dire « ignoré ».
  t("poser un seuil garde le poids du set", Poids.pour(set, "Juliette").chambres_min === prefCh.weight,
    String(Poids.pour(set, "Juliette").chambres_min));

  // Un bien qui a juste le minimum du set perd des points quand on exige deux chambres
  // de plus ; un bien qui a déjà assez de chambres ne bouge pas.
  const justes = biens.filter((b) => b.nb_chambres === defautMin).slice(0, 30);
  const grands = biens.filter((b) => b.nb_chambres >= defautMin + 2).slice(0, 30);
  const poids = Poids.defauts(set);
  const baisse = justes.filter((b) => !bloque(b)
    && Poids.match(b, set, poids, seuils) < Poids.match(b, set, poids, null));
  const stables = grands.filter((b) => Poids.match(b, set, poids, seuils) === Poids.match(b, set, poids, null));
  console.log(`8) seuil ${defautMin} -> ${defautMin + 2} chambres : ${baisse.length}/${justes.length} biens tout juste baissent, `
    + `${stables.length}/${grands.length} biens assez grands ne bougent pas`);
  t("un seuil plus exigeant fait baisser les biens tout juste", baisse.length >= justes.filter((b) => !bloque(b)).length * 0.9);
  t("et ne touche pas ceux qui le remplissaient déjà", stables.length === grands.length);

  // Le seuil du groupe est une MÉDIANE : une moyenne donnerait « 3,5 chambres ».
  Votes.voter = "Timothé";
  await Poids.definirParam(set, "chambres_min", "min", defautMin + 1);
  const med = Poids.paramsGroupe(set, Votes.users);
  t("seuil du groupe = médiane", [defautMin + 1, defautMin + 2].includes(med.chambres_min.min), JSON.stringify(med.chambres_min));

  // Un seuil effacé (valeur vide) revient à celui du set.
  await Poids.definirParam(set, "chambres_min", "min", null);
  t("effacer un seuil rend la main au set", Poids.paramsPour(set, "Timothé").chambres_min === undefined);
}

// 9. Plus aucun plafond : le seuil personnel agit par la NOTE, pas par une falaise.
//    Un bien trop cher pour mon budget descend continûment, il ne se colle pas à 70 avec
//    cent autres — c'est ce qui rendait deux classements personnels impossibles à
//    distinguer.
const prefBudget = set.preferences.find((p) => p.kind === "budget");
if (prefBudget) {
  const poids = Poids.defauts(set);
  const petitBudget = { budget: { ...prefBudget.params, budget_max: 120000, budget_min: 0 } };
  // Ceux dont la note budget n'est pas DÉJÀ à zéro sous le budget du set : un bien à
  // 299 k€ vaut déjà 0 avec les 250 k€ du set, mon plafond à 120 k€ n'y change rien.
  const chers = biens.filter((b) => b.prix > 200000
    && Mesures.subscore("budget", b, prefBudget.params) > 0).slice(0, 30);
  const baisse = chers.filter((b) => Poids.match(b, set, poids, petitBudget) < Poids.match(b, set, poids, null));
  const valeurs = new Set(chers.map((b) => Poids.match(b, set, poids, petitBudget)));
  console.log(`9) budget personnel à 120 k€ : ${baisse.length}/${chers.length} biens chers baissent, `
    + `${valeurs.size} valeurs distinctes (aucun plateau)`);
  t("un seuil personnel fait baisser sans plafonner", baisse.length === chers.length);
  t("et ne colle pas les biens sur une même valeur", valeurs.size > chers.length * 0.5);
  t("plus aucune ligne de plafond dans les détails",
    !biens.some((b) => (b.scores_by_set[sid].details || []).some((d) => d.kind === "exigence")));
}

// 9 bis. Un critère non mesuré compte à l'a priori du catalogue, il ne disparaît plus.
const avecApriori = set.preferences.filter((p) => p.apriori != null);
if (avecApriori.length) {
  const p = avecApriori.find((x) => (x.couverture ?? 1) < 0.9) || avecApriori[0];
  const id = Poids.cle(p);
  const nonMesure = biens.filter((b) => (b.scores_by_set[sid].details || [])
    .some((d) => Poids.cle(p) === id && (d.label || d.kind) === (p.label || p.kind) && d.status !== "ok"));
  console.log(`9 bis) « ${p.label} » : a priori ${p.apriori}, non mesuré sur ${nonMesure.length} biens publiés`);
  t("l'a priori est exporté avec le critère", typeof p.apriori === "number" && p.apriori > 0);
  if (nonMesure.length) {
    // Ignorer ce critère change le score d'un bien où il n'est PAS mesuré : preuve qu'il
    // comptait quand même. Avant l'a priori, il était absent du calcul et l'ignorer ne
    // changeait rien — c'est exactement le trou que les paliers « mesuré » bouchaient.
    const b = nonMesure[0];
    const sans = Poids.match(b, set, { ...Poids.defauts(set), [id]: 0 }, null);
    t("un critère non mesuré pèse quand même", sans !== Poids.match(b, set, Poids.defauts(set), null));
  }
}

// 10. Les critères non portés n'offrent pas de seuil (leur mesure ne se rejoue pas ici).
t("pas de seuil sur l'ensoleillement", !Mesures.parametrable("ensoleillement"));
t("pas de seuil sur la gare", !Mesures.parametrable("near_gare"));
t("seuil sur les chambres", Mesures.parametrable("chambres_min"));

console.log(`\n${ok} vérifications OK, ${ko} en échec`);
process.exit(ko ? 1 : 0);
