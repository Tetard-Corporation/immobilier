// Configuration du front (éditable à la main, lue par votes.js / app.js).
// — Renseigne Supabase pour activer le vote PARTAGÉ entre vous.
//   Sans ça, le vote bascule en mode local (localStorage, par navigateur).
window.APP_CONFIG = {
  // 1) Crée un projet gratuit sur https://supabase.com
  // 2) Settings → API : colle l'URL du projet et la clé "anon public"
  SUPABASE_URL: "https://vmgvreijzslrmyolejjo.supabase.co",
  // Clé "publishable" (publique, protégée par RLS) — utilisée comme apikey + Bearer.
  SUPABASE_ANON_KEY: "sb_publishable_qa2Dmw3LM3HqWZ34OLC5qQ_nZteG52r",

  // 3) Les participants : c'est la liste du "Qui es-tu ?" en début de session.
  USERS: ["Henri", "Max", "Mathurin", "Juliette", "Léo", "Timothé"],
};
