-- ===========================================================================
-- GRIDLOCK — initial schema
-- Apply with the Supabase SQL editor, or `supabase db push` from this folder.
-- ===========================================================================

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------- players --
create table if not exists public.players (
    id          text primary key,
    name        text not null default 'Player',
    avatar      text not null default '🦊',
    is_guest    boolean not null default true,
    email       text default '',
    created_at  timestamptz not null default now(),
    last_seen   timestamptz not null default now()
);

-- ------------------------------------------------------------------ games --
create table if not exists public.games (
    id           bigint generated always as identity primary key,
    player_id    text not null references public.players(id) on delete cascade,
    mode         text not null check (mode in ('single', 'local', 'online')),
    result       text not null check (result in ('win', 'loss', 'draw')),
    player_mark  text not null check (player_mark in ('X', 'O')),
    opponent     text default '',
    difficulty   text default '',
    size         smallint not null default 3 check (size between 3 and 5),
    moves        smallint not null default 0,
    duration     double precision not null default 0,
    replay       jsonb not null default '{}'::jsonb,
    room_code    text default '',
    created_at   timestamptz not null default now()
);

create index if not exists idx_games_player_created
    on public.games (player_id, created_at desc);
create index if not exists idx_games_created on public.games (created_at desc);

-- ------------------------------------------------------------ leaderboard --
create table if not exists public.leaderboard (
    player_id      text primary key references public.players(id) on delete cascade,
    name           text not null default 'Player',
    avatar         text not null default '🦊',
    games          integer not null default 0,
    wins           integer not null default 0,
    losses         integer not null default 0,
    draws          integer not null default 0,
    win_rate       double precision not null default 0,
    best_streak    integer not null default 0,
    current_streak integer not null default 0,
    updated_at     timestamptz not null default now()
);

create index if not exists idx_leaderboard_wins on public.leaderboard (wins desc);
create index if not exists idx_leaderboard_rate on public.leaderboard (win_rate desc);

-- ------------------------------------------------------------------ rooms --
create table if not exists public.rooms (
    code           text primary key,
    host_id        text not null,
    host_name      text not null default 'Host',
    host_avatar    text not null default '🦊',
    guest_id       text default '',
    guest_name     text default '',
    guest_avatar   text default '',
    board          text not null,
    size           smallint not null default 3 check (size between 3 and 5),
    win_length     smallint not null default 3,
    current_turn   text not null default 'X' check (current_turn in ('X', 'O')),
    status         text not null default 'waiting'
                   check (status in ('waiting', 'in_progress', 'finished', 'abandoned')),
    winner         text default '',
    winning_line   text default '[]',
    move_count     smallint not null default 0,
    rematch_host   smallint not null default 0,
    rematch_guest  smallint not null default 0,
    host_seen      timestamptz not null default now(),
    guest_seen     timestamptz,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create index if not exists idx_rooms_status on public.rooms (status, created_at desc);
create index if not exists idx_rooms_updated on public.rooms (updated_at);

-- ------------------------------------------------------------------ moves --
create table if not exists public.moves (
    id           bigint generated always as identity primary key,
    room_code    text not null references public.rooms(code) on delete cascade,
    move_number  smallint not null,
    cell_index   smallint not null,
    mark         text not null check (mark in ('X', 'O')),
    player_id    text default '',
    created_at   timestamptz not null default now(),
    unique (room_code, move_number)
);

create index if not exists idx_moves_room on public.moves (room_code, move_number);

-- --------------------------------------------------------------- messages --
create table if not exists public.messages (
    id          bigint generated always as identity primary key,
    room_code   text not null references public.rooms(code) on delete cascade,
    player_id   text not null,
    name        text not null default 'Player',
    kind        text not null default 'chat' check (kind in ('chat', 'reaction')),
    body        text not null,
    created_at  timestamptz not null default now()
);

create index if not exists idx_messages_room on public.messages (room_code, created_at desc);

-- ----------------------------------------------------------- achievements --
create table if not exists public.achievements (
    player_id    text not null references public.players(id) on delete cascade,
    code         text not null,
    unlocked_at  timestamptz not null default now(),
    primary key (player_id, code)
);

-- ------------------------------------------------------------- challenges --
create table if not exists public.challenges (
    player_id   text not null references public.players(id) on delete cascade,
    day         date not null,
    code        text not null,
    completed   boolean not null default false,
    updated_at  timestamptz not null default now(),
    primary key (player_id, day)
);

-- ------------------------------------------------------- updated_at hook --
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists trg_rooms_touch on public.rooms;
create trigger trg_rooms_touch
    before update on public.rooms
    for each row execute function public.touch_updated_at();

-- ------------------------------------------------------------------ RLS ---
-- The app authenticates players with anonymous IDs using the public anon key,
-- so these policies are intentionally permissive.  Tighten them (for example
-- `using (auth.uid()::text = id)`) once you require Google sign-in.
alter table public.players      enable row level security;
alter table public.games        enable row level security;
alter table public.leaderboard  enable row level security;
alter table public.rooms        enable row level security;
alter table public.moves        enable row level security;
alter table public.messages     enable row level security;
alter table public.achievements enable row level security;
alter table public.challenges   enable row level security;

do $$
declare
    target text;
begin
    foreach target in array array[
        'players', 'games', 'leaderboard', 'rooms', 'moves',
        'messages', 'achievements', 'challenges'
    ]
    loop
        execute format('drop policy if exists %I on public.%I', target || '_anon_all', target);
        execute format(
            'create policy %I on public.%I for all to anon, authenticated using (true) with check (true)',
            target || '_anon_all', target
        );
    end loop;
end;
$$;
