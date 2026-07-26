"""Shared helpers: formatting, media playback, exports, achievements, challenges."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Callable, Sequence

import streamlit as st
import streamlit.components.v1 as components

from config import SOUNDS_DIR, Difficulty, SoundName, configure_logging
from database import Row, Stats, parse_ts

logger = configure_logging()


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def format_duration(seconds: float) -> str:
    """Render a duration compactly: ``42s``, ``3m 07s``, ``1h 04m``."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def relative_time(value: Any) -> str:
    """Human friendly "time ago" string for an ISO timestamp."""
    if not value:
        return "never"
    moment = parse_ts(value)
    delta = (datetime.now(timezone.utc) - moment).total_seconds()
    if delta < 45:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    if delta < 604800:
        return f"{int(delta // 86400)} d ago"
    return moment.date().isoformat()


def percentage(part: float, whole: float) -> float:
    """Safe percentage helper."""
    return round(100.0 * part / whole, 1) if whole else 0.0


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=16)
def _sound_data_uri(name: str) -> str | None:
    """Base64 data URI for a bundled WAV file (cached)."""
    path = SOUNDS_DIR / f"{name}.wav"
    if not path.exists():
        logger.debug("Sound file missing: %s", path)
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:audio/wav;base64,{encoded}"


def _embed(markup: str, height: int) -> None:
    """Render an HTML/JavaScript island inside a sandboxed iframe.

    `st.components.v1.html` is deprecated in favour of `st.iframe`, so use the
    new API where it exists and fall back for older Streamlit releases.
    `st.iframe` rejects a height of zero, so invisible embeds — the sound
    player, for instance — collapse to a single pixel instead.
    """
    if hasattr(st, "iframe"):
        st.iframe(markup, height=max(height, 1))
    else:  # pragma: no cover - Streamlit < 1.53
        components.html(markup, height=height)


def play_sound(sound: SoundName | str, enabled: bool = True) -> None:
    """Play a short effect in the browser.

    Streamlit re-renders wipe the audio element, so a fresh one is injected each
    time with a unique key to force playback.
    """
    if not enabled:
        return
    name = sound.value if isinstance(sound, SoundName) else str(sound)
    uri = _sound_data_uri(name)
    if not uri:
        return
    token = st.session_state.get("_sound_token", 0) + 1
    st.session_state["_sound_token"] = token
    _embed(
        f"""
        <audio id="sfx-{token}" autoplay preload="auto">
          <source src="{uri}" type="audio/wav">
        </audio>
        <script>
          const el = document.getElementById("sfx-{token}");
          if (el) {{ el.volume = 0.55; el.play().catch(() => {{}}); }}
        </script>
        """,
        0,
    )


def confetti(enabled: bool = True, duration_ms: int = 2600) -> None:
    """Fire a lightweight canvas confetti burst (no external dependencies)."""
    if not enabled:
        return
    _embed(
        f"""
        <canvas id="ttt-confetti"></canvas>
        <style>
          html, body {{ margin: 0; overflow: hidden; background: transparent; }}
          #ttt-confetti {{ width: 100%; height: 220px; display: block; }}
        </style>
        <script>
          const canvas = document.getElementById("ttt-confetti");
          const ctx = canvas.getContext("2d");
          const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          canvas.width = canvas.offsetWidth; canvas.height = 220;
          const colors = ["#25f4ee", "#ff4d94", "#ffe14d", "#b6ff3b", "#c34bff"];
          const pieces = Array.from({{length: reduce ? 30 : 130}}, () => ({{
            x: Math.random() * canvas.width,
            y: Math.random() * -canvas.height,
            r: 3 + Math.random() * 5,
            c: colors[Math.floor(Math.random() * colors.length)],
            vx: -1.2 + Math.random() * 2.4,
            vy: 1.5 + Math.random() * 3.2,
            spin: Math.random() * Math.PI,
            vs: -0.15 + Math.random() * 0.3
          }}));
          const started = performance.now();
          function frame(now) {{
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            pieces.forEach(p => {{
              p.x += p.vx; p.y += p.vy; p.spin += p.vs;
              ctx.save();
              ctx.translate(p.x, p.y);
              ctx.rotate(p.spin);
              ctx.fillStyle = p.c;
              ctx.fillRect(-p.r, -p.r * 0.5, p.r * 2, p.r);
              ctx.restore();
            }});
            if (now - started < {duration_ms}) requestAnimationFrame(frame);
            else ctx.clearRect(0, 0, canvas.width, canvas.height);
          }}
          requestAnimationFrame(frame);
        </script>
        """,
        220,
    )


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #
def games_to_csv(rows: Sequence[Row]) -> str:
    """Flatten game rows into CSV text (replays are omitted for readability)."""
    buffer = io.StringIO()
    columns = [
        "created_at", "mode", "result", "player_mark", "opponent",
        "difficulty", "size", "moves", "duration", "room_code",
    ]
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in columns})
    return buffer.getvalue()


