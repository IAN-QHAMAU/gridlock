"""Online multiplayer: rooms, turn enforcement, presence, rematches and chat.

All mutating operations go through :class:`RoomManager`, which is the single
place where the rules are enforced.  Moves use a conditional (compare-and-set)
update on ``move_count`` so two clients can never both write the same ply — the
loser of the race simply re-reads the room.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from board import Board, InvalidMoveError
from config import (
    BOARD_SIZES,
    DEFAULT_BOARD_SIZE,
    PRESENCE_TIMEOUT_SECONDS,
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    ROOM_TIMEOUT_MINUTES,
    GameStatus,
    RoomRole,
    configure_logging,
)
from database import Database, Row, parse_ts, utcnow
from player import Mark, sanitize_name

logger = configure_logging()


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class RoomError(RuntimeError):
    """Base class for multiplayer failures (all are user-presentable)."""


class InvalidRoomCodeError(RoomError):
    """The code is malformed."""


class RoomNotFoundError(RoomError):
    """No room exists (or it expired)."""


class RoomFullError(RoomError):
    """Both seats are taken."""


class NotYourTurnError(RoomError):
    """A player tried to move out of turn."""


class NotInRoomError(RoomError):
    """A spectator tried to perform a player-only action."""


# --------------------------------------------------------------------------- #
# View model
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class RoomState:
    """A room row, parsed into something the UI can render directly."""

    code: str
    board: Board
    status: GameStatus
    current_turn: Mark
    winner: Mark | None
    winning_line: tuple[int, ...]
    host_id: str
    host_name: str
    host_avatar: str
    guest_id: str
    guest_name: str
    guest_avatar: str
    move_count: int
    rematch_host: bool
    rematch_guest: bool
    host_seen: datetime
    guest_seen: datetime
    created_at: datetime
    updated_at: datetime
    raw: Row = field(default_factory=dict)

    # -- membership ------------------------------------------------------- #
    def role_of(self, player_id: str) -> RoomRole:
        """Which seat (if any) a viewer occupies."""
        if player_id and player_id == self.host_id:
            return RoomRole.HOST
        if player_id and player_id == self.guest_id:
            return RoomRole.GUEST
        return RoomRole.SPECTATOR

    def mark_of(self, player_id: str) -> Mark | None:
        """Host plays X, guest plays O; spectators get ``None``."""
        role = self.role_of(player_id)
        if role is RoomRole.HOST:
            return Mark.X
        if role is RoomRole.GUEST:
            return Mark.O
        return None

    def name_of(self, mark: Mark) -> str:
        """Display name for a seat."""
        return self.host_name if mark is Mark.X else (self.guest_name or "Waiting…")

    def avatar_of(self, mark: Mark) -> str:
        """Avatar for a seat."""
        return self.host_avatar if mark is Mark.X else (self.guest_avatar or "❔")

    # -- state ------------------------------------------------------------ #
    @property
    def both_seated(self) -> bool:
        """True once a guest has joined."""
        return bool(self.guest_id)

    @property
    def is_finished(self) -> bool:
        """True when the current game has ended."""
        return self.status in (GameStatus.FINISHED, GameStatus.ABANDONED)

    def is_turn_of(self, player_id: str) -> bool:
        """True when it is this viewer's turn to move."""
        mark = self.mark_of(player_id)
        return (
            mark is not None
            and self.status is GameStatus.IN_PROGRESS
            and mark is self.current_turn
        )

    def is_online(self, mark: Mark) -> bool:
        """Presence heuristic based on the last heartbeat."""
        seen = self.host_seen if mark is Mark.X else self.guest_seen
        if mark is Mark.O and not self.guest_id:
            return False
        delta = (datetime.now(timezone.utc) - seen).total_seconds()
        return delta <= PRESENCE_TIMEOUT_SECONDS

    def rematch_of(self, mark: Mark) -> bool:
        """Whether a seat has asked for a rematch."""
        return self.rematch_host if mark is Mark.X else self.rematch_guest


