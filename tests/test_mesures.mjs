// Usage : node tests/test_mesures.mjs   (SNAPSHOT=... pour un autre instantané)
//
// Les formules portées dans mesures.js doivent redonner EXACTEMENT le sous-score du
// backend quand on leur passe les paramètres du set. Sinon elles ne sont pas portées :
// elles sont réécrites, et le classement personnalisé raconterait autre chose.
import fs from "node:fs";
import vm from "node:vm";

const root = new URL("..", import.meta.url).pathname;
const DATA = JSON.parse(fs.readFileSync(process.env.SNAPSHOT || `${root}/data/data.json`, "utf8"));
const ctx = { window: {}, console, Votes: { entriesFor: () => ({}), voter: null, users: [] } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(`${root}/mesures.js`, "utf8"), ctx);
vm.runInContext(fs.readFileSync(`${root}/poids.js`, "utf8"), ctx);
const { Mesures, Poids } = ctx.window;

let ko = 0, total = 0;
const ecarts = {};
for (const set of DATA.sets) {
  const parLabel = {};
  for (const p of set.preferences || []) parLabel[p.label || p.kind] = p;
  for (const b of DATA.biens) {
    const sb = (b.scores_by_set || {})[String(set.id)];
    if (!sb || !Array.isArray(sb.details)) continue;   // détail non publié : rien à rejouer
    for (const d of sb.details) {
      const p = parLabel[d.label || d.kind];
      if (!p) continue;
      const id = Poids.cle(p);
      if (!Mesures.parametrable(id)) continue;
      const attendu = d.status === "ok" ? d.subscore : null;
      const obtenu = Mesures.subscore(id, b, p.params || {});
      total++;
      // Le sous-score exporté est arrondi à 3 décimales.
      const ok = attendu == null ? obtenu == null : (obtenu != null && Math.abs(obtenu - attendu) <= 0.001);
      if (!ok) {
        ko++;
        (ecarts[id] ||= []).push({ set: set.id, bien: b.external_id, attendu, obtenu, commune: b.commune,
          prix: b.prix, ch: b.nb_chambres, pieces: b.nb_pieces, bati: b.surface_bati,
          terrain: b.surface_terrain, alt: b.altitude, dpe: b.dpe_classe, type: b.type_bien });
      }
    }
  }
}
console.log(`${total} sous-scores rejoués aux paramètres du set — ${ko} écarts`);
for (const [id, list] of Object.entries(ecarts)) {
  console.log(`\n  ✗ ${id} : ${list.length} écarts`);
  for (const e of list.slice(0, 3)) console.log("     ", JSON.stringify(e));
}
process.exit(ko ? 1 : 0);
