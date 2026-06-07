// Configuration du front (éditable à la main, lue par votes.js / app.js).
// — Renseigne Supabase pour activer le vote PARTAGÉ entre vous.
//   Sans ça, le vote bascule en mode local (localStorage, par navigateur).
window.APP_CONFIG = {
  // 1) Crée un projet gratuit sur https://supabase.com
  // 2) Settings → API : colle l'URL du projet et la clé "anon public"
  SUPABASE_URL: "",        // ex : "https://abcd1234.supabase.co"
  SUPABASE_ANON_KEY: "",   // clé "anon" (publique, protégée par RLS)

  // 3) Les participants : c'est la liste du "Qui es-tu ?" en début de session.
  USERS: ["Henri", "Max", "Mathurin", "Juliette", "Léo", "Timothé"],
};
