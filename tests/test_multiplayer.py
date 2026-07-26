"""Online room rules, backed by the local SQLite implementation."""

from __future__ import annotations

from pathlib import Path

import pytest

from board import InvalidMoveError
from config import GameStatus
from database import GameRecord, LocalDatabase, compute_stats
from multiplayer import (
    InvalidRoomCodeError,
    NotInRoomError,
    NotYourTurnError,
    RoomFullError,
    RoomManager,
    RoomNotFoundError,
    generate_room_code,
    normalise_code,
)
from player import Mark


@pytest.fixture()
def db(tmp_path: Path) -> LocalDatabase:
    return LocalDatabase(tmp_path / "test.db")


@pytest.fixture()
def rooms(db: LocalDatabase) -> RoomManager:
    db.ensure_player("host-1", "Ada", "🦊")
    db.ensure_player("guest-1", "Linus", "🐙")
    db.ensure_player("guest-2", "Grace", "🦉")
    return RoomManager(db)


def test_room_codes_are_unambiguous() -> None:
    code = generate_room_code()
    assert len(code) == 5
    assert not set(code) & set("O0I1")
    assert normalise_code(code.lower()) == code
    with pytest.raises(InvalidRoomCodeError):
        normalise_code("abc")


def test_create_and_join(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    assert state.status is GameStatus.WAITING
    assert not state.both_seated

    joined = rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    assert joined.both_seated
    assert joined.status is GameStatus.IN_PROGRESS
    assert joined.mark_of("host-1") is Mark.X
    assert joined.mark_of("guest-1") is Mark.O
    assert joined.mark_of("nobody") is None


def test_unknown_room_raises(rooms: RoomManager) -> None:
    with pytest.raises(RoomNotFoundError):
        rooms.fetch("ZZZZZ")


def test_third_player_cannot_take_a_seat(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    with pytest.raises(RoomFullError):
        rooms.join_room(state.code, "guest-2", "Grace", "🦉")
    # ...but they can still watch.
    assert rooms.spectate(state.code).both_seated


def test_turn_order_is_enforced(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")

    with pytest.raises(NotYourTurnError):
        rooms.make_move(state.code, "guest-1", 0)

    rooms.make_move(state.code, "host-1", 0)
    with pytest.raises(NotYourTurnError):
        rooms.make_move(state.code, "host-1", 1)

    rooms.make_move(state.code, "guest-1", 4)
    assert rooms.fetch(state.code).move_count == 2


def test_spectators_cannot_move(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    with pytest.raises(NotInRoomError):
        rooms.make_move(state.code, "guest-2", 0)


def test_occupied_cells_are_rejected(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    rooms.make_move(state.code, "host-1", 0)
    with pytest.raises(InvalidMoveError):
        rooms.make_move(state.code, "guest-1", 0)


def test_winning_finishes_the_room(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    for index, player in ((0, "host-1"), (3, "guest-1"), (1, "host-1"), (4, "guest-1")):
        rooms.make_move(state.code, player, index)
    final = rooms.make_move(state.code, "host-1", 2)

    assert final.is_finished
    assert final.winner is Mark.X
    assert final.winning_line == (0, 1, 2)
    assert len(rooms.move_history(state.code)) == 5


def test_rematch_requires_both_players(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    rooms.make_move(state.code, "host-1", 0)
    rooms.resign(state.code, "guest-1")

    after_one = rooms.request_rematch(state.code, "host-1")
    assert after_one.rematch_host and not after_one.rematch_guest
    assert after_one.is_finished

    after_both = rooms.request_rematch(state.code, "guest-1")
    assert after_both.status is GameStatus.IN_PROGRESS
    assert after_both.move_count == 0
    assert after_both.board.to_string() == "." * 9


def test_guest_leaving_frees_the_seat(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    rooms.leave_room(state.code, "guest-1")

    reopened = rooms.fetch(state.code)
    assert not reopened.both_seated
    assert reopened.status is GameStatus.WAITING
    rooms.join_room(state.code, "guest-2", "Grace", "🦉")
    assert rooms.fetch(state.code).guest_id == "guest-2"


def test_host_leaving_empty_room_deletes_it(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.leave_room(state.code, "host-1")
    with pytest.raises(RoomNotFoundError):
        rooms.fetch(state.code)


def test_reconnect_keeps_the_seat(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    again = rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    assert again.guest_id == "guest-1"
    assert again.is_online(Mark.O)


def test_stale_rooms_are_cleaned_up(db: LocalDatabase, rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    db.update_room(state.code, updated_at="2000-01-01T00:00:00+00:00")
    assert db.cleanup_expired_rooms(30) == 1
    with pytest.raises(RoomNotFoundError):
        rooms.fetch(state.code)


def test_chat_and_reactions(rooms: RoomManager) -> None:
    state = rooms.create_room("host-1", "Ada", "🦊")
    rooms.join_room(state.code, "guest-1", "Linus", "🐙")
    rooms.send_message(state.code, "host-1", "Ada", "  good luck  ")
    rooms.send_message(state.code, "guest-1", "Linus", "   ")  # ignored
    rooms.send_reaction(state.code, "guest-1", "Linus", "🔥")

    messages = rooms.messages(state.code)
    assert [m["body"] for m in messages] == ["good luck", "🔥"]
    assert messages[-1]["kind"] == "reaction"


def test_stats_and_leaderboard(db: LocalDatabase) -> None:
    db.ensure_player("p1", "Ada", "🦊")
    for result in ("win", "win", "loss", "draw", "win"):
        db.record_game(
            GameRecord(
                player_id="p1", mode="single", result=result, player_mark="X",
                opponent="Bot", difficulty="hard", size=3, moves=7, duration=30.0,
            )
        )
    stats = db.get_stats("p1")
    assert stats.games_played == 5
    assert stats.wins == 3 and stats.losses == 1 and stats.draws == 1
    assert stats.win_rate == 60.0
    assert stats.longest_streak == 2
    assert stats.current_streak == 1

    board = db.get_leaderboard("wins")
    assert board[0]["player_id"] == "p1"
    assert board[0]["wins"] == 3
    assert db.unlock_achievement("p1", "first_blood") is True
    assert db.unlock_achievement("p1", "first_blood") is False


def test_compute_stats_handles_empty() -> None:
    assert compute_stats([]).games_played == 0
