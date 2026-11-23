-- New Company Register: run this once in Supabase → SQL Editor → New query → Run.
-- Put your team's emails at the bottom first. Re-run just that last part whenever someone joins.

-- who's on the team -------------------------------------------------------------------------
create table if not exists public.team (email text primary key);

create or replace function public.is_team() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.team where lower(email) = lower(auth.jwt() ->> 'email'));
$$;

-- team lists and notes ----------------------------------------------------------------------
create table if not exists public.lists (
  id         uuid primary key default gen_random_uuid(),
  name       text not null check (length(name) between 1 and 60),
  created_by text not null default lower(auth.jwt() ->> 'email'),
  created_at timestamptz not null default now()
);
create table if not exists public.list_items (
  list_id  uuid not null references public.lists(id) on delete cascade,
  cid      text not null,                      -- CIN, or b-<slug> for a D2C brand
  added_by text not null default lower(auth.jwt() ->> 'email'),
  added_at timestamptz not null default now(),
  primary key (list_id, cid)
);
create table if not exists public.notes (
  cid        text primary key,
  note       text not null check (length(note) <= 5000),
  updated_by text not null default lower(auth.jwt() ->> 'email'),
  updated_at timestamptz not null default now()
);

alter table public.team       enable row level security;
alter table public.lists      enable row level security;
alter table public.list_items enable row level security;
alter table public.notes      enable row level security;

drop policy if exists team_read on public.team;
create policy team_read on public.team for select to authenticated using (public.is_team());
drop policy if exists lists_rw on public.lists;
create policy lists_rw on public.lists for all to authenticated using (public.is_team()) with check (public.is_team());
drop policy if exists items_rw on public.list_items;
create policy items_rw on public.list_items for all to authenticated using (public.is_team()) with check (public.is_team());
drop policy if exists notes_rw on public.notes;
create policy notes_rw on public.notes for all to authenticated using (public.is_team()) with check (public.is_team());

-- newer Supabase projects don't grant table access to signed-in users automatically; RLS above still decides
grant usage on schema public to authenticated;
grant select on public.team to authenticated;
grant select, insert, update, delete on public.lists, public.list_items, public.notes to authenticated;
grant execute on function public.is_team() to authenticated;

do $$ begin
  alter publication supabase_realtime add table public.lists, public.list_items, public.notes;
exception when duplicate_object then null; end $$;

-- the register itself: a private file bucket -------------------------------------------------
-- data.json is written only by the nightly GitHub job (service key). The team can read it, and
-- can drop new MCA workbooks into inbox/ for the job to pick up.
insert into storage.buckets (id, name, public, file_size_limit)
values ('register', 'register', false, 52428800)
on conflict (id) do nothing;

drop policy if exists register_read on storage.objects;
create policy register_read on storage.objects for select to authenticated
  using (bucket_id = 'register' and public.is_team() and name in ('data.json', 'meta.json'));
drop policy if exists register_inbox on storage.objects;
create policy register_inbox on storage.objects for insert to authenticated
  with check (bucket_id = 'register' and public.is_team() and (storage.foldername(name))[1] = 'inbox'
              and lower(name) like '%.xlsx');

-- ▼ your team: lowercase emails, one per line ---------------------------------------------------
insert into public.team (email) values
  ('you@yourcompany.com')
on conflict do nothing;
