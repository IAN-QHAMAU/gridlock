"""Persistence layer.

The application talks to :class:`Database`, an abstract interface implemented
twice:

* :class:`SupabaseDatabase` — the production backend (real online multiplayer,
  shared leaderboard, cross-device stats).
* :class:`LocalDatabase` — a zero-configuration SQLite fallback so the app runs
  immediately after `pip install -r requirements.txt`.  Online rooms then work
  between browser tabs on the same host.

Both return plain dictionaries with identical keys, so nothing above this layer
needs to know which backend is live.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from config import (
    MAX_CHAT_MESSAGES,
    ROOM_TIMEOUT_MINUTES,
    GameStatus,
    Settings,
    configure_logging,
    get_settings,
)

logger = configure_logging()

Row = dict[str, Any]


def utcnow() -> str:
    """Current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat()


#: Network failures that are worth retrying. A pooled HTTPS connection that the
#: server has quietly closed surfaces as `RemoteProtocolError` on next use,
#: which is common on mobile networks and after an idle spell.
class BackendUnavailableError(RuntimeError):
    """Raised when the database cannot be reached after retrying."""


def _transient_errors() -> tuple[type[BaseException], ...]:
    """Exception types that indicate a lost connection rather than bad data."""
    errors: list[type[BaseException]] = [ConnectionError, TimeoutError, OSError]
    try:
        import httpx

        errors += [
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.WriteError,
            httpx.PoolTimeout,
        ]
    except Exception:  # pragma: no cover - httpx ships with supabase
        pass
    return tuple(errors)


TRANSIENT_ERRORS: tuple[type[BaseException], ...] = _transient_errors()
RETRY_ATTEMPTS: int = 3
RETRY_BACKOFF_SECONDS: float = 0.3


def resilient(func: Callable[..., Any]) -> Callable[..., Any]:
    """Retry a backend call when the connection drops mid-request.

    PostgREST is reached over pooled HTTPS connections. When one goes stale —
    a phone switching cell, a laptop waking, or simply an idle keep-alive the
    server has already closed — the next request fails with "Server
    disconnected" before it is ever processed. Retrying is safe in that case
    and invisible to the player.

    The retry is deliberately shallow: three attempts over roughly a second.
    Anything longer and a polling UI would stack up requests faster than it
    clears them.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        delay = RETRY_BACKOFF_SECONDS
        last: BaseException | None = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return func(*args, **kwargs)
            except TRANSIENT_ERRORS as exc:
                last = exc
                if attempt == RETRY_ATTEMPTS:
                    break
                logger.warning(
                    "Transient backend error in %s (attempt %d/%d): %s",
                    func.__name__, attempt, RETRY_ATTEMPTS, exc,
                )
                time.sleep(delay)
                delay *= 2
        raise BackendUnavailableError(
            f"The database did not respond after {RETRY_ATTEMPTS} attempts."
        ) from last

    return wrapper


def parse_ts(value: Any) -> datetime:
    """Parse an ISO timestamp defensively (returns epoch on failure)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, timezone.utc)


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class GameRecord:
    """One finished game, from a single player's perspective."""

    player_id: str
    mode: str
    result: str  # win | loss | draw
    player_mark: str
    opponent: str
    difficulty: str = ""
    size: int = 3
    moves: int = 0
    duration: float = 0.0
    replay: dict[str, Any] = field(default_factory=dict)
    room_code: str = ""
    created_at: str = field(default_factory=utcnow)

    def to_row(self) -> Row:
        """Flatten for storage (replay is stored as JSON text)."""
        data = asdict(self)
        data["replay"] = json.dumps(self.replay)
        return data


@dataclass(slots=True)
class Stats:
    """Aggregated player statistics."""

    games_played: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    win_rate: float = 0.0
    current_streak: int = 0
    longest_streak: int = 0
    avg_moves: float = 0.0
    avg_duration: float = 0.0
    last_played: str = ""
    by_difficulty: dict[str, dict[str, int]] = field(default_factory=dict)
    by_mode: dict[str, int] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Row:
        """Dictionary form (handy for charts and exports)."""
        return asdict(self)


