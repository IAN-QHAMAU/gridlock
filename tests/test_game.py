"""Match orchestration: turns, undo, results, replays."""

from __future__ import annotations

import pytest

from board import InvalidMoveError
from config import Difficulty, GameMode, GameStatus
from game import Game, GameResult, Scoreboard
from player import Mark, Player, PlayerType


def make_game(mode: GameMode = GameMode.SINGLE, size: int = 3) -> Game:
    human = Player(name="Ada", mark=Mark.X)
    opponent = (
        Player.ai(Mark.O, Difficulty.EASY)
        if mode is GameMode.SINGLE
        else Player(name="Linus", mark=Mark.O)
    )
    return Game.new(mode=mode, x_player=human, o_player=opponent, size=size)


def test_turns_alternate() -> None:
    game = make_game(GameMode.LOCAL)
    assert game.current is Mark.X
    game.play(0)
    assert game.current is Mark.O
    game.play(1)
    assert game.current is Mark.X
    assert [move.cell for move in game.moves] == ["A1", "B1"]


def test_win_finishes_the_game() -> None:
    game = make_game(GameMode.LOCAL)
    for index in (0, 3, 1, 4, 2):
        game.play(index)
    assert game.status is GameStatus.FINISHED
    assert game.winner is Mark.X
    assert game.winning_line == (0, 1, 2)
    assert game.result is GameResult.X_WINS
    assert game.result_for(Mark.X) == "win"
    assert game.result_for(Mark.O) == "loss"
    with pytest.raises(InvalidMoveError):
        game.play(5)


def test_draw_is_detected() -> None:
    game = make_game(GameMode.LOCAL)
    for index in (4, 0, 1, 7, 6, 2, 5, 3, 8):
        game.play(index)
    assert game.result is GameResult.DRAW
    assert game.winner is None


def test_undo_only_in_single_player() -> None:
    local = make_game(GameMode.LOCAL)
    local.play(0)
    assert not local.can_undo()
    assert local.undo() == 0

    single = make_game(GameMode.SINGLE)
    single.play(0)
    single.play_ai()
    removed = single.undo()
    assert removed == 2
    assert single.board.move_count == 0
    assert single.current is Mark.X


def test_undo_reopens_a_finished_game() -> None:
    game = make_game(GameMode.SINGLE)
    game.board.cells = list("XX.OO....")
    game.moves.clear()
    game.play(2)
    assert game.is_over
    game.undo()
    assert not game.is_over
    assert game.winner is None


def test_restart_swaps_the_starter() -> None:
    game = make_game(GameMode.LOCAL)
    game.play(0)
    game.restart(swap_starter=True)
    assert game.board.move_count == 0
    assert game.current is Mark.O
    assert game.moves == []


def test_resign_awards_the_opponent() -> None:
    game = make_game(GameMode.LOCAL)
    game.resign(Mark.X)
    assert game.winner is Mark.O
    assert game.is_over


def test_ai_only_moves_on_its_turn() -> None:
    game = make_game(GameMode.SINGLE)
    with pytest.raises(InvalidMoveError):
        game.play_ai()
    game.play(0)
    move = game.play_ai()
    assert move.mark is Mark.O


def test_replay_round_trip() -> None:
    game = make_game(GameMode.LOCAL, size=4)
    for index in (0, 1, 4, 2, 8, 3):
        game.play(index)
    replay = game.to_replay()
    states = Game.board_states(replay)
    assert len(states) == len(game.moves) + 1
    assert states[-1].cells == game.board.cells
    assert replay["size"] == 4


def test_scoreboard_tracks_streaks() -> None:
    board = Scoreboard()
    board.record(GameResult.X_WINS)
    board.record(GameResult.X_WINS)
    assert board.streak == 2 and board.best_streak == 2
    board.record(GameResult.DRAW)
    assert board.streak == 0 and board.best_streak == 2
    board.record(GameResult.O_WINS)
    assert board.o_wins == 1 and board.total == 4
    board.reset()
    assert board.total == 0


def test_player_type_and_display() -> None:
    bot = Player.ai(Mark.O, Difficulty.IMPOSSIBLE)
    assert bot.kind is PlayerType.AI and bot.is_ai
    assert "Impossible" in bot.display
