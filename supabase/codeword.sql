-- Team codeword sign-in. Run in Supabase → SQL Editor after schema.sql.
-- 1. Replace change-me below with your team's codeword (not case-sensitive), then Run.
-- 2. To change the codeword later: edit and run just the "set the codeword" line, then delete everyone under
--    Authentication → Users. Their accounts are recreated the next time they enter the new codeword;
--    lists and notes are kept.

create schema if not exists private;          -- not exposed through the API
create table if not exists private.settings (k text primary key, v text not null);
alter table private.settings enable row level security;   -- no policies: only the check below can read it

-- ▼ set the codeword
insert into private.settings (k, v) values ('codeword', 'change-me') on conflict (k) do update set v = excluded.v;

-- New accounts are only created for emails on the team list that used the codeword.
create or replace function private.check_signup() returns trigger
language plpgsql security definer set search_path = '' as $$
declare w text;
begin
  if not exists (select 1 from public.team t where lower(t.email) = lower(new.email)) then
    raise exception 'not on the team list';
  end if;
  select v into w from private.settings where k = 'codeword';
  if w is null or new.encrypted_password is null
     or new.encrypted_password <> extensions.crypt('register:' || lower(trim(w)), new.encrypted_password) then
    raise exception 'wrong codeword';
  end if;
  return new;
end $$;

drop trigger if exists check_signup on auth.users;
create trigger check_signup before insert on auth.users for each row execute function private.check_signup();
