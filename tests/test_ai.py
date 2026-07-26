"""Engine behaviour across the four difficulties."""

from __future__ import annotations

import random

import pytest

from ai import AIEngine
from board import Board
from config import Difficulty
from player import Mark


@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_engine_returns_legal_moves(difficulty: Difficulty) -> None:
    board = Board.from_string("XO.......")
    move = AIEngine(difficulty=difficulty, seed=7).choose_move(board, Mark.X)
    assert move in board.available_moves()


def test_medium_takes_the_win() -> None:
    board = Board.from_string("XX.OO....")
    assert AIEngine(Difficulty.MEDIUM, seed=1).choose_move(board, Mark.X) == 2


def test_medium_blocks_the_loss() -> None:
    board = Board.from_string("OO..X....")
    assert AIEngine(Difficulty.MEDIUM, seed=1).choose_move(board, Mark.X) == 2


def test_impossible_takes_immediate_win() -> None:
    board = Board.from_string("XX.OO....")
    assert AIEngine(Difficulty.IMPOSSIBLE).choose_move(board, Mark.X) == 2


def test_impossible_blocks_fork_opening() -> None:
    # X in two opposite corners: O must take an edge or centre, never a corner.
    board = Board.from_string("X...O...X")
    move = AIEngine(Difficulty.IMPOSSIBLE).choose_move(board, Mark.O)
    assert move in {1, 3, 5, 7}


def _play_out(engine: AIEngine, engine_mark: Mark, rng: random.Random) -> Mark | None:
    """Play a full 3x3 game: engine vs a random mover. Returns the winner."""
    board = Board()
    turn = Mark.X
    while not board.is_terminal():
        if turn is engine_mark:
            index = engine.choose_move(board, engine_mark)
        else:
            index = rng.choice(board.available_moves())
        board.place(index, turn)
        turn = turn.opponent
    return board.winner()


@pytest.mark.parametrize("engine_mark", [Mark.X, Mark.O])
def test_impossible_never_loses(engine_mark: Mark) -> None:
    engine = AIEngine(Difficulty.IMPOSSIBLE, seed=11)
    rng = random.Random(2024)
    for _ in range(20):
        winner = _play_out(engine, engine_mark, rng)
        assert winner is not engine_mark.opponent


@pytest.mark.parametrize("engine_mark", [Mark.X, Mark.O])
def test_impossible_never_loses_exhaustively(engine_mark: Mark) -> None:
    """Explore every opponent reply on 3x3; the engine must never be beaten."""
    engine = AIEngine(Difficulty.IMPOSSIBLE, seed=3)
    losses = 0

    def walk(board: Board, turn: Mark) -> None:
        nonlocal losses
        if board.is_terminal():
            if board.winner() is engine_mark.opponent:
                losses += 1
            return
        if turn is engine_mark:
            index = engine.choose_move(board, engine_mark)
            board.cells[index] = engine_mark.value
            walk(board, turn.opponent)
            board.cells[index] = "."
        else:
            for index in board.available_moves():
                board.cells[index] = turn.value
                walk(board, turn.opponent)
                board.cells[index] = "."

    walk(Board(), Mark.X)
    assert losses == 0


def test_impossible_draws_against_itself() -> None:
    board = Board()
    engine = AIEngine(Difficulty.IMPOSSIBLE, seed=3)
    turn = Mark.X
    while not board.is_terminal():
        board.place(engine.choose_move(board, turn), turn)
        turn = turn.opponent
    assert board.winner() is None


def test_engine_handles_large_board_within_budget() -> None:
    board = Board(size=5)
    board.place(12, Mark.X)
    engine = AIEngine(Difficulty.IMPOSSIBLE, seed=5)
    move = engine.choose_move(board, Mark.O)
    assert move in board.available_moves()
    assert engine.stats.elapsed < 5.0


def test_engine_raises_on_full_board() -> None:
    board = Board.from_string("XXOOOXXOX")
    with pytest.raises(ValueError):
        AIEngine(Difficulty.EASY).choose_move(board, Mark.X)
