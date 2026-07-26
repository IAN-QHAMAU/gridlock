"""GRIDLOCK—a portfolio-grade tic-tac-toe web application.

Run with::

    streamlit run app.py

The module is deliberately thin: it wires together the domain layer (`game`,
`board`, `ai`), the persistence layer (`database`, `multiplayer`, `auth`) and
the design system (`styles`) into Streamlit pages.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

import pandas as pd
import streamlit as st

import styles
from auth import AuthError, AuthManager, Identity
from board import EMPTY, Board, InvalidMoveError
from config import (
    APP_NAME,
    APP_TAGLINE,
    AVATARS,
    BOARD_SIZES,
    EMOJI_REACTIONS,
    LOCKED_THEME,
    ONLINE_POLL_SECONDS,
    TIMED_MODE_SECONDS,
    VERSION,
    Difficulty,
    GameMode,
    RoomRole,
    SoundName,
    ThemeName,
    configure_logging,
    get_settings,
    get_theme,
)
from database import BackendUnavailableError, Database, GameRecord, Stats, get_database
from game import Game, GameResult, Scoreboard
from multiplayer import (
    RoomError,
    RoomManager,
    RoomState,
    normalise_code,
)
from player import Mark, Player, PlayerType, sanitize_name
from utils import (
    ACHIEVEMENTS,
    challenge_for,
    confetti,
    evaluate_achievements,
    format_duration,
    games_to_csv,
    games_to_json,
    load_replay,
    play_sound,
    relative_time,
    rerun,
    stretch,
    toast,
)

logger = configure_logging()

PAGES: tuple[str, ...] = (
    "Home",
    "Play",
    "Online",
    "Statistics",
    "Leaderboard",
    "Achievements",
    "Daily challenge",
    "Replays",
    "Settings",
)


# --------------------------------------------------------------------------- #
# Session preferences
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Prefs:
    """User-adjustable settings held in the Streamlit session."""

    theme: str = ThemeName.NEON.value
    sound: bool = True
    animations: bool = True
    difficulty: str = Difficulty.MEDIUM.value
    board_size: int = 3
    timed: bool = False
    seconds_per_move: int = TIMED_MODE_SECONDS

    @property
    def theme_name(self) -> ThemeName:
        """Active theme as an enum, honouring `config.LOCKED_THEME`."""
        if LOCKED_THEME is not None:
            return LOCKED_THEME
        try:
            return ThemeName(self.theme)
        except ValueError:
            return ThemeName.NEON

    @property
    def difficulty_enum(self) -> Difficulty:
        """Active difficulty as an enum."""
        try:
            return Difficulty(self.difficulty)
        except ValueError:
            return Difficulty.MEDIUM


@dataclass(slots=True)
class Context:
    """Everything a page needs,assembled once per script run."""

    db: Database
    auth: AuthManager
    identity: Identity
    prefs: Prefs
    rooms: RoomManager
    scoreboard: Scoreboard = field(default_factory=Scoreboard)


def load_prefs() -> Prefs:
    """Read (or initialise) preferences from the session."""
    raw = st.session_state.get("prefs")
    prefs = Prefs(**raw) if isinstance(raw, dict) else Prefs()
    st.session_state["prefs"] = asdict(prefs)
    return prefs


def save_prefs(prefs: Prefs) -> None:
    """Persist preferences back into the session."""
    st.session_state["prefs"] = asdict(prefs)


def get_context() -> Context:
    """Build the per-run context (identity, storage, preferences)."""
    settings = get_settings()
    db = get_database(settings)
    auth = AuthManager(db, st.session_state, settings)
    auth.complete_oauth(st.query_params)
    identity = auth.current()
    prefs = load_prefs()
    scoreboard = st.session_state.setdefault("scoreboard", Scoreboard())
    return Context(
        db=db,
        auth=auth,
        identity=identity,
        prefs=prefs,
        rooms=RoomManager(db),
        scoreboard=scoreboard,
    )


def fragment(run_every: float | None = None) -> Callable[[Callable], Callable]:
    """`st.fragment` when available, otherwise a no-op decorator."""
    if hasattr(st, "fragment"):
        return st.fragment(run_every=run_every)  # type: ignore[return-value]

    def _identity(func: Callable) -> Callable:  # pragma: no cover - old Streamlit
        return func

    return _identity


def navigate_to(page: str) -> None:
    """Programmatically switch pages (used by call-to-action buttons)."""
    st.session_state["current_page"] = page
    rerun()


def app_rerun() -> None:
    """Rerun the whole app, even from inside a fragment."""
    try:
        st.rerun(scope="app")
    except TypeError:  # pragma: no cover - older Streamlit
        rerun()


# --------------------------------------------------------------------------- #
# Shared rendering
# --------------------------------------------------------------------------- #
def render_board(
    board: Board,
    *,
    winning_line: Sequence[int] = (),
    last_move: int | None = None,
    frozen: bool = False,
    animations: bool = True,
) -> int | None:
    """Draw a clickable board and return the clicked cell index (if any)."""
    st.markdown(
        styles.board_css(board, winning_line, animations=animations, last_move=last_move),
        unsafe_allow_html=True,
    )
    outer = st.columns([1, 3, 1] if board.size > 3 else [1, 2, 1])
    clicked: int | None = None
    with outer[1]:
        for row in range(board.size):
            columns = st.columns(board.size, gap="small")
            for col in range(board.size):
                index = row * board.size + col
                value = board.cells[index]
                label = value if value != EMPTY else "·"
                disabled = frozen or value != EMPTY
                if columns[col].button(
                    label,
                    key=f"cell-{index}",
                    disabled=disabled,
                    help=None,
                    **stretch(),
                ):
                    clicked = index
    return clicked


def render_static_board(board: Board, winning_line: Sequence[int] = ()) -> str:
    """HTML-only board used for replays and previews."""
    winners = set(winning_line or ())
    cells = []
    for index, value in enumerate(board.cells):
        colour = {
            Mark.X.value: "var(--ttt-x-color)",
            Mark.O.value: "var(--ttt-o-color)",
        }.get(value, "var(--ttt-text-muted)")
        if index in winners:
            colour = "var(--ttt-win)"
        cells.append(
            f'<div style="display:flex;align-items:center;justify-content:center;'
            f'aspect-ratio:1/1;border:1px solid var(--ttt-grid-line);'
            f'border-radius:var(--ttt-radius);font-family:var(--ttt-display-font);'
            f'font-size:1.7rem;font-weight:700;color:{colour};'
            f'background:var(--ttt-surface-alt)">{value if value != EMPTY else "·"}</div>'
        )
    return (
        f'<div style="display:grid;gap:6px;max-width:320px;'
        f'grid-template-columns:repeat({board.size}, 1fr)">{"".join(cells)}</div>'
    )


def render_seats(game: Game) -> None:
    """Player strip above the board."""
    cards = []
    for mark in (Mark.X, Mark.O):
        player = game.players[mark]
        role = (
            f"Engine · {player.difficulty.label}" if player.is_ai and player.difficulty
            else ("Engine" if player.is_ai else "Human")
        )
        cards.append(
            styles.seat(
                name=player.name,
                avatar=player.avatar,
                mark=mark,
                role_label=role,
                active=(game.current is mark and not game.is_over),
            )
        )
    st.markdown(styles.seats_row(cards), unsafe_allow_html=True)


def result_tone(game: Game, perspective: Mark) -> tuple[str, str, str]:
    """Return (tone, title, subtitle) for the end-of-game banner."""
    duration = format_duration(game.duration)
    if game.winner is None:
        return "draw", "Stalemate", f"{len(game.moves)} moves · {duration}"
    winner = game.players[game.winner]
    tone = "win" if game.winner is perspective else "loss"
    return tone, f"{winner.avatar} {winner.name} wins", f"{len(game.moves)} moves · {duration}"


# --------------------------------------------------------------------------- #
# Result recording
# --------------------------------------------------------------------------- #
def record_result(ctx: Context, game: Game, perspective: Mark, room_code: str = "") -> None:
    """Persist a finished game once, then evaluate achievements and challenges."""
    marker = f"{game.game_id}:{room_code}"
    if st.session_state.get("_recorded") == marker:
        return
    st.session_state["_recorded"] = marker

    opponent = game.players[perspective.opponent]
    record = GameRecord(
        player_id=ctx.identity.player_id,
        mode=game.mode.value,
        result=game.result_for(perspective),
        player_mark=perspective.value,
        opponent=opponent.name,
        difficulty=(opponent.difficulty.value if opponent.difficulty else ""),
        size=game.board.size,
        moves=len(game.moves),
        duration=round(game.duration, 2),
        replay=game.to_replay(),
        room_code=room_code,
    )
    try:
        ctx.db.record_game(record)
    except Exception as exc:  # pragma: no cover - storage failures shouldn't break play
        logger.warning("Could not record game: %s", exc)
        return

    context = {
        "result": record.result,
        "difficulty": record.difficulty,
        "size": record.size,
        "moves": record.moves,
        "mode": record.mode,
        "undos": st.session_state.get("undo_count", 0),
        "opponent_took_centre": _opponent_took_centre(game, perspective),
    }
    stats = ctx.db.get_stats(ctx.identity.player_id)
    _unlock_achievements(ctx, stats, context)
    _check_challenge(ctx, context)


def _opponent_took_centre(game: Game, perspective: Mark) -> bool:
    """Whether the opponent occupied the centre cell (3x3 challenges)."""
    if game.board.size % 2 == 0:
        return False
    centre = game.board.area // 2
    return game.board.cells[centre] == perspective.opponent.value


def _unlock_achievements(ctx: Context, stats: Stats, context: dict[str, Any]) -> None:
    """Unlock any newly earned achievements and notify the player."""
    try:
        for achievement in evaluate_achievements(stats, context):
            if ctx.db.unlock_achievement(ctx.identity.player_id, achievement.code):
                toast(f"Achievement unlocked — {achievement.title}", icon=achievement.icon)
    except Exception as exc:  # pragma: no cover
        logger.debug("Achievement evaluation failed: %s", exc)


def _check_challenge(ctx: Context, context: dict[str, Any]) -> None:
    """Mark today's challenge complete when its condition is met."""
    today = datetime.now(timezone.utc).date()
    challenge = challenge_for(today)
    try:
        existing = ctx.db.get_challenge(ctx.identity.player_id, today.isoformat())
        if existing and existing.get("completed"):
            return
        if challenge.is_satisfied(context):
            ctx.db.save_challenge(ctx.identity.player_id, today.isoformat(), challenge.code, True)
            toast(f"Daily challenge complete — {challenge.title}", icon="🗓️")
    except Exception as exc:  # pragma: no cover
        logger.debug("Challenge check failed: %s", exc)


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def render_sidebar(ctx: Context) -> str:
    """Navigation,identity and quick settings.Returns the selected page."""
    with st.sidebar:
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:0.7rem;margin-bottom:0.4rem">'
            f'{styles.logo_svg(52, ctx.prefs.animations)}'
            f'<div><div style="font-family:var(--ttt-display-font);font-size:1.35rem;'
            f'font-weight:700;letter-spacing:-0.02em">{APP_NAME}</div>'
            f'<div class="ttt-muted" style="font-size:0.72rem">v{VERSION}</div></div></div>',
            unsafe_allow_html=True,
        )

        current = st.session_state.get("current_page", "Home")
        page = st.radio(
            "Navigate",
            PAGES,
            index=PAGES.index(current) if current in PAGES else 0,
            label_visibility="collapsed",
        )
        st.session_state["current_page"] = page

        st.markdown("---")
        st.markdown(
            f'<div class="ttt-eyebrow">Signed in</div>'
            f'<div style="font-size:1.05rem">{ctx.identity.avatar} {ctx.identity.name}</div>'
            f'<div class="ttt-muted">'
            f'{"Guest · " + ctx.identity.short_id if ctx.identity.is_guest else ctx.identity.email}'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.markdown("---")
        prefs = ctx.prefs
        if LOCKED_THEME is None:
            theme_names = list(ThemeName)
            prefs.theme = st.selectbox(
                "Theme",
                theme_names,
                index=theme_names.index(prefs.theme_name),
                format_func=lambda name: get_theme(name).label,
            ).value
        left, right = st.columns(2)
        prefs.sound = left.toggle("Sound", value=prefs.sound)
        prefs.animations = right.toggle("Motion", value=prefs.animations)
        save_prefs(prefs)

        st.markdown("---")
        backend = "Supabase" if ctx.db.name == "supabase" else "Local SQLite"
        st.caption(f"Storage: {backend}")
        if ctx.db.name != "supabase":
            st.caption("Add Supabase credentials to play across devices.")

        return page


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def page_home(ctx: Context) -> None:
    """Landing page."""
    st.markdown(
        styles.hero(
            APP_TAGLINE + " Four engines,three board sizes and real-time rooms "
            "built as a single Streamlit application.",
            chips=(
                "Minimax + alpha-beta",
                "Online rooms",
                "Six themes",
                "Persistent stats",
                "Replays",
            ),
            animated=ctx.prefs.animations,
        ),
        unsafe_allow_html=True,
    )

    st.write("")
    columns = st.columns(3, gap="medium")
    cards = (
        ("Play the engine","Four difficulties, from careless to unbeatable.", "Play"),
        ("Play a friend","Share a five-character room code and go.", "Online"),
        ("Study your record","Win rate,streaks,replays and exports.", "Statistics"),
    )
    for column, (title, body, target) in zip(columns, cards, strict=False):
        with column:
            st.markdown(
                styles.card(
                    f'{styles.eyebrow(target)}<h3 style="margin:0.1rem 0 0.4rem 0">{title}</h3>'
                    f'<p class="ttt-muted" style="min-height:2.6rem">{body}</p>'
                ),
                unsafe_allow_html=True,
            )
            if st.button(f"Go to {target}", key=f"home-cta-{target}", **stretch()):
                navigate_to(target)

    st.write("")
    stats = ctx.db.get_stats(ctx.identity.player_id)
    challenge = challenge_for()
    left, right = st.columns([2, 1], gap="medium")
    with left:
        st.markdown(styles.eyebrow("Your record"), unsafe_allow_html=True)
        metrics = st.columns(4)
        metrics[0].metric("Games", stats.games_played)
        metrics[1].metric("Win rate", f"{stats.win_rate}%")
        metrics[2].metric("Best streak", stats.longest_streak)
        metrics[3].metric("Last played", relative_time(stats.last_played))
    with right:
        st.markdown(
            styles.card(
                f"{styles.eyebrow('Today&rsquo;s challenge')}"
                f'<strong style="font-family:var(--ttt-display-font)">{challenge.title}</strong>'
                f'<p class="ttt-muted">{challenge.description}</p>'
            ),
            unsafe_allow_html=True,
        )


def new_local_game(ctx: Context, mode: GameMode, size: int, timed: bool) -> Game:
    """Create a fresh single-device match from the current preferences."""
    identity = ctx.identity
    human = Player(
        name=identity.name,
        mark=Mark.X,
        kind=PlayerType.HUMAN,
        avatar=identity.avatar,
        player_id=identity.player_id,
    )
    if mode is GameMode.SINGLE:
        opponent = Player.ai(Mark.O, ctx.prefs.difficulty_enum)
    else:
        opponent = Player(
            name=st.session_state.get("guest_name", "Player Two"),
            mark=Mark.O,
            kind=PlayerType.HUMAN,
            avatar=st.session_state.get("guest_avatar", AVATARS[1]),
        )
    game = Game.new(
        mode=mode,
        x_player=human,
        o_player=opponent,
        size=size,
        difficulty=ctx.prefs.difficulty_enum,
        timed=timed,
        seconds_per_move=ctx.prefs.seconds_per_move,
    )
    st.session_state["game"] = game
    st.session_state["undo_count"] = 0
    st.session_state["last_move"] = None
    st.session_state.pop("_recorded", None)
    return game


def page_play(ctx: Context) -> None:
    """Human vs AI and human vs human on one device."""
    prefs = ctx.prefs
    st.markdown(styles.eyebrow("Match setup"), unsafe_allow_html=True)

    setup = st.columns([1.2, 1.2, 1, 1, 1.1])
    mode_label = setup[0].selectbox(
        "Opponent", ("Computer", "Friend on this device"), key="play_mode"
    )
    mode = GameMode.SINGLE if mode_label == "Computer" else GameMode.LOCAL

    difficulties = list(Difficulty)
    prefs.difficulty = setup[1].selectbox(
        "Difficulty",
        difficulties,
        index=difficulties.index(prefs.difficulty_enum),
        format_func=lambda d: d.label,
        disabled=mode is GameMode.LOCAL,
    ).value
    sizes = list(BOARD_SIZES)
    prefs.board_size = setup[2].selectbox(
        "Board",
        sizes,
        index=sizes.index(prefs.board_size) if prefs.board_size in sizes else 0,
        format_func=lambda s: f"{s}×{s}",
    )
    prefs.timed = setup[3].toggle("Timed", value=prefs.timed)
    save_prefs(prefs)
    start_clicked = setup[4].button("New game", type="primary", key="play_new", **stretch())

    game: Game | None = st.session_state.get("game")
    if start_clicked or game is None or game.mode is GameMode.ONLINE:
        game = new_local_game(ctx, mode, prefs.board_size, prefs.timed)
    elif game.mode is not mode or game.board.size != prefs.board_size:
        game = new_local_game(ctx, mode, prefs.board_size, prefs.timed)

    if mode is GameMode.SINGLE and game.engine is not None:
        game.engine.difficulty = prefs.difficulty_enum
        game.players[Mark.O].difficulty = prefs.difficulty_enum
        game.players[Mark.O].name = f"{prefs.difficulty_enum.label} Bot"

    st.write("")
    render_seats(game)

    if prefs.timed and not game.is_over:
        _render_timer(game)

    if game.is_over:
        tone, title, subtitle = result_tone(game, Mark.X)
        st.markdown(styles.banner(title, subtitle, tone), unsafe_allow_html=True)
    elif game.is_ai_turn:
        st.markdown(styles.thinking(f"{game.current_player.name} is thinking"), unsafe_allow_html=True)
    else:
        st.markdown(
            styles.status_line(
                f"{game.current_player.display} to move  ({game.current.value})", "▸"
            ),
            unsafe_allow_html=True,
        )

    clicked = render_board(
        game.board,
        winning_line=game.winning_line or (),
        last_move=st.session_state.get("last_move"),
        frozen=game.is_over or game.is_ai_turn,
        animations=prefs.animations,
    )

    if clicked is not None:
        _apply_human_move(ctx, game, clicked)

    if game.is_ai_turn and not game.is_over:
        time.sleep(min(0.7, (game.players[game.current].difficulty or Difficulty.MEDIUM).think_seconds))
        try:
            move = game.play_ai()
            st.session_state["last_move"] = move.index
            st.session_state["pending_sound"] = SoundName.MOVE
        except (InvalidMoveError, ValueError) as exc:
            st.warning(str(exc))
        _after_move(ctx, game)
        rerun()

    st.write("")
    actions = st.columns(5)
    if actions[0].button("Restart", key="play_restart", **stretch()):
        game.restart(swap_starter=False)
        st.session_state["last_move"] = None
        st.session_state["undo_count"] = 0
        st.session_state.pop("_recorded", None)
        rerun()
    if actions[1].button("Undo", key="play_undo", disabled=not game.can_undo(), **stretch()):
        if game.undo():
            st.session_state["undo_count"] = st.session_state.get("undo_count", 0) + 1
            st.session_state["last_move"] = game.moves[-1].index if game.moves else None
            st.session_state.pop("_recorded", None)
        rerun()
    if actions[2].button("Resign", key="play_resign", disabled=game.is_over, **stretch()):
        game.resign(Mark.X)
        _after_move(ctx, game)
        rerun()
    if actions[3].button("Swap sides", key="play_swap", **stretch()):
        game.restart(swap_starter=True)
        st.session_state["last_move"] = None
        st.session_state.pop("_recorded", None)
        rerun()
    if actions[4].button("Reset score", key="play_reset_score", **stretch()):
        ctx.scoreboard.reset()
        rerun()

    st.write("")
    left, right = st.columns([1, 1], gap="medium")
    with left:
        st.markdown(styles.eyebrow("Session score"), unsafe_allow_html=True)
        score = st.columns(4)
        score[0].metric(f"{game.players[Mark.X].avatar} X", ctx.scoreboard.x_wins)
        score[1].metric(f"{game.players[Mark.O].avatar} O", ctx.scoreboard.o_wins)
        score[2].metric("Draws", ctx.scoreboard.draws)
        score[3].metric("Best streak", ctx.scoreboard.best_streak)
        if game.engine is not None and game.engine.stats.nodes:
            engine_stats = game.engine.stats
            st.caption(
                f"Last search: {engine_stats.strategy} · {engine_stats.nodes:,} nodes · "
                f"depth {engine_stats.depth} · {engine_stats.elapsed * 1000:.0f} ms"
            )
    with right:
        st.markdown(styles.eyebrow("Move history"), unsafe_allow_html=True)
        st.markdown(
            styles.move_log([move.to_dict() for move in game.moves]), unsafe_allow_html=True
        )

    _flush_media(ctx, game)


@fragment(run_every=1.0)
def _render_timer(game: Game) -> None:
    """Countdown for timed mode; auto-plays a random move on expiry."""
    remaining = game.time_left
    st.progress(
        min(1.0, remaining / max(1, game.seconds_per_move)),
        text=f"{game.current_player.name}: {remaining:.0f}s left",
    )
    if remaining <= 0 and not game.is_over:
        try:
            move = game.play_timeout()
            st.session_state["last_move"] = move.index
        except (InvalidMoveError, IndexError):
            pass
        app_rerun()


def _apply_human_move(ctx: Context, game: Game, index: int) -> None:
    """Handle a board click from a local human player."""
    try:
        move = game.play(index)
    except InvalidMoveError as exc:
        st.warning(str(exc))
        return
    st.session_state["last_move"] = move.index
    st.session_state["pending_sound"] = SoundName.MOVE
    _after_move(ctx, game)
    rerun()


def _after_move(ctx: Context, game: Game) -> None:
    """Bookkeeping once a move has been made."""
    if not game.is_over:
        return
    ctx.scoreboard.record(game.result)
    record_result(ctx, game, perspective=Mark.X)
    if game.result is GameResult.DRAW:
        st.session_state["pending_sound"] = SoundName.DRAW
    elif game.winner is Mark.X:
        st.session_state["pending_sound"] = SoundName.WIN
        st.session_state["pending_confetti"] = True
    else:
        st.session_state["pending_sound"] = SoundName.LOSE


def _flush_media(ctx: Context, game: Game | None = None) -> None:
    """Play any queued sound / confetti for this render."""
    sound = st.session_state.pop("pending_sound", None)
    if sound:
        play_sound(sound, ctx.prefs.sound)
    if st.session_state.pop("pending_confetti", False):
        confetti(ctx.prefs.animations)


# --------------------------------------------------------------------------- #
# Online multiplayer
# --------------------------------------------------------------------------- #
def page_online(ctx: Context) -> None:
    """Room lobby and live match view."""
    st.markdown(styles.eyebrow("Online multiplayer"), unsafe_allow_html=True)

    deep_link = st.query_params.get("room")
    if isinstance(deep_link, list):
        deep_link = deep_link[0] if deep_link else None
    if deep_link and not st.session_state.get("room_code"):
        st.session_state["join_code_input"] = str(deep_link).upper()

    if st.session_state.get("room_code"):
        _render_room(ctx)
        return

    ctx.db.cleanup_expired_rooms()
    create, join = st.columns(2, gap="large")

    with create:
        st.markdown(styles.card(f'{styles.eyebrow("Host")}<h3 style="margin:0">Create a room</h3>'
                                '<p class="ttt-muted">You play X and move first.Share the code '
                                'with your opponent anyone else who opens it can spectate.</p>'),
                    unsafe_allow_html=True)
        sizes = list(BOARD_SIZES)
        size = st.selectbox(
            "Board size", sizes, format_func=lambda s: f"{s}×{s}", key="create_size"
        )
        if st.button("Create room", type="primary", key="create_room", **stretch()):
            try:
                state = ctx.rooms.create_room(
                    ctx.identity.player_id, ctx.identity.name, ctx.identity.avatar, size
                )
                st.session_state["room_code"] = state.code
                st.session_state.pop("_recorded", None)
                rerun()
            except RoomError as exc:
                st.error(str(exc))

    with join:
        st.markdown(styles.card(f'{styles.eyebrow("Guest")}<h3 style="margin:0">Join a room</h3>'
                                '<p class="ttt-muted">Enter the five-character code your opponent '
                                'shared. You play O.</p>'),
                    unsafe_allow_html=True)
        code = st.text_input("Room code", max_chars=8, key="join_code_input").upper()
        buttons = st.columns(2)
        if buttons[0].button("Join", type="primary", key="join_room", **stretch()):
            _try_join(ctx, code, spectate=False)
        if buttons[1].button("Spectate", key="spectate_room", **stretch()):
            _try_join(ctx, code, spectate=True)

    open_rooms = ctx.rooms.open_rooms()
    if open_rooms:
        st.write("")
        st.markdown(styles.eyebrow("Rooms waiting for a player"), unsafe_allow_html=True)
        for row in open_rooms:
            columns = st.columns([1, 2, 1, 1])
            columns[0].markdown(f"**{row['code']}**")
            columns[1].markdown(
                f"{row.get('host_avatar', '')} {row.get('host_name', 'Host')} · "
                f"{row.get('size', 3)}×{row.get('size', 3)}"
            )
            columns[2].markdown(
                f'<span class="ttt-muted">{relative_time(row.get("created_at"))}</span>',
                unsafe_allow_html=True,
            )
            if columns[3].button("Join", key=f"quick-join-{row['code']}", **stretch()):
                _try_join(ctx, row["code"], spectate=False)


def _try_join(ctx: Context, code: str, spectate: bool) -> None:
    """Join or spectate, surfacing any rule violation as a message."""
    try:
        code = normalise_code(code)
        if spectate:
            ctx.rooms.spectate(code)
            st.session_state["spectating"] = True
        else:
            ctx.rooms.join_room(code, ctx.identity.player_id, ctx.identity.name, ctx.identity.avatar)
            st.session_state["spectating"] = False
        st.session_state["room_code"] = code
        st.session_state.pop("_recorded", None)
        rerun()
    except RoomError as exc:
        st.error(str(exc))


def _render_room(ctx: Context) -> None:
    """Container for the polling room view plus the leave control."""
    code = st.session_state["room_code"]
    header = st.columns([3, 1])
    header[0].markdown(
        f'{styles.eyebrow("Room")}<span class="ttt-mono" style="font-size:1.4rem;'
        f'letter-spacing:0.35rem">{code}</span>',
        unsafe_allow_html=True,
    )
    if header[1].button("Leave room", key="leave_room", **stretch()):
        try:
            ctx.rooms.leave_room(code, ctx.identity.player_id)
        finally:
            st.session_state.pop("room_code", None)
            st.session_state.pop("spectating", None)
            st.session_state.pop("_recorded", None)
        rerun()

    _room_view(ctx, code)
    _flush_media(ctx)


@fragment(run_every=ONLINE_POLL_SECONDS)
def _room_view(ctx: Context, code: str) -> None:
    """Live room: board,presence,chat and controls.Polls the backend."""
    try:
        state = ctx.rooms.heartbeat(code, ctx.identity.player_id)
    except RoomError as exc:
        st.error(str(exc))
        st.session_state.pop("room_code", None)
        return
    except BackendUnavailableError:
        # The fragment polls every couple of seconds, so a dropped connection
        # heals itself. Say so quietly and keep the room open rather than
        # throwing the player out over a moment of bad signal.
        st.markdown(styles.status_line("Reconnecting…"), unsafe_allow_html=True)
        st.caption("Lost contact with the server. Retrying automatically — your game is safe.")
        return

    role = state.role_of(ctx.identity.player_id)
    my_mark = state.mark_of(ctx.identity.player_id)

    cards = [
        styles.seat(
            state.host_name, state.host_avatar, Mark.X,
            "Host" + (" · you" if role is RoomRole.HOST else ""),
            active=state.current_turn is Mark.X and not state.is_finished,
            online=state.is_online(Mark.X),
        ),
        styles.seat(
            state.guest_name or "Waiting…", state.guest_avatar or "❔", Mark.O,
            "Guest" + (" · you" if role is RoomRole.GUEST else ""),
            active=state.current_turn is Mark.O and not state.is_finished,
            online=state.is_online(Mark.O),
        ),
    ]
    st.markdown(styles.seats_row(cards), unsafe_allow_html=True)

    if not state.both_seated:
        st.markdown(styles.room_code(state.code), unsafe_allow_html=True)
        st.markdown(
            styles.status_line("Waiting for an opponent to join with this code…", "◴"),
            unsafe_allow_html=True,
        )
    elif state.is_finished:
        _render_room_result(ctx, state, my_mark)
    elif role is RoomRole.SPECTATOR:
        st.markdown(
            styles.status_line(f"Spectating · {state.name_of(state.current_turn)} to move", "👁"),
            unsafe_allow_html=True,
        )
    elif state.is_turn_of(ctx.identity.player_id):
        st.markdown(styles.status_line("Your move.", "▸"), unsafe_allow_html=True)
    else:
        opponent = state.name_of(state.current_turn)
        online = state.is_online(state.current_turn)
        st.markdown(
            styles.status_line(
                f"Waiting for {opponent}…" + ("" if online else "  (disconnected)"), "◴"
            ),
            unsafe_allow_html=True,
        )

    clicked = render_board(
        state.board,
        winning_line=state.winning_line,
        frozen=not state.is_turn_of(ctx.identity.player_id),
        animations=ctx.prefs.animations,
    )
    if clicked is not None:
        try:
            state = ctx.rooms.make_move(code, ctx.identity.player_id, clicked)
            st.session_state["pending_sound"] = SoundName.MOVE
        except (RoomError, InvalidMoveError) as exc:
            st.warning(str(exc))
        app_rerun()

    controls = st.columns(3)
    if role is not RoomRole.SPECTATOR:
        rematch_label = "Play again"
        if state.is_finished and my_mark is not None and state.rematch_of(my_mark):
            rematch_label = "Waiting for opponent…"
        if controls[0].button(
            rematch_label,
            key="room_rematch",
            disabled=not state.is_finished or (my_mark is not None and state.rematch_of(my_mark)),
            **stretch(),
        ):
            try:
                ctx.rooms.request_rematch(code, ctx.identity.player_id)
                st.session_state.pop("_recorded", None)
            except RoomError as exc:
                st.warning(str(exc))
            app_rerun()
        if controls[1].button(
            "Resign", key="room_resign", disabled=state.is_finished or not state.both_seated, **stretch()
        ):
            try:
                ctx.rooms.resign(code, ctx.identity.player_id)
            except RoomError as exc:
                st.warning(str(exc))
            app_rerun()
    if controls[2].button("Refresh", key="room_refresh", **stretch()):
        app_rerun()

    st.write("")
    left, right = st.columns([1, 1], gap="medium")
    with left:
        st.markdown(styles.eyebrow("Move history"), unsafe_allow_html=True)
        moves = [
            {
                "number": row.get("move_number"),
                "mark": row.get("mark"),
                "cell": _cell_label(int(row.get("cell_index", 0)), state.board.size),
                "player_name": state.name_of(Mark(row.get("mark", "X"))),
            }
            for row in ctx.rooms.move_history(code)
        ]
        st.markdown(styles.move_log(moves), unsafe_allow_html=True)
    with right:
        st.markdown(styles.eyebrow("Chat"), unsafe_allow_html=True)
        st.markdown(
            styles.chat_log(ctx.rooms.messages(code), ctx.identity.player_id),
            unsafe_allow_html=True,
        )
        with st.form("room_chat_form", clear_on_submit=True):
            message = st.text_input(
                "Message", label_visibility="collapsed", placeholder="Say something…"
            )
            if st.form_submit_button("Send", **stretch()) and message.strip():
                ctx.rooms.send_message(code, ctx.identity.player_id, ctx.identity.name, message)
                app_rerun()
        reaction_columns = st.columns(len(EMOJI_REACTIONS))
        for column, emoji in zip(reaction_columns, EMOJI_REACTIONS, strict=False):
            if column.button(emoji, key=f"react-{emoji}", **stretch()):
                ctx.rooms.send_reaction(code, ctx.identity.player_id, ctx.identity.name, emoji)
                app_rerun()


def _cell_label(index: int, size: int) -> str:
    """Chess-style label for an online move."""
    row, col = divmod(index, size)
    return f"{chr(ord('A') + col)}{row + 1}"


def _render_room_result(ctx: Context, state: RoomState, my_mark: Mark | None) -> None:
    """Result banner for an online game,and one-time result recording."""
    if state.winner is None:
        tone, title = "draw", "Stalemate"
    else:
        winner_name = state.name_of(state.winner)
        tone = "win" if my_mark is state.winner else "loss"
        title = f"{state.avatar_of(state.winner)} {winner_name} wins"
    st.markdown(
        styles.banner(title, f"{state.move_count} moves · room {state.code}", tone),
        unsafe_allow_html=True,
    )

    if my_mark is None:
        return
    marker = f"online:{state.code}:{state.move_count}:{state.winner.value if state.winner else 'draw'}"
    if st.session_state.get("_recorded") == marker:
        return
    st.session_state["_recorded"] = marker

    result = "draw" if state.winner is None else ("win" if state.winner is my_mark else "loss")
    record = GameRecord(
        player_id=ctx.identity.player_id,
        mode=GameMode.ONLINE.value,
        result=result,
        player_mark=my_mark.value,
        opponent=state.name_of(my_mark.opponent),
        size=state.board.size,
        moves=state.move_count,
        duration=max(0.0, (state.updated_at - state.created_at).total_seconds()),
        replay={
            "mode": GameMode.ONLINE.value,
            "size": state.board.size,
            "win_length": state.board.win_length,
            "winner": state.winner.value if state.winner else "",
            "winning_line": list(state.winning_line),
            "players": {
                "X": {"name": state.host_name, "avatar": state.host_avatar, "mark": "X"},
                "O": {"name": state.guest_name, "avatar": state.guest_avatar, "mark": "O"},
            },
            "moves": [
                {
                    "number": row.get("move_number"),
                    "index": row.get("cell_index"),
                    "mark": row.get("mark"),
                    "player_name": state.name_of(Mark(row.get("mark", "X"))),
                    "cell": _cell_label(int(row.get("cell_index", 0)), state.board.size),
                }
                for row in ctx.rooms.move_history(state.code)
            ],
        },
        room_code=state.code,
    )
    try:
        ctx.db.record_game(record)
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not record online game: %s", exc)
        return

    if result == "win":
        st.session_state["pending_confetti"] = True
        st.session_state["pending_sound"] = SoundName.WIN
    elif result == "loss":
        st.session_state["pending_sound"] = SoundName.LOSE
    else:
        st.session_state["pending_sound"] = SoundName.DRAW

    stats = ctx.db.get_stats(ctx.identity.player_id)
    _unlock_achievements(
        ctx,
        stats,
        {
            "result": result,
            "difficulty": "",
            "size": state.board.size,
            "moves": state.move_count,
            "mode": GameMode.ONLINE.value,
            "undos": 0,
        },
    )


# --------------------------------------------------------------------------- #
# Statistics, leaderboard, achievements, challenge, replays
# --------------------------------------------------------------------------- #
def page_stats(ctx: Context) -> None:
    """Charts and aggregates for the signed-in player."""
    stats = ctx.db.get_stats(ctx.identity.player_id)
    st.markdown(styles.eyebrow("Your statistics"), unsafe_allow_html=True)

    if not stats.games_played:
        st.markdown(
            styles.card(
                '<h3 style="margin:0">No games yet</h3>'
                '<p class="ttt-muted">Play a match and your record will appear here — '
                'win rate, streaks, average length and a full replay archive.</p>'
            ),
            unsafe_allow_html=True,
        )
        return

    top = st.columns(4)
    top[0].metric("Games played", stats.games_played)
    top[1].metric("Wins", stats.wins, delta=f"{stats.win_rate}% win rate")
    top[2].metric("Losses / draws", f"{stats.losses} / {stats.draws}")
    top[3].metric("Longest streak", stats.longest_streak, delta=f"current {stats.current_streak}")

    second = st.columns(3)
    second[0].metric("Average moves", stats.avg_moves)
    second[1].metric("Average length", format_duration(stats.avg_duration))
    second[2].metric("Last played", relative_time(stats.last_played))

    st.write("")
    charts = st.columns([1, 1], gap="medium")
    with charts[0]:
        st.markdown(styles.eyebrow("Outcomes"), unsafe_allow_html=True)
        outcome_df = pd.DataFrame(
            {"Outcome": ["Wins", "Losses", "Draws"], "Games": [stats.wins, stats.losses, stats.draws]}
        ).set_index("Outcome")
        st.bar_chart(outcome_df, height=260)
    with charts[1]:
        st.markdown(styles.eyebrow("Results by difficulty"), unsafe_allow_html=True)
        rows = [
            {"Difficulty": key, "Result": result, "Games": count}
            for key, bucket in stats.by_difficulty.items()
            for result, count in bucket.items()
        ]
        if rows:
            pivot = pd.DataFrame(rows).pivot_table(
                index="Difficulty", columns="Result", values="Games", aggfunc="sum", fill_value=0
            )
            st.bar_chart(pivot, height=260)
        else:
            st.caption("Play a few games to populate this chart.")

    st.markdown(styles.eyebrow("Games over time"), unsafe_allow_html=True)
    timeline = pd.DataFrame(stats.timeline)
    if not timeline.empty:
        daily = (
            timeline.assign(count=1)
            .pivot_table(index="date", columns="result", values="count", aggfunc="sum", fill_value=0)
            .sort_index()
        )
        st.area_chart(daily, height=260)

    st.write("")
    games = ctx.db.list_games(ctx.identity.player_id, limit=500)
    st.markdown(styles.eyebrow("Export"), unsafe_allow_html=True)
    exports = st.columns(2)
    exports[0].download_button(
        "Download CSV",
        data=games_to_csv(games),
        file_name="gridlock-history.csv",
        mime="text/csv",
        key="export_csv",
        **stretch(),
    )
    exports[1].download_button(
        "Download JSON (with replays)",
        data=games_to_json(games),
        file_name="gridlock-history.json",
        mime="application/json",
        key="export_json",
        **stretch(),
    )


def page_leaderboard(ctx: Context) -> None:
    """Global rankings and recent activity."""
    st.markdown(styles.eyebrow("Leaderboard"), unsafe_allow_html=True)
    wins_tab, rate_tab, recent_tab = st.tabs(["Top wins", "Top win rate", "Recent matches"])

    with wins_tab:
        rows = ctx.db.get_leaderboard("wins", limit=25)
        _render_leaderboard(rows, ctx.identity.player_id)
    with rate_tab:
        rows = ctx.db.get_leaderboard("win_rate", limit=25)
        _render_leaderboard(rows, ctx.identity.player_id, rate_first=True)
    with recent_tab:
        matches = ctx.db.recent_matches(limit=20)
        if not matches:
            st.caption("No matches recorded yet.")
        else:
            table = pd.DataFrame(
                [
                    {
                        "Player": f"{row.get('avatar', '')} {row.get('name', 'Player')}",
                        "Mode": row.get("mode", ""),
                        "Result": row.get("result", ""),
                        "Opponent": row.get("opponent", ""),
                        "Board": f"{row.get('size', 3)}×{row.get('size', 3)}",
                        "Moves": row.get("moves", 0),
                        "When": relative_time(row.get("created_at")),
                    }
                    for row in matches
                ]
            )
            st.dataframe(table, hide_index=True, **stretch())


def _render_leaderboard(rows: Sequence[dict], my_id: str, rate_first: bool = False) -> None:
    """Shared leaderboard table renderer."""
    if not rows:
        st.caption("Nobody has finished a game yet. Be the first.")
        return
    table = pd.DataFrame(
        [
            {
                "#": position,
                "Player": f"{row.get('avatar', '')} {row.get('name', 'Player')}"
                + ("  ← you" if row.get("player_id") == my_id else ""),
                "Win rate": f"{row.get('win_rate', 0)}%",
                "Wins": row.get("wins", 0),
                "Games": row.get("games", 0),
                "Best streak": row.get("best_streak", 0),
            }
            for position, row in enumerate(rows, start=1)
        ]
    )
    if rate_first:
        table = table[["#", "Player", "Win rate", "Wins", "Games", "Best streak"]]
    st.dataframe(table, hide_index=True, **stretch())


def page_achievements(ctx: Context) -> None:
    """Badge wall."""
    st.markdown(styles.eyebrow("Achievements"), unsafe_allow_html=True)
    unlocked = {row["code"] for row in ctx.db.list_achievements(ctx.identity.player_id)}
    st.caption(f"{len(unlocked)} of {len(ACHIEVEMENTS)} unlocked")

    columns = st.columns(4, gap="small")
    for position, achievement in enumerate(ACHIEVEMENTS):
        with columns[position % 4]:
            st.markdown(
                styles.badge(
                    achievement.icon,
                    achievement.title,
                    achievement.description,
                    achievement.code in unlocked,
                ),
                unsafe_allow_html=True,
            )
            st.write("")


def page_challenge(ctx: Context) -> None:
    """Today's objective."""
    today = datetime.now(timezone.utc).date()
    challenge = challenge_for(today)
    progress = ctx.db.get_challenge(ctx.identity.player_id, today.isoformat())
    done = bool(progress and progress.get("completed"))

    st.markdown(styles.eyebrow(f"Daily challenge · {today.isoformat()}"), unsafe_allow_html=True)
    st.markdown(
        styles.card(
            f'<h2 style="margin:0 0 0.3rem 0">{challenge.title}</h2>'
            f'<p class="ttt-muted">{challenge.description}</p>'
            f'<p><strong style="color:{"var(--ttt-success)" if done else "var(--ttt-accent)"}">'
            f'{"Completed" if done else "Not completed yet"}</strong></p>'
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        f"Suggested setup: {challenge.difficulty.label} bot on a "
        f"{challenge.size}×{challenge.size} board."
    )
    if st.button("Set it up and play", type="primary", key="challenge_play"):
        prefs = ctx.prefs
        prefs.difficulty = challenge.difficulty.value
        prefs.board_size = challenge.size
        save_prefs(prefs)
        st.session_state.pop("game", None)
        st.session_state.pop("play_mode", None)
        navigate_to("Play")


def page_replays(ctx: Context) -> None:
    """Step through any archived game."""
    st.markdown(styles.eyebrow("Replays"), unsafe_allow_html=True)
    games = ctx.db.list_games(ctx.identity.player_id, limit=50)
    if not games:
        st.caption("Finished games appear here with a move-by-move replay.")
        return

    def _describe(row: dict) -> str:
        return (
            f"{relative_time(row.get('created_at'))} · {row.get('mode', '')} · "
            f"{row.get('result', '')} vs {row.get('opponent', '—')} "
            f"({row.get('size', 3)}×{row.get('size', 3)})"
        )

    choice = st.selectbox("Game", games, format_func=_describe, key="replay_choice")
    replay = load_replay(choice)
    moves = replay.get("moves", [])
    if not moves:
        st.caption("This game has no stored move list.")
        return

    states = Game.board_states(replay)
    ply = st.slider("Move", 0, len(states) - 1, len(states) - 1, key="replay_ply")
    winning_line = replay.get("winning_line", []) if ply == len(states) - 1 else []

    columns = st.columns([1, 1], gap="large")
    with columns[0]:
        st.markdown(render_static_board(states[ply], winning_line), unsafe_allow_html=True)
    with columns[1]:
        st.markdown(styles.eyebrow("Moves"), unsafe_allow_html=True)
        st.markdown(styles.move_log(moves[:ply]), unsafe_allow_html=True)
        st.caption(
            f"Result: {replay.get('result', '—')} · "
            f"{replay.get('duration_seconds', 0)}s · game {replay.get('game_id', '')}"
        )


def page_settings(ctx: Context) -> None:
    """Profile, appearance, gameplay and account."""
    prefs = ctx.prefs
    st.markdown(styles.eyebrow("Settings"), unsafe_allow_html=True)

    profile, appearance = st.columns(2, gap="large")

    with profile:
        st.subheader("Profile")
        name = st.text_input("Display name", value=ctx.identity.name, max_chars=20, key="settings_name")
        st.markdown('<div class="ttt-eyebrow">Avatar</div>', unsafe_allow_html=True)
        avatar = st.radio(
            "Avatar",
            AVATARS,
            index=AVATARS.index(ctx.identity.avatar) if ctx.identity.avatar in AVATARS else 0,
            horizontal=True,
            label_visibility="collapsed",
        )
        if st.button("Save profile", type="primary", key="settings_save"):
            ctx.auth.update_profile(name=sanitize_name(name, ctx.identity.name), avatar=avatar)
            toast("Profile updated", icon="✅")
            rerun()

        st.write("")
        st.subheader("Second player")
        st.session_state["guest_name"] = st.text_input(
            "Name for local two-player games",
            value=st.session_state.get("guest_name", "Player Two"),
            max_chars=20,
            key="settings_guest_name",
        )
        st.session_state["guest_avatar"] = st.selectbox(
            "Their avatar",
            AVATARS,
            index=AVATARS.index(st.session_state.get("guest_avatar", AVATARS[1])),
            key="settings_guest_avatar",
        )

    with appearance:
        st.subheader("Appearance")
        if LOCKED_THEME is None:
            theme_names = list(ThemeName)
            prefs.theme = st.selectbox(
                "Theme",
                theme_names,
                index=theme_names.index(prefs.theme_name),
                format_func=lambda name: get_theme(name).label,
            ).value
        st.markdown(styles.theme_swatch(prefs.theme_name), unsafe_allow_html=True)
        prefs.sound = st.toggle("Sound effects", value=prefs.sound)
        prefs.animations = st.toggle("Animations", value=prefs.animations)

        st.write("")
        st.subheader("Gameplay")
        difficulties = list(Difficulty)
        prefs.difficulty = st.select_slider(
            "Default difficulty",
            options=difficulties,
            value=prefs.difficulty_enum,
            format_func=lambda d: d.label,
        ).value
        st.caption(prefs.difficulty_enum.description)
        sizes = list(BOARD_SIZES)
        prefs.board_size = st.selectbox(
            "Default board size",
            sizes,
            index=sizes.index(prefs.board_size) if prefs.board_size in sizes else 0,
            format_func=lambda s: f"{s}×{s} (line of {BOARD_SIZES[s]})",
        )
        prefs.timed = st.toggle("Timed mode", value=prefs.timed)
        prefs.seconds_per_move = st.slider(
            "Seconds per move", 5, 60, prefs.seconds_per_move, step=5
        )
        save_prefs(prefs)

    st.write("")
    st.subheader("Account")
    if ctx.identity.is_guest:
        st.caption(
            "You are playing as a guest, so this browser session is the only thing "
            "holding your history. Create an account to keep stats, achievements and "
            "streaks across devices."
        )
    else:
        st.caption(f"Signed in as {ctx.identity.email} · progress is saved to your account.")

    if not ctx.auth.accounts_available:
        st.info(
            "Accounts need Supabase. Add SUPABASE_URL and SUPABASE_KEY to your .env "
            "file and restart to enable sign-up."
        )
    elif ctx.identity.is_guest:
        sign_in_tab, sign_up_tab = st.tabs(["Sign in", "Create account"])

        with sign_in_tab:
            with st.form("sign_in_form"):
                email = st.text_input("Email", key="sign_in_email")
                password = st.text_input("Password", type="password", key="sign_in_password")
                if st.form_submit_button("Sign in", **stretch()):
                    try:
                        ctx.auth.sign_in(email, password)
                    except AuthError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop("scoreboard", None)
                        toast("Welcome back", icon="👋")
                        app_rerun()

        with sign_up_tab:
            with st.form("sign_up_form"):
                new_email = st.text_input("Email", key="sign_up_email")
                new_password = st.text_input(
                    "Password", type="password", key="sign_up_password",
                    help="At least 8 characters.",
                )
                if st.form_submit_button("Create account", **stretch()):
                    try:
                        ctx.auth.sign_up(
                            new_email, new_password, ctx.identity.name, ctx.identity.avatar
                        )
                    except AuthError as exc:
                        st.warning(str(exc))
                    else:
                        st.session_state.pop("scoreboard", None)
                        toast("Account created", icon="🎉")
                        app_rerun()
    else:
        if st.button("Sign out", key="settings_signout_account", **stretch()):
            ctx.auth.sign_out()
            st.session_state.pop("scoreboard", None)
            toast("Signed out", icon="👋")
            app_rerun()

    account = st.columns(2)
    if ctx.auth.google_available and ctx.identity.is_guest:
        url = ctx.auth.google_sign_in_url()
        if url:
            account[0].link_button("Sign in with Google", url, **stretch())
    if ctx.identity.is_guest and account[0].button(
        "Start a fresh guest session", key="settings_signout", **stretch()
    ):
        ctx.auth.sign_out()
        st.session_state.pop("scoreboard", None)
        toast("New guest session created", icon="👋")
        app_rerun()
    account[1].caption(f"Player ID · {ctx.identity.short_id}")

    with st.expander("Diagnostics"):
        settings = get_settings()
        st.write(
            {
                "version": VERSION,
                "storage_backend": ctx.db.name,
                "supabase_configured": settings.supabase_configured,
                "google_auth": ctx.auth.google_available,
                "data_dir": str(settings.data_dir),
                "streamlit": st.__version__,
            }
        )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
PAGE_HANDLERS: dict[str, Callable[[Context], None]] = {
    "Home": page_home,
    "Play": page_play,
    "Online": page_online,
    "Statistics": page_stats,
    "Leaderboard": page_leaderboard,
    "Achievements": page_achievements,
    "Daily challenge": page_challenge,
    "Replays": page_replays,
    "Settings": page_settings,
}


def main() -> None:
    """Configure the page, build the context and dispatch to the active page."""
    st.set_page_config(
        page_title=f"{APP_NAME} · Tic-Tac-Toe",
        page_icon="✖️",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={"About": f"{APP_NAME} v{VERSION} — {APP_TAGLINE}"},
    )
    st.session_state.setdefault("current_page", "Home")
    ctx = get_context()
    st.markdown(
        styles.base_css(get_theme(ctx.prefs.theme_name), ctx.prefs.animations),
        unsafe_allow_html=True,
    )
    page = render_sidebar(ctx)
    try:
        PAGE_HANDLERS.get(page, page_home)(ctx)
    except BackendUnavailableError as exc:
        logger.warning("Backend unavailable while rendering %s: %s", page, exc)
        st.markdown(
            styles.banner("Connection lost", "The database is not responding."),
            unsafe_allow_html=True,
        )
        st.caption(
            "This is usually a brief network drop. Nothing has been lost — "
            "wait a moment and try again."
        )
        if st.button("Retry now", **stretch()):
            app_rerun()


if __name__ == "__main__":
    main()