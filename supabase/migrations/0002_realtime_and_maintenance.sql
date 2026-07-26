-- ===========================================================================
-- GRIDLOCK — realtime publication, maintenance helpers and reporting views
-- ===========================================================================

-- --------------------------------------------------------------- realtime --
-- The Streamlit client polls, but enabling realtime lets you subscribe from a
-- JavaScript front end (or a future Streamlit component) without extra work.
do $$
begin
    if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
        execute 'alter publication supabase_realtime add table public.rooms';
        execute 'alter publication supabase_realtime add table public.moves';
        execute 'alter publication supabase_realtime add table public.messages';
    end if;
exception
    when duplicate_object then null;
end;
$$;

-- ------------------------------------------------------------ maintenance --
-- Delete rooms nobody has touched for `max_age_minutes`.  Child rows go with
-- them thanks to the on-delete cascades in 0001.
create or replace function public.purge_stale_rooms(max_age_minutes integer default 30)
returns integer
language plpgsql
security definer
as $$
declare
    removed integer;
begin
    with deleted as (
        delete from public.rooms
        where updated_at < now() - make_interval(mins => max_age_minutes)
        returning code
    )
    select count(*) into removed from deleted;
    return removed;
end;
$$;

-- Schedule it every 15 minutes when pg_cron is available.
do $$
begin
    if exists (select 1 from pg_extension where extname = 'pg_cron') then
        perform cron.schedule(
            'gridlock-purge-stale-rooms',
            '*/15 * * * *',
            $cron$select public.purge_stale_rooms(30)$cron$
        );
    end if;
exception
    when others then null;
end;
$$;

-- --------------------------------------------------------------- reporting --
create or replace view public.recent_matches as
select
    g.id,
    g.created_at,
    g.mode,
    g.result,
    g.opponent,
    g.size,
    g.moves,
    g.duration,
    g.room_code,
    p.name,
    p.avatar
from public.games g
left join public.players p on p.id = g.player_id
order by g.created_at desc
limit 100;

create or replace view public.leaderboard_top as
select
    row_number() over (order by wins desc, win_rate desc) as rank,
    player_id,
    name,
    avatar,
    games,
    wins,
    losses,
    draws,
    win_rate,
    best_streak
from public.leaderboard
where games > 0
order by wins desc, win_rate desc
limit 100;
