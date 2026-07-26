"""Regenerate every binary asset the app ships with.

Run from the project root::

    python scripts/generate_assets.py

Assets are generated rather than downloaded so the repository stays free of
third-party licensed media, and so every colour stays in sync with the themes
defined in `config.py`.
"""

from __future__ import annotations

import json
import math
import struct
import sys
import wave
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import ICONS_DIR, LOGO_PATH, SOUNDS_DIR, THEMES, THEMES_DIR  # noqa: E402

SAMPLE_RATE = 44_100


# --------------------------------------------------------------------------- #
# Sound
# --------------------------------------------------------------------------- #
def _envelope(position: float, attack: float = 0.02, release: float = 0.35) -> float:
    """Simple attack/release envelope in the range 0..1."""
    if position < attack:
        return position / attack
    if position > 1 - release:
        return max(0.0, (1 - position) / release)
    return 1.0


def tone(
    frequency: float,
    duration: float,
    volume: float = 0.4,
    shape: str = "sine",
    sweep: float = 0.0,
) -> list[float]:
    """Generate a single tone, optionally sweeping to ``sweep`` Hz."""
    frames = int(SAMPLE_RATE * duration)
    samples: list[float] = []
    for index in range(frames):
        position = index / frames
        freq = frequency + (sweep - frequency) * position if sweep else frequency
        phase = 2 * math.pi * freq * (index / SAMPLE_RATE)
        if shape == "square":
            value = 1.0 if math.sin(phase) >= 0 else -1.0
        elif shape == "triangle":
            value = 2 / math.pi * math.asin(math.sin(phase))
        else:
            value = math.sin(phase)
        samples.append(value * volume * _envelope(position))
    return samples


def sequence(*parts: list[float]) -> list[float]:
    """Concatenate tone segments."""
    joined: list[float] = []
    for part in parts:
        joined.extend(part)
    return joined


def write_wav(path: Path, samples: list[float]) -> None:
    """Write mono 16-bit PCM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        frames = b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767)) for sample in samples
        )
        handle.writeframes(frames)


def build_sounds() -> None:
    """Create the five effects used by the app."""
    write_wav(SOUNDS_DIR / "move.wav", tone(660, 0.09, 0.35, "triangle", sweep=880))
    write_wav(SOUNDS_DIR / "click.wav", tone(1200, 0.045, 0.22, "square"))
    write_wav(
        SOUNDS_DIR / "win.wav",
        sequence(
            tone(523.25, 0.11, 0.34, "triangle"),
            tone(659.25, 0.11, 0.34, "triangle"),
            tone(783.99, 0.11, 0.34, "triangle"),
            tone(1046.50, 0.26, 0.36, "triangle"),
        ),
    )
    write_wav(
        SOUNDS_DIR / "lose.wav",
        sequence(
            tone(392.00, 0.14, 0.30, "sine"),
            tone(311.13, 0.16, 0.30, "sine"),
            tone(233.08, 0.30, 0.28, "sine"),
        ),
    )
    write_wav(
        SOUNDS_DIR / "draw.wav",
        sequence(tone(440, 0.14, 0.28, "sine"), tone(440, 0.20, 0.24, "sine")),
    )


# --------------------------------------------------------------------------- #
# Logo
# --------------------------------------------------------------------------- #
def build_logo(size: int = 512) -> None:
    """Draw the app icon: an etched grid with X, O and a live winning line."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow not installed — skipping logo.png")
        return

    scale = 4  # supersample for smooth edges
    canvas = Image.new("RGBA", (size * scale, size * scale), (5, 6, 11, 255))
    draw = ImageDraw.Draw(canvas)
    unit = size * scale / 200

    def point(x: float, y: float) -> tuple[float, float]:
        return x * unit, y * unit

    draw.rounded_rectangle(
        [point(6, 6), point(194, 194)], radius=int(18 * unit), outline=(37, 244, 238, 90),
        width=int(2 * unit),
    )
    grid = (37, 244, 238, 150)
    for offset in (70, 130):
        draw.line([point(offset, 26), point(offset, 174)], fill=grid, width=int(3 * unit))
        draw.line([point(26, offset), point(174, offset)], fill=grid, width=int(3 * unit))

    def cross(cx: float, cy: float, colour: tuple[int, int, int, int]) -> None:
        arm = 15
        draw.line([point(cx - arm, cy - arm), point(cx + arm, cy + arm)], fill=colour, width=int(7 * unit))
        draw.line([point(cx + arm, cy - arm), point(cx - arm, cy + arm)], fill=colour, width=int(7 * unit))

    def circle(cx: float, cy: float, colour: tuple[int, int, int, int]) -> None:
        draw.ellipse([point(cx - 15, cy - 15), point(cx + 15, cy + 15)], outline=colour, width=int(7 * unit))

    x_colour = (37, 244, 238, 255)
    o_colour = (255, 77, 148, 255)
    cross(47, 47, x_colour)
    circle(100, 47, o_colour)
    cross(100, 100, x_colour)
    circle(153, 100, o_colour)
    cross(153, 153, x_colour)
    draw.line([point(40, 40), point(160, 160)], fill=(182, 255, 59, 230), width=int(6 * unit))

    LOGO_PATH.parent.mkdir(parents=True, exist_ok=True)
    canvas.resize((size, size), Image.LANCZOS).save(LOGO_PATH)


# --------------------------------------------------------------------------- #
# Icons & themes
# --------------------------------------------------------------------------- #
ICONS: dict[str, str] = {
    "mark-x": '<line x1="14" y1="14" x2="34" y2="34"/><line x1="34" y1="14" x2="14" y2="34"/>',
    "mark-o": '<circle cx="24" cy="24" r="11"/>',
    "grid": (
        '<line x1="18" y1="8" x2="18" y2="40"/><line x1="30" y1="8" x2="30" y2="40"/>'
        '<line x1="8" y1="18" x2="40" y2="18"/><line x1="8" y1="30" x2="40" y2="30"/>'
    ),
    "bot": (
        '<rect x="12" y="16" width="24" height="18" rx="5"/>'
        '<line x1="24" y1="8" x2="24" y2="16"/><circle cx="19" cy="25" r="2"/>'
        '<circle cx="29" cy="25" r="2"/>'
    ),
    "trophy": (
        '<path d="M16 10h16v9a8 8 0 0 1-16 0z"/><path d="M16 13h-5a6 6 0 0 0 6 6"/>'
        '<path d="M32 13h5a6 6 0 0 1-6 6"/><line x1="24" y1="27" x2="24" y2="34"/>'
        '<line x1="17" y1="38" x2="31" y2="38"/>'
    ),
    "online": (
        '<circle cx="24" cy="24" r="14"/><path d="M10 24h28"/>'
        '<path d="M24 10a20 20 0 0 1 0 28a20 20 0 0 1 0-28"/>'
    ),
}


def build_icons() -> None:
    """Write the small SVG icon set (currentColor-driven, theme agnostic)."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in ICONS.items():
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="48" height="48" '
            'fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" '
            f'stroke-linejoin="round" role="img" aria-label="{name}">{body}</svg>'
        )
        (ICONS_DIR / f"{name}.svg").write_text(svg, encoding="utf-8")


def build_themes() -> None:
    """Export the theme tokens as JSON (useful for design handoff)."""
    THEMES_DIR.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        payload = asdict(theme)
        payload["key"] = name.value
        (THEMES_DIR / f"{name.value}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )


def main() -> None:
    """Build every asset."""
    build_sounds()
    build_icons()
    build_themes()
    build_logo()
    print(f"Assets written to {ROOT / 'assets'}")


if __name__ == "__main__":
    main()