def games_to_json(rows: Sequence[Row]) -> str:
    """Full export including replays."""
    payload = []
    for row in rows:
        item = dict(row)
        replay = item.get("replay")
        if isinstance(replay, str):
            try:
                item["replay"] = json.loads(replay or "{}")
            except json.JSONDecodeError:
                item["replay"] = {}
        payload.append(item)
    return json.dumps(payload, indent=2, ensure_ascii=False)


def load_replay(row: Row) -> dict[str, Any]:
    """Return a game row's replay payload as a dictionary."""
    replay = row.get("replay")
    if isinstance(replay, dict):
        return replay
    try:
        return json.loads(replay or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


# --------------------------------------------------------------------------- #
# Achievements
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Achievement:
    """An unlockable badge."""

    code: str
    title: str
    description: str
    icon: str
    check: Callable[[Stats, dict[str, Any]], bool]

    def evaluate(self, stats: Stats, context: dict[str, Any]) -> bool:
        """Safely run the unlock condition."""
        try:
            return bool(self.check(stats, context))
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Achievement %s failed to evaluate: %s", self.code, exc)
            return False


ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement(
        "first_blood", "First Win", "Win your first game.", "🥇",
        lambda s, c: s.wins >= 1,
    ),
    Achievement(
        "hat_trick", "Hat Trick", "Win three games in a row.", "🎩",
        lambda s, c: s.current_streak >= 3,
    ),
    Achievement(
        "unstoppable", "Unstoppable", "Win five games in a row.", "🔥",
        lambda s, c: s.current_streak >= 5,
    ),
    Achievement(
        "giant_slayer", "Giant Slayer", "Beat the Impossible bot.", "🐉",
        lambda s, c: c.get("result") == "win" and c.get("difficulty") == Difficulty.IMPOSSIBLE.value,
    ),
    Achievement(
        "wall", "Immovable", "Draw against the Impossible bot.", "🧱",
        lambda s, c: c.get("result") == "draw" and c.get("difficulty") == Difficulty.IMPOSSIBLE.value,
    ),
    Achievement(
        "speedrun", "Speedrun", "Win a 3x3 game in five moves.", "⚡",
        lambda s, c: c.get("result") == "win" and c.get("size") == 3 and c.get("moves", 99) <= 5,
    ),
    Achievement(
        "big_board", "Room to Think", "Win on a 5x5 board.", "🗺️",
        lambda s, c: c.get("result") == "win" and c.get("size") == 5,
    ),
    Achievement(
        "veteran", "Veteran", "Play 25 games.", "🎖️",
        lambda s, c: s.games_played >= 25,
    ),
    Achievement(
        "centurion", "Centurion", "Play 100 games.", "🏛️",
        lambda s, c: s.games_played >= 100,
    ),
    Achievement(
        "online_debut", "Going Live", "Finish an online match.", "🌐",
        lambda s, c: c.get("mode") == "online",
    ),
    Achievement(
        "online_champ", "Net Runner", "Win five online matches.", "📡",
        lambda s, c: s.by_mode.get("online", 0) >= 5 and s.wins >= 5,
    ),
    Achievement(
        "sharpshooter", "Sharpshooter", "Reach a 70% win rate over 20+ games.", "🎯",
        lambda s, c: s.games_played >= 20 and s.win_rate >= 70,
    ),
)