def compute_stats(rows: Sequence[Row]) -> Stats:
    """Aggregate raw game rows (newest first or oldest first) into :class:`Stats`."""
    if not rows:
        return Stats()

    ordered = sorted(rows, key=lambda r: parse_ts(r.get("created_at")))
    wins = sum(1 for r in ordered if r.get("result") == "win")
    losses = sum(1 for r in ordered if r.get("result") == "loss")
    draws = sum(1 for r in ordered if r.get("result") == "draw")
    played = len(ordered)

    longest = current = 0
    for row in ordered:
        if row.get("result") == "win":
            current += 1
            longest = max(longest, current)
        else:
            current = 0

    by_difficulty: dict[str, dict[str, int]] = {}
    by_mode: dict[str, int] = {}
    for row in ordered:
        diff = (row.get("difficulty") or "—")
        bucket = by_difficulty.setdefault(diff, {"win": 0, "loss": 0, "draw": 0})
        bucket[row.get("result", "draw")] = bucket.get(row.get("result", "draw"), 0) + 1
        mode = row.get("mode") or "single"
        by_mode[mode] = by_mode.get(mode, 0) + 1

    timeline = [
        {
            "date": parse_ts(row.get("created_at")).date().isoformat(),
            "result": row.get("result", "draw"),
            "moves": int(row.get("moves") or 0),
            "duration": float(row.get("duration") or 0.0),
            "mode": row.get("mode", "single"),
        }
        for row in ordered
    ]

    return Stats(
        games_played=played,
        wins=wins,
        losses=losses,
        draws=draws,
        win_rate=round(100.0 * wins / played, 1) if played else 0.0,
        current_streak=current,
        longest_streak=longest,
        avg_moves=round(sum(float(r.get("moves") or 0) for r in ordered) / played, 1),
        avg_duration=round(sum(float(r.get("duration") or 0) for r in ordered) / played, 1),
        last_played=str(ordered[-1].get("created_at", "")),
        by_difficulty=by_difficulty,
        by_mode=by_mode,
        timeline=timeline,
    )


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class DatabaseError(RuntimeError):
    """Raised when the storage backend fails irrecoverably."""


class Database(ABC):
    """Storage interface used by the rest of the application."""

    name: str = "abstract"
    supports_realtime_rooms: bool = False

    # -- players ---------------------------------------------------------- #
    @abstractmethod
    def ensure_player(self, player_id: str, name: str, avatar: str, is_guest: bool = True) -> Row:
        """Create or refresh a player row and return it."""

    @abstractmethod
    def get_player(self, player_id: str) -> Row | None:
        """Fetch a player row."""

    @abstractmethod
    def update_player(self, player_id: str, **fields: Any) -> None:
        """Patch player columns."""

    # -- games ------------------------------------------------------------ #
    @abstractmethod
    def record_game(self, record: GameRecord) -> None:
        """Persist a finished game and refresh the leaderboard."""

    @abstractmethod
    def list_games(self, player_id: str, limit: int = 200) -> list[Row]:
        """Most recent games for a player, newest first."""

    @abstractmethod
    def recent_matches(self, limit: int = 15) -> list[Row]:
        """Most recent games across all players."""

    def get_stats(self, player_id: str) -> Stats:
        """Aggregate statistics for a player."""
        return compute_stats(self.list_games(player_id, limit=1000))

    # -- leaderboard ------------------------------------------------------ #
    @abstractmethod
    def get_leaderboard(self, order_by: str = "wins", limit: int = 20) -> list[Row]:
        """Ranked players; ``order_by`` is ``wins`` or ``win_rate``."""

    # -- achievements & challenges ---------------------------------------- #
    @abstractmethod
    def unlock_achievement(self, player_id: str, code: str) -> bool:
        """Unlock an achievement. Returns True when newly unlocked."""

    @abstractmethod
    def list_achievements(self, player_id: str) -> list[Row]:
        """Achievements a player has unlocked."""

    @abstractmethod
    def get_challenge(self, player_id: str, day: str) -> Row | None:
        """Daily-challenge progress row for a given ISO date."""

    @abstractmethod
    def save_challenge(self, player_id: str, day: str, code: str, completed: bool) -> None:
        """Create or update daily-challenge progress."""

    # -- rooms ------------------------------------------------------------ #
    @abstractmethod
    def create_room(self, room: Row) -> Row:
        """Insert a new room."""

    @abstractmethod
    def get_room(self, code: str) -> Row | None:
        """Fetch a room by code."""

    @abstractmethod
    def update_room(self, code: str, **fields: Any) -> Row | None:
        """Patch room columns and return the updated row."""

    @abstractmethod
    def update_room_if(self, code: str, expected_move_count: int, **fields: Any) -> Row | None:
        """Conditional update used to serialise concurrent moves.

        Returns the updated row, or ``None`` when another client got there
        first (i.e. ``move_count`` no longer matches).
        """

    @abstractmethod
    def delete_room(self, code: str) -> None:
        """Remove a room and its child rows."""

    @abstractmethod
    def list_open_rooms(self, limit: int = 20) -> list[Row]:
        """Rooms still waiting for an opponent."""

    @abstractmethod
    def cleanup_expired_rooms(self, minutes: int = ROOM_TIMEOUT_MINUTES) -> int:
        """Delete stale rooms; returns how many were removed."""

    # -- moves & messages -------------------------------------------------- #
    @abstractmethod
    def add_move(self, room_code: str, move_number: int, cell_index: int, mark: str, player_id: str) -> None:
        """Append a move to a room's history."""

    @abstractmethod
    def list_moves(self, room_code: str) -> list[Row]:
        """A room's moves in play order."""

    @abstractmethod
    def add_message(self, room_code: str, player_id: str, name: str, kind: str, body: str) -> None:
        """Post a chat message or emoji reaction."""

    @abstractmethod
    def list_messages(self, room_code: str, limit: int = MAX_CHAT_MESSAGES) -> list[Row]:
        """Recent messages for a room, oldest first."""