def parse_room(row: Row) -> RoomState:
    """Convert a raw room row into a :class:`RoomState`."""
    size = int(row.get("size") or DEFAULT_BOARD_SIZE)
    try:
        board = Board.from_string(
            row.get("board") or "." * (size * size),
            size=size,
            win_length=int(row.get("win_length") or 0),
        )
    except ValueError:
        logger.warning("Room %s has a malformed board; resetting it", row.get("code"))
        board = Board(size=size)
    line_raw = row.get("winning_line") or "[]"
    if isinstance(line_raw, str):
        try:
            line = tuple(json.loads(line_raw))
        except json.JSONDecodeError:
            line = ()
    else:
        line = tuple(line_raw or ())

    winner_raw = (row.get("winner") or "").strip()
    return RoomState(
        code=str(row.get("code", "")).upper(),
        board=board,
        status=GameStatus(row.get("status") or GameStatus.WAITING.value),
        current_turn=Mark(row.get("current_turn") or "X"),
        winner=Mark(winner_raw) if winner_raw in ("X", "O") else None,
        winning_line=tuple(int(i) for i in line),
        host_id=row.get("host_id", ""),
        host_name=row.get("host_name", "Host"),
        host_avatar=row.get("host_avatar", "🦊"),
        guest_id=row.get("guest_id", "") or "",
        guest_name=row.get("guest_name", "") or "",
        guest_avatar=row.get("guest_avatar", "") or "",
        move_count=int(row.get("move_count") or 0),
        rematch_host=bool(row.get("rematch_host")),
        rematch_guest=bool(row.get("rematch_guest")),
        host_seen=parse_ts(row.get("host_seen")),
        guest_seen=parse_ts(row.get("guest_seen")),
        created_at=parse_ts(row.get("created_at")),
        updated_at=parse_ts(row.get("updated_at")),
        raw=row,
    )


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
def generate_room_code(length: int = ROOM_CODE_LENGTH) -> str:
    """Random, unambiguous room code (no O/0/I/1)."""
    return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(length))


def normalise_code(code: str) -> str:
    """Validate and canonicalise a user-entered room code.

    Raises:
        InvalidRoomCodeError: If the code is not ``ROOM_CODE_LENGTH`` characters
            drawn from the room alphabet.
    """
    cleaned = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if len(cleaned) != ROOM_CODE_LENGTH or any(ch not in ROOM_CODE_ALPHABET for ch in cleaned):
        raise InvalidRoomCodeError(
            f"Room codes are {ROOM_CODE_LENGTH} characters, letters and digits only."
        )
    return cleaned


