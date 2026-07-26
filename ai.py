"""The opponent engine.

Four difficulties share one entry point, :meth:`AIEngine.choose_move`:

======  =========================================================
Easy    Uniformly random legal move.
Medium  Takes an immediate win, blocks an immediate loss, then
        prefers centre/corners.
Hard    Plain minimax (no pruning), depth limited on large boards.
Impos.  Minimax + alpha-beta pruning, move ordering, transposition
        table and iterative deepening.  Never loses on 3x3.
======  =========================================================
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Sequence

from board import EMPTY, Board
from config import Difficulty, configure_logging
from player import Mark

logger = configure_logging()

WIN_SCORE: int = 10_000
#: Hard ceiling so the UI never blocks on a large board.
TIME_BUDGET_SECONDS: float = 1.6

#: Transposition table entry kinds.  Storing the bound type alongside the score
#: is what makes it safe to reuse alpha-beta results across different windows.
EXACT, LOWER_BOUND, UPPER_BOUND = 0, 1, 2


@dataclass(slots=True)
class SearchStats:
    """Diagnostics for the last search (surfaced in the UI as flavour)."""

    nodes: int = 0
    depth: int = 0
    elapsed: float = 0.0
    strategy: str = "random"


@dataclass(slots=True)
class AIEngine:
    """Chooses moves for the computer player."""

    difficulty: Difficulty = Difficulty.MEDIUM
    seed: int | None = None
    stats: SearchStats = field(default_factory=SearchStats)
    _rng: random.Random = field(init=False, repr=False)
    _table: dict[tuple[str, bool, int], tuple[float, int]] = field(
        default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    # ------------------------------------------------------------------ API
    def choose_move(self, board: Board, mark: Mark) -> int:
        """Return the index the engine wants to play.

        Args:
            board: Current position (not mutated).
            mark: The mark the engine is playing.

        Returns:
            A legal cell index.

        Raises:
            ValueError: If the board has no legal moves.
        """
        moves = board.available_moves()
        if not moves:
            raise ValueError("No legal moves available")

        started = time.perf_counter()
        self.stats = SearchStats(strategy=self.difficulty.value)
        working = board.copy()

        if self.difficulty is Difficulty.EASY:
            move = self._random_move(moves)
        elif self.difficulty is Difficulty.MEDIUM:
            move = self._heuristic_move(working, mark)
        elif self.difficulty is Difficulty.HARD:
            move = self._search(working, mark, use_pruning=False)
        else:
            move = self._search(working, mark, use_pruning=True)

        self.stats.elapsed = time.perf_counter() - started
        logger.debug(
            "AI(%s) picked %s in %.3fs (%d nodes, depth %d)",
            self.difficulty.value,
            move,
            self.stats.elapsed,
            self.stats.nodes,
            self.stats.depth,
        )
        return move

    # -------------------------------------------------------------- easy/med
    def _random_move(self, moves: Sequence[int]) -> int:
        """Pick uniformly at random."""
        self.stats.strategy = "random"
        return self._rng.choice(list(moves))

    def _immediate(self, board: Board, mark: Mark) -> int | None:
        """Index that completes a line for ``mark``, if one exists."""
        for index in board.available_moves():
            board.cells[index] = mark.value
            won = board.winner() == mark
            board.cells[index] = EMPTY
            if won:
                return index
        return None

    def _heuristic_move(self, board: Board, mark: Mark) -> int:
        """Win now, else block, else take strong squares."""
        if (win := self._immediate(board, mark)) is not None:
            self.stats.strategy = "win"
            return win
        if (block := self._immediate(board, mark.opponent)) is not None:
            self.stats.strategy = "block"
            return block

        # Occasional slip keeps Medium beatable and human-feeling.
        if self._rng.random() < 0.12:
            return self._random_move(board.available_moves())

        self.stats.strategy = "positional"
        for index in self._preferred_squares(board):
            if board.is_empty_cell(index):
                return index
        return self._random_move(board.available_moves())

    @staticmethod
    def _preferred_squares(board: Board) -> list[int]:
        """Centre first, then corners, then edges."""
        size = board.size
        centre = [
            r * size + c
            for r in range((size - 1) // 2, size // 2 + 1)
            for c in range((size - 1) // 2, size // 2 + 1)
        ]
        corners = [0, size - 1, size * (size - 1), size * size - 1]
        rest = [i for i in range(board.area) if i not in centre and i not in corners]
        return centre + corners + rest

    # --------------------------------------------------------------- search
    def _max_depth(self, board: Board) -> int:
        """Depth cap: exhaustive on 3x3, budgeted on larger grids."""
        empties = len(board.available_moves())
        if board.size == 3:
            return empties
        return min(empties, self.difficulty.search_depth)

    def _candidates(self, board: Board) -> list[int]:
        """Legal moves worth searching, best-first.

        On boards larger than 3x3 the search is restricted to cells adjacent to
        existing marks (plus the centre) — a standard and safe restriction for
        connection games that keeps the branching factor manageable.
        """
        moves = board.available_moves()
        if board.size > 3 and board.move_count:
            size = board.size
            neighbourhood: set[int] = set()
            for index, cell in enumerate(board.cells):
                if cell == EMPTY:
                    continue
                row, col = divmod(index, size)
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        r, c = row + dr, col + dc
                        if 0 <= r < size and 0 <= c < size:
                            neighbour = r * size + c
                            if board.cells[neighbour] == EMPTY:
                                neighbourhood.add(neighbour)
            if neighbourhood:
                moves = sorted(neighbourhood)

        order = {index: rank for rank, index in enumerate(self._preferred_squares(board))}
        return sorted(moves, key=lambda index: order.get(index, 10**6))

    def _search(self, board: Board, mark: Mark, use_pruning: bool) -> int:
        """Root search with iterative deepening and a wall-clock budget."""
        self.stats.strategy = "alpha-beta" if use_pruning else "minimax"
        self._table.clear()

        # Cheap tactical shortcuts keep large boards responsive and sane.
        if (win := self._immediate(board, mark)) is not None:
            self.stats.depth = 1
            return win
        if (block := self._immediate(board, mark.opponent)) is not None:
            self.stats.depth = 1
            return block

        deadline = time.perf_counter() + TIME_BUDGET_SECONDS
        candidates = self._candidates(board)
        best_move = candidates[0]
        max_depth = self._max_depth(board)

        for depth in range(1, max_depth + 1):
            best_score = float("-inf")
            depth_best: list[int] = []
            timed_out = False

            for index in candidates:
                # Root moves are searched with a full window so every score is
                # exact: ties are then real ties, and picking among them at
                # random can never smuggle in a losing move.
                board.cells[index] = mark.value
                score = self._minimax(
                    board,
                    mark,
                    depth - 1,
                    maximising=False,
                    alpha=float("-inf"),
                    beta=float("inf"),
                    use_pruning=use_pruning,
                    deadline=deadline,
                )
                board.cells[index] = EMPTY

                if score > best_score:
                    best_score, depth_best = score, [index]
                elif score == best_score:
                    depth_best.append(index)

                if time.perf_counter() > deadline:
                    timed_out = True
                    break

            if depth_best:
                best_move = self._rng.choice(depth_best)
                # Search best-first next iteration.
                candidates = depth_best + [i for i in candidates if i not in depth_best]
                self.stats.depth = depth
            if timed_out:
                break
            if best_score >= WIN_SCORE - 100:  # forced win found, stop early
                break

        return best_move

    def _minimax(
        self,
        board: Board,
        mark: Mark,
        depth: int,
        maximising: bool,
        alpha: float,
        beta: float,
        use_pruning: bool,
        deadline: float,
    ) -> float:
        """Minimax returning a score from ``mark``'s point of view."""
        self.stats.nodes += 1
        alpha_original, beta_original = alpha, beta
        key = (board.to_string(), maximising, depth)

        if use_pruning and (cached := self._table.get(key)) is not None:
            score, kind = cached
            if kind == EXACT:
                return score
            if kind == LOWER_BOUND:
                alpha = max(alpha, score)
            else:
                beta = min(beta, score)
            if alpha >= beta:
                return score

        winner = board.winner()
        if winner is not None:
            score = float(WIN_SCORE - (board.area - depth)) if winner is mark else float(
                -WIN_SCORE + (board.area - depth)
            )
            return score
        if board.is_full():
            return 0.0
        if depth <= 0 or time.perf_counter() > deadline:
            return self._evaluate(board, mark)

        turn = mark if maximising else mark.opponent
        best = float("-inf") if maximising else float("inf")

        for index in self._candidates(board):
            board.cells[index] = turn.value
            score = self._minimax(
                board, mark, depth - 1, not maximising, alpha, beta, use_pruning, deadline
            )
            board.cells[index] = EMPTY

            if maximising:
                best = max(best, score)
                alpha = max(alpha, best)
            else:
                best = min(best, score)
                beta = min(beta, best)
            if use_pruning and beta <= alpha:
                break

        if use_pruning:
            if best <= alpha_original:
                kind = UPPER_BOUND
            elif best >= beta_original:
                kind = LOWER_BOUND
            else:
                kind = EXACT
            self._table[key] = (best, kind)
        return best

    def _evaluate(self, board: Board, mark: Mark) -> float:
        """Heuristic for non-terminal positions on depth-limited searches.

        Open lines are worth exponentially more as they fill up, so the engine
        naturally builds threats and blocks the opponent's.
        """
        me, them = mark.value, mark.opponent.value
        score = 0.0
        for line in board.lines:
            mine = sum(1 for i in line if board.cells[i] == me)
            theirs = sum(1 for i in line if board.cells[i] == them)
            if mine and theirs:
                continue  # dead line
            if mine:
                score += 10 ** (mine - 1)
            elif theirs:
                score -= 1.4 * 10 ** (theirs - 1)
        return score


def best_move(board: Board, mark: Mark, difficulty: Difficulty = Difficulty.IMPOSSIBLE) -> int:
    """Convenience wrapper for scripts and tests."""
    return AIEngine(difficulty=difficulty).choose_move(board, mark)
