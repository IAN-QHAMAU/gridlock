"""Board geometry, move legality and outcome detection."""

from __future__ import annotations

import pytest

from board import EMPTY, Board, InvalidMoveError, winning_lines
from player import Mark


def test_new_board_is_empty() -> None:
    board = Board()
    assert board.size == 3
    assert board.win_length == 3
    assert board.cells == [EMPTY] * 9
    assert board.available_moves() == list(range(9))
    assert not board.is_full()
    assert board.winner() is None


@pytest.mark.parametrize("size, win_length, expected", [(3, 3, 8), (4, 4, 10), (5, 4, 28)])
def test_line_counts(size: int, win_length: int, expected: int) -> None:
    assert len(winning_lines(size, win_length)) == expected


def test_place_and_reject_duplicate() -> None:
    board = Board()
    board.place(4, Mark.X)
    assert board.cells[4] == "X"
    with pytest.raises(InvalidMoveError):
        board.place(4, Mark.O)
    with pytest.raises(InvalidMoveError):
        board.place(99, Mark.O)


def test_row_column_and_diagonal_wins() -> None:
    row = Board.from_string("XXXOO....")
    assert row.winner() is Mark.X

    column = Board.from_string("XO.XO.X..")
    assert column.winner() is Mark.X

    diagonal = Board.from_string("X..OX..OX")
    assert diagonal.winner() is Mark.X
    assert diagonal.winning_line() == (0, 4, 8)

    anti = Board.from_string("..X.X.X..")
    assert anti.winner() is Mark.X


def test_draw_detection() -> None:
    board = Board.from_string("XXOOOXXOX")
    assert board.is_full()
    assert board.winner() is None
    assert board.is_terminal()


def test_from_string_rejects_wrong_length() -> None:
    with pytest.raises(ValueError):
        Board.from_string("XO.XO.X...", size=3)


def test_serialisation_round_trip() -> None:
    board = Board(size=4)
    board.place(0, Mark.X)
    board.place(5, Mark.O)
    clone = Board.from_string(board.to_string(), size=4)
    assert clone.cells == board.cells
    assert clone.win_length == 4


def test_large_board_win_length() -> None:
    board = Board(size=5)
    assert board.win_length == 4
    for index in (0, 1, 2):
        board.place(index, Mark.O)
    assert board.winner() is None
    board.place(3, Mark.O)
    assert board.winner() is Mark.O


def test_copy_is_independent() -> None:
    board = Board()
    clone = board.copy()
    clone.place(0, Mark.X)
    assert board.cells[0] == EMPTY
