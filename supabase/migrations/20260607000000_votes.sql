-- Table des votes (note globale + votes par critère). Voir docs/README.md.
-- Appliquée via `supabase db push` après `supabase link`, ou copiable dans
-- le SQL Editor du dashboard.

create table if not exists votes (
  id         bigint generated always as identity primary key,
  bien_id    text not null,
  voter      text not null,
  criterion  text not null default '__overall__',  -- '__overall__' = note globale
  stars      int  check (stars between 1 and 5),    -- nullable : commentaire possible sans note
  comment    text,                                 -- commentaire optionnel
  updated_at timestamptz not null default now(),
  unique (bien_id, voter, criterion)               -- 1 vote par (bien, personne, critère)
);

alter table votes enable row level security;

-- Confiance assumée : lecture + écriture ouvertes à anon (clé publique, protégée RLS).
create policy "anon read"   on votes for select using (true);
create policy "anon insert" on votes for insert with check (true);
create policy "anon update" on votes for update using (true) with check (true);
