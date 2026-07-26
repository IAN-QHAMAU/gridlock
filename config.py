"""Application configuration: enums, themes, tunables and environment settings.

This module is dependency-light on purpose so every other module can import it
without creating cycles.  Nothing here touches Streamlit.

Design note — *NOTATION*: the whole interface is set in one monospaced family.
There is no display face.  Hierarchy comes from weight, size, case and
tracking, which means the app loads a single webfont instead of four families.
Each theme is a stock and an ink rather than a hue rotation, and the set is
dark-first — the text is dimmed rather than white, and every accent is
desaturated, so a long session doesn't glare:

    Slate      neutral charcoal, dusty blue and clay     (default)
    Duotone    warm charcoal, rose and teal
    Blueprint  white linework on dimmed blue ground
    Green bar  fanfold printout after hours
    Graph      blue and red pen on graph paper           (light)
    Plain      black on white, no texture                (light)

Theme *keys* are unchanged, so anything already persisted keeps working.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

try:  # optional, but very convenient for local development
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - python-dotenv is an optional extra
    pass


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
APP_NAME: Final[str] = "GRIDLOCK"
APP_TAGLINE: Final[str] = "Tic-Tac-Toe, reinvented."
VERSION: Final[str] = "1.0.0"

BASE_DIR: Final[Path] = Path(__file__).resolve().parent
ASSETS_DIR: Final[Path] = BASE_DIR / "assets"
SOUNDS_DIR: Final[Path] = ASSETS_DIR / "sounds"
ICONS_DIR: Final[Path] = ASSETS_DIR / "icons"
THEMES_DIR: Final[Path] = ASSETS_DIR / "themes"
LOGO_PATH: Final[Path] = ASSETS_DIR / "logo.png"
DATA_DIR: Final[Path] = Path(os.getenv("TTT_DATA_DIR", str(BASE_DIR / "data")))


# --------------------------------------------------------------------------- #
# Core enums
# --------------------------------------------------------------------------- #
class GameMode(str, Enum):
    """How a match is being played."""

    SINGLE = "single"
    LOCAL = "local"
    ONLINE = "online"

    @property
    def label(self) -> str:
        return {
            GameMode.SINGLE: "Human vs AI",
            GameMode.LOCAL: "Human vs Human (same device)",
            GameMode.ONLINE: "Online multiplayer",
        }[self]


class Difficulty(str, Enum):
    """AI strength levels."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    IMPOSSIBLE = "impossible"

    @property
    def label(self) -> str:
        return {
            Difficulty.EASY: "Easy",
            Difficulty.MEDIUM: "Medium",
            Difficulty.HARD: "Hard",
            Difficulty.IMPOSSIBLE: "Impossible",
        }[self]

    @property
    def description(self) -> str:
        return {
            Difficulty.EASY: "Plays at random. Good for warming up.",
            Difficulty.MEDIUM: "Takes the win, blocks the obvious loss.",
            Difficulty.HARD: "Minimax search, depth limited.",
            Difficulty.IMPOSSIBLE: "Minimax with alpha-beta pruning. Never loses on 3x3.",
        }[self]

    @property
    def search_depth(self) -> int:
        """Maximum plies searched (only binding on boards larger than 3x3)."""
        return {
            Difficulty.EASY: 0,
            Difficulty.MEDIUM: 1,
            Difficulty.HARD: 4,
            Difficulty.IMPOSSIBLE: 6,
        }[self]

    @property
    def think_seconds(self) -> float:
        """Cosmetic delay so the AI feels like it is deliberating."""
        return {
            Difficulty.EASY: 0.25,
            Difficulty.MEDIUM: 0.35,
            Difficulty.HARD: 0.5,
            Difficulty.IMPOSSIBLE: 0.6,
        }[self]


class GameStatus(str, Enum):
    """Lifecycle of a match."""

    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
    ABANDONED = "abandoned"


class RoomRole(str, Enum):
    """Who a viewer is inside an online room."""

    HOST = "host"
    GUEST = "guest"
    SPECTATOR = "spectator"


class ThemeName(str, Enum):
    """Available visual themes.

    The keys are stable identifiers,not descriptions they are stored in save
    files and room records.The human-readable name lives in ``Theme.label``.
    """

    NEON = "neon"  # Duotone
    DARK = "dark"  # Slate
    LIGHT = "light"  # Graph (light stock)
    RETRO = "retro"  # Green bar
    MINIMAL = "minimal"  # Plain (light stock)
    CYBERPUNK = "cyberpunk"  # Blueprint


