-- ---------------------------------------------------------------------------
-- 0003_data_api_grants.sql
--
-- Explicit Data API grants.
--
-- Supabase used to grant the `anon` and `authenticated` roles access to every
-- new table in `public` automatically. That behaviour is being retired: from
-- 2026 a table is invisible to PostgREST (and therefore to this app) unless a
-- role has been granted access to it explicitly.
--
-- Run this file if you turned OFF "Automatically expose new tables and
-- functions" when creating your project, or if the app reports that a table
-- does not exist even though you can see it in the Table Editor.
--
-- Running it when the setting is ON is harmless: these grants are the same
-- ones the platform would have applied for you.
--
-- Two separate gates protect this data, and both must pass:
--   1. GRANTs below  -> "may this role touch this table at all?"
--   2. RLS policies  -> "which rows may it touch?" (see 0001_init.sql)
-- A grant on its own exposes nothing that the policies do not already allow.
-- ---------------------------------------------------------------------------

-- The API roles need to see into the schema before anything else applies.
grant usage on schema public to anon, authenticated;

-- Tables written and read by the app. Listed one by one rather than with
-- `all tables in schema public` so that this file stays an accurate,
-- greppable record of exactly what is exposed.
grant select, insert, update, delete on public.players      to anon, authenticated;
grant select, insert, update, delete on public.games        to anon, authenticated;
grant select, insert, update, delete on public.leaderboard  to anon, authenticated;
grant select, insert, update, delete on public.rooms        to anon, authenticated;
grant select, insert, update, delete on public.moves        to anon, authenticated;
grant select, insert, update, delete on public.messages     to anon, authenticated;
grant select, insert, update, delete on public.achievements to anon, authenticated;
grant select, insert, update, delete on public.challenges   to anon, authenticated;

-- `games`, `moves` and `messages` use identity columns, whose sequences need
-- their own grant before an insert can allocate an id.
grant usage, select on all sequences in schema public to anon, authenticated;

-- Read-only reporting views from 0002. Safe to skip if you have not run that
-- migration yet.
do $$
begin
    if to_regclass('public.recent_matches') is not null then
        execute 'grant select on public.recent_matches to anon, authenticated';
    end if;
    if to_regclass('public.leaderboard_top') is not null then
        execute 'grant select on public.leaderboard_top to anon, authenticated';
    end if;
end
$$;

-- Room cleanup is invoked by the app, so the anon role must be able to call it.
-- `touch_updated_at()` is a trigger function and is deliberately NOT granted:
-- triggers run as the table owner, and nothing should call it directly.
do $$
begin
    if to_regprocedure('public.purge_stale_rooms(integer)') is not null then
        execute 'grant execute on function public.purge_stale_rooms(integer) to anon, authenticated';
    end if;
end
$$;

-- Future tables in `public` created by the `postgres` role inherit these
-- grants, so a later migration does not silently break the app. Remove this
-- block if you would rather grant every new table by hand.
alter default privileges for role postgres in schema public
    grant select, insert, update, delete on tables to anon, authenticated;

alter default privileges for role postgres in schema public
    grant usage, select on sequences to anon, authenticated;