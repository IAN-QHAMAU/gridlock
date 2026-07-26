"""Player domain objects."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum

from config import AVATARS, MAX_NAME_LENGTH, Difficulty


class Mark(str, Enum):
    """A board mark."""

    X = "X"
    O = "O"  # noqa: E741 - the mark really is called "O"

    @property
    def opponent(self) -> "Mark":
        """The other mark."""
        return Mark.O if self is Mark.X else Mark.X

    @property
    def label(self) -> str:
        return self.value


class PlayerType(str, Enum):
    """What is driving a seat at the table."""

    HUMAN = "human"
    AI = "ai"
    REMOTE = "remote"


def sanitize_name(name: str | None, fallback: str = "Player") -> str:
    """Trim, collapse and clamp a display name.

    Args:
        name: Raw user input.
        fallback: Value used when the input is empty after cleaning.

    Returns:
        A safe, human-readable display name.
    """
    cleaned = " ".join((name or "").split())
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    cleaned = cleaned[:MAX_NAME_LENGTH].strip()
    return cleaned or fallback


@dataclass(slots=True)
class Player:
    """A participant in a match (local human, AI or remote human)."""

    name: str
    mark: Mark
    kind: PlayerType = PlayerType.HUMAN
    avatar: str = AVATARS[0]
    player_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    difficulty: Difficulty | None = None

    def __post_init__(self) -> None:
        self.name = sanitize_name(self.name, fallback=self.mark.value)
        if self.avatar not in AVATARS:
            self.avatar = AVATARS[0]
        if self.kind is PlayerType.AI and self.difficulty is None:
            self.difficulty = Difficulty.MEDIUM

    @property
    def is_ai(self) -> bool:
        """True when moves are produced by the engine."""
        return self.kind is PlayerType.AI

    @property
    def display(self) -> str:
        """Avatar + name, ready for the UI."""
        return f"{self.avatar} {self.name}"

    def to_dict(self) -> dict[str, str]:
        """Serialise for storage / replay export."""
        return {
            "player_id": self.player_id,
            "name": self.name,
            "mark": self.mark.value,
            "kind": self.kind.value,
            "avatar": self.avatar,
            "difficulty": self.difficulty.value if self.difficulty else "",
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "Player":
        """Rebuild a player from :meth:`to_dict` output."""
        difficulty = data.get("difficulty") or ""
        return cls(
            name=data.get("name", "Player"),
            mark=Mark(data.get("mark", "X")),
            kind=PlayerType(data.get("kind", "human")),
            avatar=data.get("avatar", AVATARS[0]),
            player_id=data.get("player_id", uuid.uuid4().hex),
            difficulty=Difficulty(difficulty) if difficulty else None,
        )

    @classmethod
    def ai(cls, mark: Mark, difficulty: Difficulty) -> "Player":
        """Factory for an engine-controlled player."""
        return cls(
            name=f"{difficulty.label} Bot",
            mark=mark,
            kind=PlayerType.AI,
            avatar="🤖",
            difficulty=difficulty,
        )