#: Pin the app to a single palette.
#:
#: Leave as ``None`` to let players pick a theme from the sidebar and the
#: Settings page.  Set it to a member of :class:`ThemeName` — for example
#: ``LOCKED_THEME = ThemeName.DARK`` — and that palette is used everywhere,
#: with both theme selectors hidden.
LOCKED_THEME: Final[ThemeName | None] = None


class SoundName(str, Enum):
    """Sound effect identifiers (map 1:1 to files in assets/sounds)."""

    MOVE = "move"
    WIN = "win"
    LOSE = "lose"
    DRAW = "draw"
    CLICK = "click"


# --------------------------------------------------------------------------- #
# Themes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Theme:
    """A complete set of design tokens rendered into CSS custom properties."""

    key: ThemeName
    label: str
    dark: bool
    bg: str
    bg_alt: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text_muted: str
    primary: str
    secondary: str
    accent: str
    x_color: str
    o_color: str
    win: str
    danger: str
    success: str
    display_font: str
    body_font: str
    radius: str
    glow: str
    grid_line: str

    def as_css_vars(self) -> str:
        """Render the theme as a block of CSS custom properties."""
        return "\n".join(
            f"  --ttt-{name.replace('_', '-')}: {value};"
            for name, value in (
                ("bg", self.bg),
                ("bg-alt", self.bg_alt),
                ("surface", self.surface),
                ("surface-alt", self.surface_alt),
                ("border", self.border),
                ("text", self.text),
                ("text-muted", self.text_muted),
                ("primary", self.primary),
                ("secondary", self.secondary),
                ("accent", self.accent),
                ("x-color", self.x_color),
                ("o-color", self.o_color),
                ("win", self.win),
                ("danger", self.danger),
                ("success", self.success),
                ("display-font", self.display_font),
                ("body-font", self.body_font),
                ("radius", self.radius),
                ("glow", self.glow),
                ("grid-line", self.grid_line),
            )
        )


# One webfont for the entire application.  `display_font` and `body_font` stay
# separate fields so a future theme can break the rule, but every shipped theme
# sets them to the same stack — that single-family discipline *is* the look.
_MONO: Final[str] = (
    "'JetBrains Mono', ui-monospace, SFMono-Regular, 'SF Mono', "
    "Menlo, Consolas, 'Liberation Mono', monospace"
)

# Green bar is a dot-matrix printout, so it uses a face that ships with every
# OS and costs nothing to load.
_TYPEWRITER: Final[str] = "'Courier New', Courier, ui-monospace, monospace"