# --------------------------------------------------------------------------- #
# SQLite implementation
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    avatar TEXT DEFAULT '🦊',
    is_guest INTEGER DEFAULT 1,
    email TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    result TEXT NOT NULL,
    player_mark TEXT NOT NULL,
    opponent TEXT DEFAULT '',
    difficulty TEXT DEFAULT '',
    size INTEGER DEFAULT 3,
    moves INTEGER DEFAULT 0,
    duration REAL DEFAULT 0,
    replay TEXT DEFAULT '{}',
    room_code TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_player ON games(player_id, created_at DESC);
CREATE TABLE IF NOT EXISTS leaderboard (
    player_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    avatar TEXT DEFAULT '🦊',
    games INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    draws INTEGER DEFAULT 0,
    win_rate REAL DEFAULT 0,
    best_streak INTEGER DEFAULT 0,
    current_streak INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (
    code TEXT PRIMARY KEY,
    host_id TEXT NOT NULL,
    host_name TEXT NOT NULL,
    host_avatar TEXT DEFAULT '🦊',
    guest_id TEXT DEFAULT '',
    guest_name TEXT DEFAULT '',
    guest_avatar TEXT DEFAULT '🐙',
    board TEXT NOT NULL,
    size INTEGER DEFAULT 3,
    win_length INTEGER DEFAULT 3,
    current_turn TEXT DEFAULT 'X',
    status TEXT DEFAULT 'waiting',
    winner TEXT DEFAULT '',
    winning_line TEXT DEFAULT '[]',
    move_count INTEGER DEFAULT 0,
    rematch_host INTEGER DEFAULT 0,
    rematch_guest INTEGER DEFAULT 0,
    host_seen TEXT NOT NULL,
    guest_seen TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_code TEXT NOT NULL,
    move_number INTEGER NOT NULL,
    cell_index INTEGER NOT NULL,
    mark TEXT NOT NULL,
    player_id TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_moves_room ON moves(room_code, move_number);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_code TEXT NOT NULL,
    player_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'chat',
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_code, id);
CREATE TABLE IF NOT EXISTS achievements (
    player_id TEXT NOT NULL,
    code TEXT NOT NULL,
    unlocked_at TEXT NOT NULL,
    PRIMARY KEY (player_id, code)
);
CREATE TABLE IF NOT EXISTS challenges (
    player_id TEXT NOT NULL,
    day TEXT NOT NULL,
    code TEXT NOT NULL,
    completed INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (player_id, day)
);
"""


class LocalDatabase(Database):
    """SQLite backend — works offline, no configuration required."""

    name = "sqlite"
    supports_realtime_rooms = True  # same-host tabs only

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()
        logger.info("Local SQLite backend ready at %s", self.path)

    # -- helpers ---------------------------------------------------------- #
    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[Row]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur.rowcount

    # -- players ---------------------------------------------------------- #
    def ensure_player(self, player_id: str, name: str, avatar: str, is_guest: bool = True) -> Row:
        now = utcnow()
        self._execute(
            """INSERT INTO players (id, name, avatar, is_guest, created_at, last_seen)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                                             avatar=excluded.avatar,
                                             last_seen=excluded.last_seen""",
            (player_id, name, avatar, int(is_guest), now, now),
        )
        return self.get_player(player_id) or {}

    def get_player(self, player_id: str) -> Row | None:
        rows = self._query("SELECT * FROM players WHERE id = ?", (player_id,))
        return rows[0] if rows else None

    def update_player(self, player_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"name", "avatar", "email", "is_guest", "last_seen"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self._execute(
            f"UPDATE players SET {assignments} WHERE id = ?", (*fields.values(), player_id)
        )

    # -- games ------------------------------------------------------------ #
    def record_game(self, record: GameRecord) -> None:
        row = record.to_row()
        self._execute(
            """INSERT INTO games (player_id, mode, result, player_mark, opponent, difficulty,
                                  size, moves, duration, replay, room_code, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["player_id"], row["mode"], row["result"], row["player_mark"], row["opponent"],
                row["difficulty"], row["size"], row["moves"], row["duration"], row["replay"],
                row["room_code"], row["created_at"],
            ),
        )
        self._refresh_leaderboard(record.player_id)

    def _refresh_leaderboard(self, player_id: str) -> None:
        stats = compute_stats(self.list_games(player_id, limit=1000))
        player = self.get_player(player_id) or {"name": "Player", "avatar": "🦊"}
        self._execute(
            """INSERT INTO leaderboard (player_id, name, avatar, games, wins, losses, draws,
                                        win_rate, best_streak, current_streak, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(player_id) DO UPDATE SET name=excluded.name, avatar=excluded.avatar,
                    games=excluded.games, wins=excluded.wins, losses=excluded.losses,
                    draws=excluded.draws, win_rate=excluded.win_rate,
                    best_streak=excluded.best_streak, current_streak=excluded.current_streak,
                    updated_at=excluded.updated_at""",
            (
                player_id, player.get("name", "Player"), player.get("avatar", "🦊"),
                stats.games_played, stats.wins, stats.losses, stats.draws, stats.win_rate,
                stats.longest_streak, stats.current_streak, utcnow(),
            ),
        )

    def list_games(self, player_id: str, limit: int = 200) -> list[Row]:
        return self._query(
            "SELECT * FROM games WHERE player_id = ? ORDER BY id DESC LIMIT ?",
            (player_id, limit),
        )

    def recent_matches(self, limit: int = 15) -> list[Row]:
        return self._query(
            """SELECT g.*, COALESCE(p.name, 'Player') AS name, COALESCE(p.avatar, '🦊') AS avatar
               FROM games g LEFT JOIN players p ON p.id = g.player_id
               ORDER BY g.id DESC LIMIT ?""",
            (limit,),
        )

    # -- leaderboard ------------------------------------------------------ #
    def get_leaderboard(self, order_by: str = "wins", limit: int = 20) -> list[Row]:
        column = "win_rate" if order_by == "win_rate" else "wins"
        having = "AND games >= 3" if order_by == "win_rate" else ""
        return self._query(
            f"""SELECT * FROM leaderboard WHERE games > 0 {having}
                ORDER BY {column} DESC, wins DESC LIMIT ?""",
            (limit,),
        )

    # -- achievements & challenges ---------------------------------------- #
    def unlock_achievement(self, player_id: str, code: str) -> bool:
        changed = self._execute(
            "INSERT OR IGNORE INTO achievements (player_id, code, unlocked_at) VALUES (?, ?, ?)",
            (player_id, code, utcnow()),
        )
        return bool(changed)

    def list_achievements(self, player_id: str) -> list[Row]:
        return self._query(
            "SELECT * FROM achievements WHERE player_id = ? ORDER BY unlocked_at DESC",
            (player_id,),
        )

    def get_challenge(self, player_id: str, day: str) -> Row | None:
        rows = self._query(
            "SELECT * FROM challenges WHERE player_id = ? AND day = ?", (player_id, day)
        )
        return rows[0] if rows else None

    def save_challenge(self, player_id: str, day: str, code: str, completed: bool) -> None:
        self._execute(
            """INSERT INTO challenges (player_id, day, code, completed, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(player_id, day) DO UPDATE SET code=excluded.code,
                    completed=excluded.completed, updated_at=excluded.updated_at""",
            (player_id, day, code, int(completed), utcnow()),
        )

    # -- rooms ------------------------------------------------------------ #
    def create_room(self, room: Row) -> Row:
        columns = ", ".join(room)
        placeholders = ", ".join("?" for _ in room)
        self._execute(f"INSERT INTO rooms ({columns}) VALUES ({placeholders})", tuple(room.values()))
        return self.get_room(room["code"]) or room

    def get_room(self, code: str) -> Row | None:
        rows = self._query("SELECT * FROM rooms WHERE code = ?", (code.upper(),))
        return rows[0] if rows else None

    def update_room(self, code: str, **fields: Any) -> Row | None:
        if not fields:
            return self.get_room(code)
        fields.setdefault("updated_at", utcnow())
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self._execute(
            f"UPDATE rooms SET {assignments} WHERE code = ?", (*fields.values(), code.upper())
        )
        return self.get_room(code)

    def update_room_if(self, code: str, expected_move_count: int, **fields: Any) -> Row | None:
        fields.setdefault("updated_at", utcnow())
        assignments = ", ".join(f"{key} = ?" for key in fields)
        changed = self._execute(
            f"UPDATE rooms SET {assignments} WHERE code = ? AND move_count = ?",
            (*fields.values(), code.upper(), expected_move_count),
        )
        return self.get_room(code) if changed else None

    def delete_room(self, code: str) -> None:
        code = code.upper()
        self._execute("DELETE FROM moves WHERE room_code = ?", (code,))
        self._execute("DELETE FROM messages WHERE room_code = ?", (code,))
        self._execute("DELETE FROM rooms WHERE code = ?", (code,))

    def list_open_rooms(self, limit: int = 20) -> list[Row]:
        return self._query(
            "SELECT * FROM rooms WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (GameStatus.WAITING.value, limit),
        )

    def cleanup_expired_rooms(self, minutes: int = ROOM_TIMEOUT_MINUTES) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        stale = self._query("SELECT code FROM rooms WHERE updated_at < ?", (cutoff,))
        for row in stale:
            self.delete_room(row["code"])
        if stale:
            logger.info("Cleaned up %d stale room(s)", len(stale))
        return len(stale)

    # -- moves & messages -------------------------------------------------- #
    def add_move(
        self, room_code: str, move_number: int, cell_index: int, mark: str, player_id: str
    ) -> None:
        self._execute(
            """INSERT INTO moves (room_code, move_number, cell_index, mark, player_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (room_code.upper(), move_number, cell_index, mark, player_id, utcnow()),
        )

    def list_moves(self, room_code: str) -> list[Row]:
        return self._query(
            "SELECT * FROM moves WHERE room_code = ? ORDER BY move_number", (room_code.upper(),)
        )

    def add_message(self, room_code: str, player_id: str, name: str, kind: str, body: str) -> None:
        self._execute(
            """INSERT INTO messages (room_code, player_id, name, kind, body, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (room_code.upper(), player_id, name, kind, body[:280], utcnow()),
        )

    def list_messages(self, room_code: str, limit: int = MAX_CHAT_MESSAGES) -> list[Row]:
        rows = self._query(
            "SELECT * FROM messages WHERE room_code = ? ORDER BY id DESC LIMIT ?",
            (room_code.upper(), limit),
        )
        return list(reversed(rows))


# --------------------------------------------------------------------------- #
# Supabase implementation
# --------------------------------------------------------------------------- #
class SupabaseDatabase(Database):
    """PostgREST-backed production storage."""

    name = "supabase"
    supports_realtime_rooms = True

    def __init__(self, url: str, key: str) -> None:
        try:
            from supabase import create_client
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise DatabaseError(
                "The 'supabase' package is required for the Supabase backend"
            ) from exc
        self.client = create_client(url, key)
        logger.info("Supabase backend connected (%s)", url)

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _clean(payload: Row) -> Row:
        """Replace blank timestamps with NULL.

        Postgres raises `invalid input syntax for type timestamp with time zone`
        when handed an empty string, whereas SQLite stores it happily. Callers
        may legitimately have no value yet — a room with no guest, a game that
        has not finished — so blanks become None for any timestamp column.
        """
        return {
            key: (None if value == "" and (key.endswith("_at") or key.endswith("_seen")) else value)
            for key, value in payload.items()
        }

    def _table(self, name: str):
        return self.client.table(name)

    @staticmethod
    def _rows(response: Any) -> list[Row]:
        data = getattr(response, "data", None) or []
        return [dict(row) for row in data]

    # -- players ---------------------------------------------------------- #
    @resilient

    def ensure_player(self, player_id: str, name: str, avatar: str, is_guest: bool = True) -> Row:
        now = utcnow()
        payload = {
            "id": player_id,
            "name": name,
            "avatar": avatar,
            "is_guest": is_guest,
            "created_at": now,
            "last_seen": now,
        }
        try:
            rows = self._rows(self._table("players").upsert(payload, on_conflict="id").execute())
            return rows[0] if rows else payload
        except Exception as exc:
            logger.warning("ensure_player failed: %s", exc)
            return payload

    @resilient

    def get_player(self, player_id: str) -> Row | None:
        rows = self._rows(self._table("players").select("*").eq("id", player_id).limit(1).execute())
        return rows[0] if rows else None

    @resilient

    def update_player(self, player_id: str, **fields: Any) -> None:
        if not fields:
            return
        self._table("players").update(self._clean(fields)).eq("id", player_id).execute()

    # -- games ------------------------------------------------------------ #
    @resilient

    def record_game(self, record: GameRecord) -> None:
        payload = record.to_row()
        payload["replay"] = record.replay  # jsonb column
        self._table("games").insert(self._clean(payload)).execute()
        self._refresh_leaderboard(record.player_id)

    def _refresh_leaderboard(self, player_id: str) -> None:
        stats = compute_stats(self.list_games(player_id, limit=1000))
        player = self.get_player(player_id) or {}
        self._table("leaderboard").upsert(
            {
                "player_id": player_id,
                "name": player.get("name", "Player"),
                "avatar": player.get("avatar", "🦊"),
                "games": stats.games_played,
                "wins": stats.wins,
                "losses": stats.losses,
                "draws": stats.draws,
                "win_rate": stats.win_rate,
                "best_streak": stats.longest_streak,
                "current_streak": stats.current_streak,
                "updated_at": utcnow(),
            },
            on_conflict="player_id",
        ).execute()

    @resilient

    def list_games(self, player_id: str, limit: int = 200) -> list[Row]:
        return self._rows(
            self._table("games")
            .select("*")
            .eq("player_id", player_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

    @resilient

    def recent_matches(self, limit: int = 15) -> list[Row]:
        rows = self._rows(
            self._table("games")
            .select("*, players(name, avatar)")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        for row in rows:
            joined = row.pop("players", None) or {}
            row["name"] = joined.get("name", "Player")
            row["avatar"] = joined.get("avatar", "🦊")
        return rows

    # -- leaderboard ------------------------------------------------------ #
    @resilient

    def get_leaderboard(self, order_by: str = "wins", limit: int = 20) -> list[Row]:
        query = self._table("leaderboard").select("*").gt("games", 0)
        if order_by == "win_rate":
            query = query.gte("games", 3).order("win_rate", desc=True)
        else:
            query = query.order("wins", desc=True)
        return self._rows(query.limit(limit).execute())

    # -- achievements & challenges ---------------------------------------- #
    @resilient

    def unlock_achievement(self, player_id: str, code: str) -> bool:
        existing = self._rows(
            self._table("achievements")
            .select("code")
            .eq("player_id", player_id)
            .eq("code", code)
            .limit(1)
            .execute()
        )
        if existing:
            return False
        self._table("achievements").insert(
            {"player_id": player_id, "code": code, "unlocked_at": utcnow()}
        ).execute()
        return True

    @resilient

    def list_achievements(self, player_id: str) -> list[Row]:
        return self._rows(
            self._table("achievements")
            .select("*")
            .eq("player_id", player_id)
            .order("unlocked_at", desc=True)
            .execute()
        )

    @resilient

    def get_challenge(self, player_id: str, day: str) -> Row | None:
        rows = self._rows(
            self._table("challenges")
            .select("*")
            .eq("player_id", player_id)
            .eq("day", day)
            .limit(1)
            .execute()
        )
        return rows[0] if rows else None

    @resilient

    def save_challenge(self, player_id: str, day: str, code: str, completed: bool) -> None:
        self._table("challenges").upsert(
            {
                "player_id": player_id,
                "day": day,
                "code": code,
                "completed": completed,
                "updated_at": utcnow(),
            },
            on_conflict="player_id,day",
        ).execute()

    # -- rooms ------------------------------------------------------------ #
    @resilient

    def create_room(self, room: Row) -> Row:
        rows = self._rows(self._table("rooms").insert(self._clean(room)).execute())
        return rows[0] if rows else room

    @resilient

    def get_room(self, code: str) -> Row | None:
        rows = self._rows(
            self._table("rooms").select("*").eq("code", code.upper()).limit(1).execute()
        )
        return rows[0] if rows else None

    @resilient

    def update_room(self, code: str, **fields: Any) -> Row | None:
        if not fields:
            return self.get_room(code)
        fields.setdefault("updated_at", utcnow())
        rows = self._rows(
            self._table("rooms").update(self._clean(fields)).eq("code", code.upper()).execute()
        )
        return rows[0] if rows else self.get_room(code)

    @resilient

    def update_room_if(self, code: str, expected_move_count: int, **fields: Any) -> Row | None:
        fields.setdefault("updated_at", utcnow())
        rows = self._rows(
            self._table("rooms")
            .update(self._clean(fields))
            .eq("code", code.upper())
            .eq("move_count", expected_move_count)
            .execute()
        )
        return rows[0] if rows else None

    @resilient

    def delete_room(self, code: str) -> None:
        code = code.upper()
        self._table("moves").delete().eq("room_code", code).execute()
        self._table("messages").delete().eq("room_code", code).execute()
        self._table("rooms").delete().eq("code", code).execute()

    @resilient

    def list_open_rooms(self, limit: int = 20) -> list[Row]:
        return self._rows(
            self._table("rooms")
            .select("*")
            .eq("status", GameStatus.WAITING.value)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

    @resilient

    def cleanup_expired_rooms(self, minutes: int = ROOM_TIMEOUT_MINUTES) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        stale = self._rows(self._table("rooms").select("code").lt("updated_at", cutoff).execute())
        for row in stale:
            self.delete_room(row["code"])
        return len(stale)

    # -- moves & messages -------------------------------------------------- #
    @resilient

    def add_move(
        self, room_code: str, move_number: int, cell_index: int, mark: str, player_id: str
    ) -> None:
        self._table("moves").insert(
            {
                "room_code": room_code.upper(),
                "move_number": move_number,
                "cell_index": cell_index,
                "mark": mark,
                "player_id": player_id,
                "created_at": utcnow(),
            }
        ).execute()

    @resilient

    def list_moves(self, room_code: str) -> list[Row]:
        return self._rows(
            self._table("moves")
            .select("*")
            .eq("room_code", room_code.upper())
            .order("move_number")
            .execute()
        )

    @resilient

    def add_message(self, room_code: str, player_id: str, name: str, kind: str, body: str) -> None:
        self._table("messages").insert(
            {
                "room_code": room_code.upper(),
                "player_id": player_id,
                "name": name,
                "kind": kind,
                "body": body[:280],
                "created_at": utcnow(),
            }
        ).execute()

    @resilient

    def list_messages(self, room_code: str, limit: int = MAX_CHAT_MESSAGES) -> list[Row]:
        rows = self._rows(
            self._table("messages")
            .select("*")
            .eq("room_code", room_code.upper())
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(reversed(rows))


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
_database: Database | None = None


def get_database(settings: Settings | None = None, refresh: bool = False) -> Database:
    """Return the active backend, preferring Supabase when configured.

    Falls back to SQLite (with a warning) if Supabase cannot be reached, so the
    application never hard-fails because of a misconfigured environment.
    """
    global _database
    if _database is not None and not refresh:
        return _database

    settings = settings or get_settings()
    if settings.supabase_configured and not settings.force_local_backend:
        try:
            _database = SupabaseDatabase(settings.supabase_url, settings.supabase_key)
            return _database
        except Exception as exc:
            logger.warning("Supabase unavailable (%s); falling back to SQLite", exc)

    _database = LocalDatabase(settings.data_dir / "gridlock.db")
    return _database