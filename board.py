"""Board model.

Supports 3x3, 4x4 and 5x5 grids with a configurable "marks in a row" win
length.  The board is a flat list of cells which keeps serialisation (and the
minimax hot loop) fast and simple.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterator

from config import BOARD_SIZES, DEFAULT_BOARD_SIZE
from player import Mark

EMPTY: str = "."


class InvalidMoveError(ValueError):
    """Raised when a move is illegal (out of range or already occupied)."""


@lru_cache(maxsize=32)
def winning_lines(size: int, win_length: int) -> tuple[tuple[int, ...], ...]:
    """Every straight run of ``win_length`` cells on a ``size`` x ``size`` grid.

    Args:
        size: Board edge length.
        win_length: Number of consecutive marks needed to win.

    Returns:
        A tuple of index tuples, cached per (size, win_length) pair.
    """
    lines: list[tuple[int, ...]] = []
    for row in range(size):
        for col in range(size):
            if col + win_length <= size:  # horizontal
                lines.append(tuple(row * size + col + i for i in range(win_length)))
            if row + win_length <= size:  # vertical
                lines.append(tuple((row + i) * size + col for i in range(win_length)))
            if row + win_length <= size and col + win_length <= size:  # main diagonal
                lines.append(tuple((row + i) * size + col + i for i in range(win_length)))
            if row + win_length <= size and col - win_length + 1 >= 0:  # anti diagonal
                lines.append(tuple((row + i) * size + col - i for i in range(win_length)))
    return tuple(lines)


@dataclass(slots=True)
class Board:
    """A tic-tac-toe grid of arbitrary supported size."""

    size: int = DEFAULT_BOARD_SIZE
    win_length: int = 0
    cells: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.size not in BOARD_SIZES:
            raise ValueError(f"Unsupported board size: {self.size}")
        if not self.win_length:
            self.win_length = BOARD_SIZES[self.size]
        if not self.cells:
            self.cells = [EMPTY] * (self.size * self.size)
        if len(self.cells) != self.size * self.size:
            raise ValueError("Cell count does not match board size")

    # ---------------------------------------------------------------- basics
    @property
    def area(self) -> int:
        """Total number of cells."""
        return self.size * self.size

    @property
    def lines(self) -> tuple[tuple[int, ...], ...]:
        """All potential winning lines for this geometry."""
        return winning_lines(self.size, self.win_length)

    def __getitem__(self, index: int) -> str:
        return self.cells[index]

    def __iter__(self) -> Iterator[str]:
        return iter(self.cells)

    def rows(self) -> list[list[str]]:
        """The board as a list of rows (useful for rendering)."""
        return [self.cells[r * self.size : (r + 1) * self.size] for r in range(self.size)]

    def is_empty_cell(self, index: int) -> bool:
        """True when ``index`` is on the board and unoccupied."""
        return 0 <= index < self.area and self.cells[index] == EMPTY

    def available_moves(self) -> list[int]:
        """Indices of all empty cells."""
        return [i for i, cell in enumerate(self.cells) if cell == EMPTY]

    @property
    def move_count(self) -> int:
        """How many marks are on the board."""
        return sum(1 for cell in self.cells if cell != EMPTY)

    def is_full(self) -> bool:
        """True when no empty cell remains."""
        return EMPTY not in self.cells

    # ----------------------------------------------------------------- moves
    def place(self, index: int, mark: Mark) -> None:
        """Place ``mark`` at ``index``.

        Raises:
            InvalidMoveError: If the cell is out of range or already taken.
        """
        if not 0 <= index < self.area:
            raise InvalidMoveError(f"Cell {index} is outside the board")
        if self.cells[index] != EMPTY:
            raise InvalidMoveError(f"Cell {index} is already taken")
        self.cells[index] = mark.value

    def unplace(self, index: int) -> None:
        """Clear a single cell (used by minimax and undo)."""
        if not 0 <= index < self.area:
            raise InvalidMoveError(f"Cell {index} is outside the board")
        self.cells[index] = EMPTY

    def clear(self) -> None:
        """Reset every cell."""
        self.cells = [EMPTY] * self.area

    def copy(self) -> "Board":
        """Deep-enough copy for search routines."""
        return Board(size=self.size, win_length=self.win_length, cells=list(self.cells))

    # --------------------------------------------------------------- outcome
    def winning_line(self) -> tuple[int, ...] | None:
        """The first completed line, or ``None``."""
        cells = self.cells
        for line in self.lines:
            first = cells[line[0]]
            if first == EMPTY:
                continue
            if all(cells[i] == first for i in line[1:]):
                return line
        return None

    def winner(self) -> Mark | None:
        """The winning mark, or ``None`` when nobody has won yet."""
        line = self.winning_line()
        return Mark(self.cells[line[0]]) if line else None

    def is_terminal(self) -> bool:
        """True when the position is won or drawn."""
        return self.winner() is not None or self.is_full()

    # --------------------------------------------------- (de)serialisation
    def to_string(self) -> str:
        """Compact representation, e.g. ``"X.O.X...O"``."""
        return "".join(self.cells)

    @classmethod
    def from_string(cls, data: str, size: int | None = None, win_length: int = 0) -> "Board":
        """Rebuild a board from :meth:`to_string` output.

        Raises:
            ValueError: If the data length does not match a supported board.
        """
        data = (data or "").strip()
        inferred = size or int(round(len(data) ** 0.5)) or DEFAULT_BOARD_SIZE
        if inferred not in BOARD_SIZES:
            raise ValueError(f"Cannot infer a valid board size from {len(data)} cells")
        area = inferred * inferred
        if data and len(data) != area:
            raise ValueError(f"Expected {area} cells for a {inferred}x{inferred} board, got {len(data)}")
        cells = list(data) if data else [EMPTY] * area
        cells = [c if c in (EMPTY, Mark.X.value, Mark.O.value) else EMPTY for c in cells]
        return cls(size=inferred, win_length=win_length or BOARD_SIZES[inferred], cells=cells)

    def render_text(self) -> str:
        """ASCII rendering — the direct descendant of the original CLI game."""
        divider = "\n" + "-" * (self.size * 4 - 1) + "\n"
        return divider.join(
            " " + " | ".join(cell if cell != EMPTY else " " for cell in row) for row in self.rows()
        )

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.render_text()