THEMES: Final[dict[ThemeName, Theme]] = {
    # Neutral charcoal, dimmed ink. Low glare, low saturation, long sessions.
    ThemeName.DARK: Theme(
        key=ThemeName.DARK,
        label="Slate",
        dark=True,
        bg="#15171b",
        bg_alt="#191c21",
        surface="#1a1d22",
        surface_alt="#202329",
        border="rgba(201, 206, 214, 0.13)",
        text="#c9ced6",
        text_muted="#79818c",
        primary="#7fa6d8",
        secondary="#a99bd1",
        accent="#d9a566",
        x_color="#7fa6d8",
        o_color="#d08c7a",
        win="#7fbf9a",
        danger="#d98c8c",
        success="#7fbf9a",
        display_font=_MONO,
        body_font=_MONO,
        radius="3px",
        glow="0 0 0 2px rgba(127, 166, 216, 0.28)",
        grid_line="rgba(201, 206, 214, 0.13)",
    ),
    # Warm charcoal with a dusty two-ink palette. The colourful dark one.
    ThemeName.NEON: Theme(
        key=ThemeName.NEON,
        label="Duotone",
        dark=True,
        bg="#17151a",
        bg_alt="#1b1920",
        surface="#1d1a22",
        surface_alt="#232029",
        border="rgba(226, 214, 226, 0.13)",
        text="#d3cbd4",
        text_muted="#857c88",
        primary="#c98bb0",
        secondary="#6fb3b8",
        accent="#d1a45f",
        x_color="#c98bb0",
        o_color="#6fb3b8",
        win="#c9a95f",
        danger="#cf8080",
        success="#79b892",
        display_font=_MONO,
        body_font=_MONO,
        radius="0px",
        glow="0 0 0 2px rgba(201, 139, 176, 0.28)",
        grid_line="rgba(226, 214, 226, 0.14)",
    ),
    # White linework on dimmed blue ground, amber for annotations.
    ThemeName.CYBERPUNK: Theme(
        key=ThemeName.CYBERPUNK,
        label="Blueprint",
        dark=True,
        bg="#0f1e2e",
        bg_alt="#122334",
        surface="#132638",
        surface_alt="#182d41",
        border="rgba(198, 216, 236, 0.16)",
        text="#c2d3e4",
        text_muted="#7690a8",
        primary="#8fb8d8",
        secondary="#b6c9dc",
        accent="#d9ad6a",
        x_color="#d6e3f0",
        o_color="#8fb8d8",
        win="#d9ad6a",
        danger="#d68b83",
        success="#84bfa2",
        display_font=_MONO,
        body_font=_MONO,
        radius="0px",
        glow="0 0 0 2px rgba(143, 184, 216, 0.28)",
        grid_line="rgba(198, 216, 236, 0.15)",
    ),
    # Fanfold printout after hours: deep pine bands, sage ink, rust marks.
    ThemeName.RETRO: Theme(
        key=ThemeName.RETRO,
        label="Green bar",
        dark=True,
        bg="#141a15",
        bg_alt="#171e18",
        surface="#18201a",
        surface_alt="#1e271f",
        border="rgba(195, 208, 190, 0.14)",
        text="#c3d0be",
        text_muted="#788573",
        primary="#8fbf94",
        secondary="#c9a06e",
        accent="#c9805e",
        x_color="#c9805e",
        o_color="#8fbf94",
        win="#a8c98a",
        danger="#cf8a80",
        success="#8fbf94",
        display_font=_TYPEWRITER,
        body_font=_TYPEWRITER,
        radius="0px",
        glow="0 0 0 2px rgba(143, 191, 148, 0.28)",
        grid_line="rgba(195, 208, 190, 0.15)",
    ),
    # The two light stocks, kept for anyone who wants paper. Not the default.
    ThemeName.LIGHT: Theme(
        key=ThemeName.LIGHT,
        label="Graph",
        dark=False,
        bg="#f4f4f2",
        bg_alt="#eceef1",
        surface="#fbfbfa",
        surface_alt="#f2f4f7",
        border="rgba(16, 24, 40, 0.16)",
        text="#242a33",
        text_muted="#5f6875",
        primary="#3355c4",
        secondary="#6b4ee6",
        accent="#9a5b12",
        x_color="#3355c4",
        o_color="#b93a45",
        win="#2a7a56",
        danger="#b93a45",
        success="#2a7a56",
        display_font=_MONO,
        body_font=_MONO,
        radius="2px",
        glow="0 0 0 2px rgba(51, 85, 196, 0.28)",
        grid_line="rgba(51, 85, 196, 0.20)",
    ),
    ThemeName.MINIMAL: Theme(
        key=ThemeName.MINIMAL,
        label="Plain",
        dark=False,
        bg="#ffffff",
        bg_alt="#fafafa",
        surface="#ffffff",
        surface_alt="#fafafa",
        border="rgba(0, 0, 0, 0.16)",
        text="#111111",
        text_muted="#767676",
        primary="#111111",
        secondary="#555555",
        accent="#111111",
        x_color="#111111",
        o_color="#8a8a8a",
        win="#111111",
        danger="#b00020",
        success="#0f7b3f",
        display_font=_MONO,
        body_font=_MONO,
        radius="0px",
        glow="0 0 0 2px rgba(0, 0, 0, 0.30)",
        grid_line="rgba(0, 0, 0, 0.24)",
    ),
}

DEFAULT_THEME: Final[ThemeName] = ThemeName.DARK


def get_theme(name: ThemeName | str | None) -> Theme:
    """Return a theme by name, falling back to the default when unknown."""
    if isinstance(name, str):
        try:
            name = ThemeName(name)
        except ValueError:
            name = DEFAULT_THEME
    return THEMES.get(name or DEFAULT_THEME, THEMES[DEFAULT_THEME])


def theme_choices() -> tuple[ThemeName, ...]:
    """Theme keys in the order they should be offered to players."""
    return tuple(THEMES)


# --------------------------------------------------------------------------- #
# Gameplay tunables
# --------------------------------------------------------------------------- #
#: board size -> number of marks in a row required to win
BOARD_SIZES: Final[dict[int, int]] = {3: 3, 4: 4, 5: 4}
DEFAULT_BOARD_SIZE: Final[int] = 3

