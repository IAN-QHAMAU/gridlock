"""Match orchestration: turn order, move history, undo, results and replays."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ai import AIEngine
from board import Board, InvalidMoveError
from config import (
    BOARD_SIZES,
    DEFAULT_BOARD_SIZE,
    TIMED_MODE_SECONDS,
    Difficulty,
    GameMode,
    GameStatus,
    configure_logging,
)
from player import Mark, Player, PlayerType

logger = configure_logging()


class GameResult(str, Enum):
    """Outcome of a finished (or ongoing) match."""

    ONGOING = "ongoing"
    X_WINS = "x_wins"
    O_WINS = "o_wins"
    DRAW = "draw"

    @property
    def label(self) -> str:
        return {
            GameResult.ONGOING: "In progress",
            GameResult.X_WINS: "X wins",
            GameResult.O_WINS: "O wins",
            GameResult.DRAW: "Draw",
        }[self]


@dataclass(slots=True)
class Move:
    """A single placed mark."""

    number: int
    index: int
    mark: Mark
    player_name: str
    seconds: float = 0.0
    cell: str = ""
    played_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, object]:
        """Serialise for storage and replay export."""
        return {
            "number": self.number,
            "index": self.index,
            "mark": self.mark.value,
            "player_name": self.player_name,
            "seconds": round(self.seconds, 3),
            "played_at": self.played_at,
            "cell": self.cell,
        }


@dataclass(slots=True)
class Scoreboard:
    """Session score for a pair of seats."""

    x_wins: int = 0
    o_wins: int = 0
    draws: int = 0
    streak_mark: Mark | None = None
    streak: int = 0
    best_streak: int = 0

    @property
    def total(self) -> int:
        """Games recorded in this session."""
        return self.x_wins + self.o_wins + self.draws

    def record(self, result: GameResult) -> None:
        """Fold a finished game into the session score."""
        if result is GameResult.X_WINS:
            self.x_wins += 1
            self._bump(Mark.X)
        elif result is GameResult.O_WINS:
            self.o_wins += 1
            self._bump(Mark.O)
        elif result is GameResult.DRAW:
            self.draws += 1
            self.streak_mark, self.streak = None, 0

    def _bump(self, mark: Mark) -> None:
        self.streak = self.streak + 1 if self.streak_mark is mark else 1
        self.streak_mark = mark
        self.best_streak = max(self.best_streak, self.streak)

    def reset(self) -> None:
        """Clear the session score."""
        self.x_wins = self.o_wins = self.draws = self.streak = self.best_streak = 0
        self.streak_mark = None


@dataclass(slots=True)
class Game:
    """A single match of tic-tac-toe."""

    mode: GameMode = GameMode.SINGLE
    board: Board = field(default_factory=Board)
    players: dict[Mark, Player] = field(default_factory=dict)
    current: Mark = Mark.X
    status: GameStatus = GameStatus.IN_PROGRESS
    moves: list[Move] = field(default_factory=list)
    winner: Mark | None = None
    winning_line: tuple[int, ...] | None = None
    engine: AIEngine | None = None
    timed: bool = False
    seconds_per_move: int = TIMED_MODE_SECONDS
    game_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    turn_started_at: float = field(default_factory=time.time)
    starting_mark: Mark = Mark.X

    # ------------------------------------------------------------ factories
    @classmethod
    def new(
        cls,
        mode: GameMode,
        x_player: Player,
        o_player: Player,
        size: int = DEFAULT_BOARD_SIZE,
        difficulty: Difficulty = Difficulty.MEDIUM,
        timed: bool = False,
        seconds_per_move: int = TIMED_MODE_SECONDS,
        starting_mark: Mark = Mark.X,
    ) -> "Game":
        """Create a ready-to-play match."""
        if size not in BOARD_SIZES:
            raise ValueError(f"Unsupported board size: {size}")
        engine = AIEngine(difficulty=difficulty) if PlayerType.AI in {
            x_player.kind,
            o_player.kind,
        } else None
        return cls(
            mode=mode,
            board=Board(size=size),
            players={Mark.X: x_player, Mark.O: o_player},
            current=starting_mark,
            starting_mark=starting_mark,
            engine=engine,
            timed=timed,
            seconds_per_move=seconds_per_move,
        )

    # -------------------------------------------------------------- helpers
    @property
    def current_player(self) -> Player:
        """Player whose turn it is."""
        return self.players[self.current]

    @property
    def is_over(self) -> bool:
        """True when the match has finished."""
        return self.status in (GameStatus.FINISHED, GameStatus.ABANDONED)

    @property
    def is_ai_turn(self) -> bool:
        """True when the engine should move next."""
        return not self.is_over and self.current_player.is_ai

    @property
    def result(self) -> GameResult:
        """Current outcome."""
        if not self.is_over:
            return GameResult.ONGOING
        if self.winner is Mark.X:
            return GameResult.X_WINS
        if self.winner is Mark.O:
            return GameResult.O_WINS
        return GameResult.DRAW

    @property
    def duration(self) -> float:
        """Seconds elapsed (frozen once the match ends)."""
        return (self.finished_at or time.time()) - self.started_at

    @property
    def time_left(self) -> float:
        """Seconds remaining on the current turn in timed mode."""
        if not self.timed or self.is_over:
            return float(self.seconds_per_move)
        return max(0.0, self.seconds_per_move - (time.time() - self.turn_started_at))

    def cell_name(self, index: int) -> str:
        """Chess-style reference for a cell index, e.g. ``B3``."""
        row, col = divmod(index, self.board.size)
        return f"{chr(ord('A') + col)}{row + 1}"

    def result_for(self, mark: Mark) -> str:
        """``"win"`` / ``"loss"`` / ``"draw"`` from ``mark``'s perspective."""
        if self.winner is None:
            return "draw"
        return "win" if self.winner is mark else "loss"

    # ---------------------------------------------------------------- moves
    def play(self, index: int) -> Move:
        """Place the current player's mark at ``index`` and advance the turn.

        Args:
            index: Target cell.

        Returns:
            The recorded :class:`Move`.

        Raises:
            InvalidMoveError: If the game is over or the cell is unavailable.
        """
        if self.is_over:
            raise InvalidMoveError("This match has already finished")

        self.board.place(index, self.current)
        move = Move(
            number=len(self.moves) + 1,
            index=index,
            mark=self.current,
            player_name=self.current_player.name,
            seconds=max(0.0, time.time() - self.turn_started_at),
            cell=self.cell_name(index),
        )
        self.moves.append(move)

        self._settle()
        if not self.is_over:
            self.current = self.current.opponent
            self.turn_started_at = time.time()
        return move

    def play_ai(self) -> Move:
        """Let the engine move for the current player.

        Raises:
            InvalidMoveError: If it is not the engine's turn.
        """
        if not self.is_ai_turn:
            raise InvalidMoveError("It is not the engine's turn")
        engine = self.engine or AIEngine(
            difficulty=self.current_player.difficulty or Difficulty.MEDIUM
        )
        engine.difficulty = self.current_player.difficulty or engine.difficulty
        return self.play(engine.choose_move(self.board, self.current))

    def play_timeout(self) -> Move:
        """Auto-play a random legal move when the clock runs out."""
        import random

        return self.play(random.choice(self.board.available_moves()))

    def can_undo(self) -> bool:
        """Undo is offered in single-player only, once a move exists."""
        return self.mode is GameMode.SINGLE and bool(self.moves)

    def undo(self) -> int:
        """Roll back to the human's previous decision point.

        Returns:
            Number of moves removed.
        """
        if not self.can_undo():
            return 0
        removed = 0
        # Remove the trailing AI move (if any) plus the human's own move.
        while self.moves and removed < 2:
            move = self.moves.pop()
            self.board.unplace(move.index)
            removed += 1
            if not self.players[move.mark].is_ai:
                break
        self.status = GameStatus.IN_PROGRESS
        self.winner = None
        self.winning_line = None
        self.finished_at = None
        self.current = self.moves[-1].mark.opponent if self.moves else self.starting_mark
        self.turn_started_at = time.time()
        logger.debug("Undo removed %d move(s) from game %s", removed, self.game_id)
        return removed

    def resign(self, mark: Mark) -> None:
        """``mark`` gives up; the opponent takes the win."""
        self.winner = mark.opponent
        self.winning_line = None
        self.status = GameStatus.FINISHED
        self.finished_at = time.time()

    def restart(self, swap_starter: bool = True) -> None:
        """Reset the position, optionally alternating who starts."""
        self.board.clear()
        self.moves.clear()
        self.winner = None
        self.winning_line = None
        self.status = GameStatus.IN_PROGRESS
        self.starting_mark = self.starting_mark.opponent if swap_starter else self.starting_mark
        self.current = self.starting_mark
        self.started_at = time.time()
        self.finished_at = None
        self.turn_started_at = time.time()
        self.game_id = uuid.uuid4().hex[:12]

    def _settle(self) -> None:
        """Update status/winner after a move."""
        line = self.board.winning_line()
        if line:
            self.winning_line = line
            self.winner = Mark(self.board.cells[line[0]])
            self.status = GameStatus.FINISHED
            self.finished_at = time.time()
        elif self.board.is_full():
            self.winner = None
            self.status = GameStatus.FINISHED
            self.finished_at = time.time()

    # --------------------------------------------------------------- export
    def to_replay(self) -> dict[str, object]:
        """Full match record, used for storage, replay and export."""
        return {
            "game_id": self.game_id,
            "mode": self.mode.value,
            "size": self.board.size,
            "win_length": self.board.win_length,
            "result": self.result.value,
            "winner": self.winner.value if self.winner else "",
            "winning_line": list(self.winning_line or ()),
            "players": {mark.value: player.to_dict() for mark, player in self.players.items()},
            "moves": [move.to_dict() for move in self.moves],
            "duration_seconds": round(self.duration, 2),
            "started_at": datetime.fromtimestamp(self.started_at, timezone.utc).isoformat(),
            "finished_at": (
                datetime.fromtimestamp(self.finished_at, timezone.utc).isoformat()
                if self.finished_at
                else ""
            ),
        }

    @staticmethod
    def board_states(replay: dict[str, object]) -> list[Board]:
        """Reconstruct every position of a replay, first to last."""
        size = int(replay.get("size", DEFAULT_BOARD_SIZE))
        board = Board(size=size, win_length=int(replay.get("win_length", 0)) or 0)
        states = [board.copy()]
        for move in replay.get("moves", []):  # type: ignore[union-attr]
            board.place(int(move["index"]), Mark(move["mark"]))
            states.append(board.copy())
        return states