ACHIEVEMENTS_BY_CODE: dict[str, Achievement] = {a.code: a for a in ACHIEVEMENTS}


def evaluate_achievements(stats: Stats, context: dict[str, Any]) -> list[Achievement]:
    """Return every achievement whose condition currently holds."""
    return [a for a in ACHIEVEMENTS if a.evaluate(stats, context)]


# --------------------------------------------------------------------------- #
# Daily challenges
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Challenge:
    """A deterministic daily objective."""

    code: str
    title: str
    description: str
    difficulty: Difficulty
    size: int
    check: Callable[[dict[str, Any]], bool]

    def is_satisfied(self, context: dict[str, Any]) -> bool:
        """Evaluate the objective against a finished-game context."""
        try:
            return bool(self.check(context))
        except Exception:  # pragma: no cover - defensive
            return False


_CHALLENGE_POOL: tuple[Challenge, ...] = (
    Challenge(
        "beat_hard_3", "Break the Wall", "Beat the Hard bot on a 3x3 board.",
        Difficulty.HARD, 3,
        lambda c: c.get("result") == "win" and c.get("difficulty") == "hard" and c.get("size") == 3,
    ),
    Challenge(
        "draw_impossible", "Hold the Line", "Force a draw against the Impossible bot.",
        Difficulty.IMPOSSIBLE, 3,
        lambda c: c.get("result") in {"draw", "win"} and c.get("difficulty") == "impossible",
    ),
    Challenge(
        "quick_win", "Five and Done", "Win a 3x3 game in five moves or fewer.",
        Difficulty.MEDIUM, 3,
        lambda c: c.get("result") == "win" and c.get("size") == 3 and c.get("moves", 99) <= 5,
    ),
    Challenge(
        "big_board_win", "Wide Angle", "Win a game on a 4x4 board.",
        Difficulty.MEDIUM, 4,
        lambda c: c.get("result") == "win" and c.get("size") == 4,
    ),
    Challenge(
        "clean_sweep", "Clean Sweep", "Win without letting the bot take the centre.",
        Difficulty.HARD, 3,
        lambda c: c.get("result") == "win" and not c.get("opponent_took_centre", False),
    ),
    Challenge(
        "marathon", "Marathon", "Win a 5x5 game.",
        Difficulty.MEDIUM, 5,
        lambda c: c.get("result") == "win" and c.get("size") == 5,
    ),
    Challenge(
        "no_undo", "Committed", "Win a game without using undo.",
        Difficulty.HARD, 3,
        lambda c: c.get("result") == "win" and c.get("undos", 0) == 0,
    ),
)


def challenge_for(day: date | None = None) -> Challenge:
    """The challenge for a given day (same for everyone, changes daily)."""
    day = day or datetime.now(timezone.utc).date()
    digest = hashlib.sha256(day.isoformat().encode()).digest()
    return _CHALLENGE_POOL[digest[0] % len(_CHALLENGE_POOL)]


# --------------------------------------------------------------------------- #
# Streamlit helpers
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _supports_width_param() -> bool:
    """True on Streamlit versions that expose the newer ``width`` argument."""
    import inspect

    try:
        return "width" in inspect.signature(st.button).parameters
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return False


def stretch() -> dict[str, Any]:
    """Kwargs that make a widget fill its container on any recent Streamlit."""
    return {"width": "stretch"} if _supports_width_param() else {"use_container_width": True}


def rerun() -> None:
    """Rerun the script, tolerating Streamlit API changes."""
    try:
        st.rerun()
    except AttributeError:  # pragma: no cover - very old Streamlit
        st.experimental_rerun()  # type: ignore[attr-defined]


def toast(message: str, icon: str = "✅") -> None:
    """Show a transient toast, falling back to an info box."""
    try:
        st.toast(message, icon=icon)
    except Exception:  # pragma: no cover - older Streamlit
        st.info(f"{icon} {message}")