ROOM_CODE_LENGTH: Final[int] = 5
ROOM_CODE_ALPHABET: Final[str] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1
ROOM_TIMEOUT_MINUTES: Final[int] = 30
PRESENCE_TIMEOUT_SECONDS: Final[int] = 25
ONLINE_POLL_SECONDS: Final[float] = 2.0
TIMED_MODE_SECONDS: Final[int] = 15
MAX_CHAT_MESSAGES: Final[int] = 60
MAX_NAME_LENGTH: Final[int] = 20

AVATARS: Final[tuple[str, ...]] = (
    "🦊", "🐙", "🐼", "🦉", "🐺", "🐝", "🦁", "🐸",
    "🤖", "👾", "🛸", "🐲", "🦖", "🦄", "🌊", "⚡",
)

EMOJI_REACTIONS: Final[tuple[str, ...]] = ("👍", "😂", "😮", "🔥", "😤", "🤝", "🧠", "🎯")


def cell_name(index: int, size: int) -> str:
    """Board index as coordinate notation: 0 -> ``a1``, 4 -> ``b2`` on a 3x3.

    Columns are lettered left to right, rows numbered top to bottom.  Used on
    the board,in the move log and anywhere one player has to tell another
    where to play.
    """
    if size <= 0:
        return str(index)
    column, row = index % size, index // size
    return f"{chr(97 + column)}{row + 1}"


# --------------------------------------------------------------------------- #
# Environment settings
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Settings:
    """Runtime settings sourced from environment variables (or `.env`)."""

    supabase_url: str = ""
    supabase_key: str = ""
    supabase_service_key: str = ""
    enable_google_auth: bool = False
    oauth_redirect_url: str = ""
    data_dir: Path = field(default_factory=lambda: DATA_DIR)
    log_level: str = "INFO"
    force_local_backend: bool = False

    @property
    def supabase_configured(self) -> bool:
        """True when both a project URL and an API key are present."""
        return bool(self.supabase_url and self.supabase_key)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from the process environment."""

        def _flag(name: str, default: str = "false") -> bool:
            return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            supabase_url=os.getenv("SUPABASE_URL", "").strip(),
            supabase_key=os.getenv("SUPABASE_KEY", "").strip(),
            supabase_service_key=os.getenv("SUPABASE_SERVICE_KEY", "").strip(),
            enable_google_auth=_flag("ENABLE_GOOGLE_AUTH"),
            oauth_redirect_url=os.getenv("OAUTH_REDIRECT_URL", "").strip(),
            data_dir=Path(os.getenv("TTT_DATA_DIR", str(DATA_DIR))),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            force_local_backend=_flag("FORCE_LOCAL_BACKEND"),
        )


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    """Return the process-wide settings singleton.

    Streamlit secrets are merged in when available, so the app works both with a
    local `.env` file and with `st.secrets` on Streamlit Community Cloud.
    """
    global _settings
    if _settings is None or refresh:
        settings = Settings.from_env()
        _merge_streamlit_secrets(settings)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _settings = settings
    return _settings


def _merge_streamlit_secrets(settings: Settings) -> None:
    """Overlay `st.secrets` on top of environment variables when present."""
    try:  # pragma: no cover - depends on runtime environment
        import streamlit as st

        secrets = st.secrets
        mapping = {
            "SUPABASE_URL": "supabase_url",
            "SUPABASE_KEY": "supabase_key",
            "SUPABASE_SERVICE_KEY": "supabase_service_key",
            "OAUTH_REDIRECT_URL": "oauth_redirect_url",
        }
        for secret_key, attr in mapping.items():
            if secret_key in secrets and secrets[secret_key]:
                setattr(settings, attr, str(secrets[secret_key]).strip())
        if "ENABLE_GOOGLE_AUTH" in secrets:
            settings.enable_google_auth = str(secrets["ENABLE_GOOGLE_AUTH"]).lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
    except Exception:
        # No Streamlit context, or no secrets file: environment variables win.
        return


_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
)


def configure_logging(level: str | None = None) -> logging.Logger:
    """Configure and return the application logger (idempotent)."""
    logger = logging.getLogger("gridlock")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
    name = (level or get_settings().log_level or "INFO").upper()
    # `getattr(logging, name)` would happily return a function for something
    # like LOG_LEVEL=disable and blow up inside setLevel.
    logger.setLevel(name if name in _LOG_LEVELS else "INFO")
    return logger