class RoomManager:
    """Business rules for online rooms."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ lifecycle
    def create_room(
        self,
        player_id: str,
        name: str,
        avatar: str,
        size: int = DEFAULT_BOARD_SIZE,
    ) -> RoomState:
        """Open a new room with the caller seated as host (X)."""
        if size not in BOARD_SIZES:
            raise RoomError(f"Unsupported board size: {size}")

        self.db.cleanup_expired_rooms(ROOM_TIMEOUT_MINUTES)
        code = self._unique_code()
        now = utcnow()
        row = {
            "code": code,
            "host_id": player_id,
            "host_name": sanitize_name(name, "Host"),
            "host_avatar": avatar,
            "guest_id": "",
            "guest_name": "",
            "guest_avatar": "",
            "board": "." * (size * size),
            "size": size,
            "win_length": BOARD_SIZES[size],
            "current_turn": Mark.X.value,
            "status": GameStatus.WAITING.value,
            "winner": "",
            "winning_line": "[]",
            "move_count": 0,
            "rematch_host": 0,
            "rematch_guest": 0,
            "host_seen": now,
            # No guest yet.  This must be None rather than "": Postgres rejects
            # an empty string for `timestamptz`, while SQLite would accept it.
            "guest_seen": None,
            "created_at": now,
            "updated_at": now,
        }
        created = self.db.create_room(row)
        logger.info("Room %s created by %s", code, player_id)
        return parse_room(created)

    def _unique_code(self, attempts: int = 12) -> str:
        """Generate a code that is not already in use."""
        for _ in range(attempts):
            code = generate_room_code()
            if self.db.get_room(code) is None:
                return code
        raise RoomError("Could not allocate a free room code. Please try again.")

    def fetch(self, code: str) -> RoomState:
        """Load a room.

        Raises:
            InvalidRoomCodeError: Malformed code.
            RoomNotFoundError: No such room (or it timed out).
        """
        code = normalise_code(code)
        row = self.db.get_room(code)
        if row is None:
            raise RoomNotFoundError(f"Room {code} doesn't exist. It may have timed out.")
        return parse_room(row)

    def join_room(self, code: str, player_id: str, name: str, avatar: str) -> RoomState:
        """Take the guest seat, or rejoin a seat you already hold.

        Raises:
            RoomFullError: When both seats belong to other players.
        """
        state = self.fetch(code)
        role = state.role_of(player_id)

        if role is RoomRole.HOST:
            return self.heartbeat(code, player_id)
        if role is RoomRole.GUEST:  # reconnect
            self.db.update_room(code, guest_seen=utcnow(), guest_name=sanitize_name(name, "Guest"))
            logger.info("Player %s reconnected to room %s", player_id, code)
            return self.fetch(code)
        if state.both_seated:
            raise RoomFullError(f"Room {state.code} already has two players. Try spectating.")

        updated = self.db.update_room(
            state.code,
            guest_id=player_id,
            guest_name=sanitize_name(name, "Guest"),
            guest_avatar=avatar,
            guest_seen=utcnow(),
            status=GameStatus.IN_PROGRESS.value,
        )
        logger.info("Player %s joined room %s", player_id, state.code)
        return parse_room(updated or {})

    def spectate(self, code: str) -> RoomState:
        """Read-only view of a room."""
        return self.fetch(code)

    def heartbeat(self, code: str, player_id: str) -> RoomState:
        """Record presence for the caller and return the fresh state."""
        state = self.fetch(code)
        role = state.role_of(player_id)
        if role is RoomRole.HOST:
            self.db.update_room(state.code, host_seen=utcnow())
        elif role is RoomRole.GUEST:
            self.db.update_room(state.code, guest_seen=utcnow())
        else:
            return state
        return self.fetch(code)

    def leave_room(self, code: str, player_id: str) -> None:
        """Leave a room; the host closing an empty room deletes it."""
        try:
            state = self.fetch(code)
        except RoomError:
            return
        role = state.role_of(player_id)

        if role is RoomRole.HOST:
            if not state.both_seated:
                self.db.delete_room(state.code)
                logger.info("Empty room %s closed by host", state.code)
                return
            self.db.update_room(
                state.code,
                status=GameStatus.ABANDONED.value,
                winner=Mark.O.value if not state.is_finished else (state.winner or Mark.O).value,
            )
        elif role is RoomRole.GUEST:
            self.db.update_room(
                state.code,
                guest_id="",
                guest_name="",
                guest_avatar="",
                guest_seen=None,
                status=GameStatus.WAITING.value if not state.is_finished else state.status.value,
                rematch_host=0,
                rematch_guest=0,
            )
        logger.info("Player %s left room %s", player_id, state.code)

    # ----------------------------------------------------------------- play
    def make_move(self, code: str, player_id: str, index: int) -> RoomState:
        """Validate and apply a move.

        The three cheat vectors — moving as someone else, moving out of turn and
        moving into an occupied cell — are all rejected here, and the write is
        conditional on the room still being at the same ply.

        Raises:
            NotInRoomError, NotYourTurnError, InvalidMoveError, RoomError
        """
        state = self.fetch(code)
        mark = state.mark_of(player_id)

        if mark is None:
            raise NotInRoomError("Spectators can't move. Ask for a seat in the next game.")
        if not state.both_seated:
            raise RoomError("Waiting for a second player to join.")
        if state.status is not GameStatus.IN_PROGRESS:
            raise RoomError("This game isn't accepting moves right now.")
        if state.current_turn is not mark:
            raise NotYourTurnError(f"It's {state.name_of(state.current_turn)}'s turn.")

        board = state.board.copy()
        board.place(index, mark)  # raises InvalidMoveError when occupied/out of range

        line = board.winning_line()
        winner = Mark(board.cells[line[0]]) if line else None
        finished = bool(line) or board.is_full()

        updated = self.db.update_room_if(
            state.code,
            state.move_count,
            board=board.to_string(),
            move_count=state.move_count + 1,
            current_turn=(mark.opponent if not finished else mark).value,
            status=(GameStatus.FINISHED if finished else GameStatus.IN_PROGRESS).value,
            winner=winner.value if winner else "",
            winning_line=json.dumps(list(line or ())),
        )
        if updated is None:
            # Someone else wrote first — surface the authoritative state.
            raise RoomError("The board moved on. Showing the latest position.")

        self.db.add_move(state.code, state.move_count + 1, index, mark.value, player_id)
        return parse_room(updated)

    def request_rematch(self, code: str, player_id: str) -> RoomState:
        """Flag the caller as ready to play again; reset once both agree."""
        state = self.fetch(code)
        role = state.role_of(player_id)
        if role is RoomRole.SPECTATOR:
            raise NotInRoomError("Only players can start a rematch.")

        field_name = "rematch_host" if role is RoomRole.HOST else "rematch_guest"
        self.db.update_room(state.code, **{field_name: 1})
        state = self.fetch(code)

        if state.rematch_host and state.rematch_guest:
            return self.reset_room(code)
        return state

    def reset_room(self, code: str) -> RoomState:
        """Clear the board for a new game in the same room."""
        state = self.fetch(code)
        size = state.board.size
        updated = self.db.update_room(
            state.code,
            board="." * (size * size),
            move_count=0,
            current_turn=Mark.X.value,
            status=(
                GameStatus.IN_PROGRESS if state.both_seated else GameStatus.WAITING
            ).value,
            winner="",
            winning_line="[]",
            rematch_host=0,
            rematch_guest=0,
        )
        logger.info("Room %s reset", state.code)
        return parse_room(updated or {})

    def resign(self, code: str, player_id: str) -> RoomState:
        """Concede the current game."""
        state = self.fetch(code)
        mark = state.mark_of(player_id)
        if mark is None:
            raise NotInRoomError("Only players can resign.")
        updated = self.db.update_room(
            state.code,
            status=GameStatus.FINISHED.value,
            winner=mark.opponent.value,
            winning_line="[]",
        )
        return parse_room(updated or {})

    # -------------------------------------------------------------- social
    def send_message(self, code: str, player_id: str, name: str, body: str) -> None:
        """Post a chat line (ignored when empty)."""
        text = " ".join((body or "").split())[:280]
        if not text:
            return
        self.db.add_message(normalise_code(code), player_id, sanitize_name(name), "chat", text)

    def send_reaction(self, code: str, player_id: str, name: str, emoji: str) -> None:
        """Post an emoji reaction."""
        self.db.add_message(
            normalise_code(code), player_id, sanitize_name(name), "reaction", emoji[:8]
        )

    def messages(self, code: str) -> list[Row]:
        """Recent chat + reactions for a room, oldest first."""
        return self.db.list_messages(normalise_code(code))

    def move_history(self, code: str) -> list[Row]:
        """Move list for a room."""
        return self.db.list_moves(normalise_code(code))

    def open_rooms(self, limit: int = 10) -> list[Row]:
        """Rooms currently waiting for a second player."""
        return self.db.list_open_rooms(limit)


__all__ = [
    "InvalidMoveError",
    "InvalidRoomCodeError",
    "NotInRoomError",
    "NotYourTurnError",
    "RoomError",
    "RoomFullError",
    "RoomManager",
    "RoomNotFoundError",
    "RoomState",
    "generate_room_code",
    "normalise_code",
    "parse_room",
]