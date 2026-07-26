"""The design system.

Everything visual that is not a Streamlit widget lives here: the CSS variables
derived from the active :class:`~config.Theme`, the mark-grid logo, the board
styling (driven by widget keys) and the small HTML fragments used across pages.

Design direction — *NOTATION*: the interface is a printed game record.  It is
set in one monospaced family with no display face; hierarchy comes from weight,
size, case and tracking.  Surfaces are flat, rules are hairlines, nothing glows
and nothing is blurred.

The board is the scoresheet.  An empty cell prints its coordinate, a played
cell carries its move number, and the winning line inverts to solid ink one
cell at a time, in the order the line was made.  That is the only loud moment
in the app, and the only place colour is allowed to fill an area rather than
draw a line.
"""

from __future__ import annotations

from functools import wraps
from html import escape
from typing import Callable, Iterable, Mapping, Sequence

from board import EMPTY, Board
from config import Theme, ThemeName, cell_name, get_theme
from player import Mark

#: One family, three weights.  The previous four-family import pulled roughly
#: eleven faces before first paint; this pulls three.
FONT_IMPORT = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=JetBrains+Mono:wght@400;500;700&display=swap');"
)


# --------------------------------------------------------------------------- #
# Markup helpers
# --------------------------------------------------------------------------- #
def _compact(markup: str) -> str:
    """Drop blank lines from generated markup.

    Streamlit passes everything through a Markdown parser before it reaches the
    page, and in Markdown a blank line ends an HTML block. A blank line inside
    a `<style>` element therefore closes it early and the remaining rules are
    printed to the screen as literal text. Stripping empty lines keeps each
    block intact; whitespace is meaningless in both CSS and HTML.
    """
    return "\n".join(line for line in markup.splitlines() if line.strip())


def _markup(func: Callable[..., str]) -> Callable[..., str]:
    """Compact whatever markup `func` returns."""

    @wraps(func)
    def wrapper(*args: object, **kwargs: object) -> str:
        return _compact(func(*args, **kwargs))

    return wrapper


# --------------------------------------------------------------------------- #
# Global stylesheet
# --------------------------------------------------------------------------- #
@_markup
def base_css(theme: Theme, animations: bool = True) -> str:
    """Return the full application stylesheet for a theme."""
    motion = "" if animations else _motion_off()
    texture = _texture(theme)

    # Print surfaces are flat. Dark stock is the one case that needs a shadow,
    # and only to separate a raised banner from the page behind it.
    lift = "0 2px 0 rgba(0,0,0,0.35)" if theme.dark else "0 2px 0 var(--ttt-rule)"
    # Native widgets (scrollbars, date pickers, autofill) follow the stock.
    scheme = "dark" if theme.dark else "light"
    # On dark stock a solid heading at full text colour glares; step it back.
    heading = "color-mix(in srgb, var(--ttt-text) 92%, transparent)" if theme.dark else "var(--ttt-text)"
    ink = "color-mix(in srgb, var(--ttt-text) 45%, transparent)" if theme.dark else "var(--ttt-text)"
    # Inverting a button to full text colour flashes on dark stock.
    hover_bg = "var(--ttt-surface-alt)" if theme.dark else "var(--ttt-text)"
    hover_ink = "var(--ttt-primary)" if theme.dark else "var(--ttt-bg)"

    return f"""
<style>
{FONT_IMPORT}

:root {{
{theme.as_css_vars()}
  /* Derived tokens. Everything below is drawn from the theme above. */
  --ttt-rule: color-mix(in srgb, var(--ttt-border) 100%, transparent);
  --ttt-rule-soft: color-mix(in srgb, var(--ttt-border) 55%, transparent);
  --ttt-tint: color-mix(in srgb, var(--ttt-primary) 9%, transparent);
  --ttt-shadow: none;
  --ttt-lift: {lift};
  --ttt-hair: 1px solid var(--ttt-rule);
  --ttt-hair-soft: 1px solid var(--ttt-rule-soft);
  /* Heavy rules (section tops, the banner, the metric caps). On dark stock
     a full-strength bar is the brightest thing on the page. */
  --ttt-ink: {ink};
  /* Type: label / meta / body / lead / title */
  --ttt-t-label: 0.68rem;
  --ttt-t-meta: 0.76rem;
  --ttt-t-body: 0.9rem;
  --ttt-track-label: 0.16em;
  /* Space: a 4px scale, used everywhere. */
  --ttt-s1: 0.25rem; --ttt-s2: 0.5rem; --ttt-s3: 1rem;
  --ttt-s4: 1.5rem;  --ttt-s5: 2.5rem;
}}

/* ---------- shell ---------- */
html {{ color-scheme: {scheme}; }}
.stApp {{
  background: {texture} var(--ttt-bg);
  color: var(--ttt-text);
  font-family: var(--ttt-body-font);
  font-size: 15px;
  font-variant-ligatures: none;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}}
/* Quiet scrollbars: on dark stock the default ones are the brightest thing
   on screen. */
* {{ scrollbar-width: thin; scrollbar-color: var(--ttt-rule) transparent; }}
::-webkit-scrollbar {{ width: 9px; height: 9px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{
  background: var(--ttt-rule);
  border: 2px solid transparent;
  background-clip: content-box;
}}
::-webkit-scrollbar-thumb:hover {{ background: var(--ttt-text-muted); background-clip: content-box; }}
.stApp a {{
  color: var(--ttt-primary);
  text-underline-offset: 3px;
  text-decoration-thickness: 1px;
}}
h1, h2, h3, h4, h5 {{
  font-family: var(--ttt-display-font) !important;
  color: {heading} !important;
  font-weight: 700;
  letter-spacing: -0.01em;
}}
h3, h4, h5 {{
  font-size: 0.95rem !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: var(--ttt-track-label);
}}
p, li, label, span, div {{ color: var(--ttt-text); }}
p, li {{ line-height: 1.6; font-size: var(--ttt-t-body); }}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.4rem; padding-bottom: 4rem; max-width: 1060px; }}
::selection {{
  background: color-mix(in srgb, var(--ttt-primary) 30%, transparent);
  color: var(--ttt-text);
}}
:focus-visible {{ outline: 2px solid var(--ttt-accent); outline-offset: 2px; }}

/* ---------- sidebar: a table of contents, not a control panel ---------- */
[data-testid="stSidebar"] {{
  background: var(--ttt-surface);
  border-right: var(--ttt-hair);
}}
[data-testid="stSidebar"] .stRadio > div {{ gap: 0; }}
[data-testid="stSidebar"] .stRadio label {{
  padding: 0.42rem 0.6rem;
  border-left: 2px solid transparent;
  transition: background 100ms linear, border-color 100ms linear;
}}
[data-testid="stSidebar"] .stRadio label:hover {{ background: var(--ttt-tint); }}
[data-testid="stSidebar"] .stRadio label:has(input:checked) {{
  border-left-color: var(--ttt-primary);
  background: var(--ttt-tint);
}}
[data-testid="stSidebar"] .stRadio label p {{
  font-size: var(--ttt-t-meta);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}}
[data-testid="stSidebar"] .stRadio label:has(input:checked) p {{
  font-weight: 700;
  color: var(--ttt-primary);
}}

/* ---------- buttons: labelled keys ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
  background: var(--ttt-surface-alt);
  color: var(--ttt-text);
  border: var(--ttt-hair);
  border-radius: var(--ttt-radius);
  box-shadow: none;
  font-family: var(--ttt-body-font);
  font-size: var(--ttt-t-meta);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  padding: 0.5rem 0.95rem;
  transition: background 100ms linear, color 100ms linear, border-color 100ms linear;
}}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
  background: {hover_bg};
  color: {hover_ink};
  border-color: var(--ttt-primary);
}}
.stButton > button:active {{ transform: translateY(1px); }}
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible {{
  outline: 2px solid var(--ttt-accent);
  outline-offset: 2px;
}}
.stButton > button[kind="primary"] {{
  background: var(--ttt-primary);
  color: var(--ttt-bg);
  border-color: var(--ttt-primary);
  font-weight: 700;
}}
.stButton > button[kind="primary"]:hover {{
  background: color-mix(in srgb, var(--ttt-primary) 82%, var(--ttt-bg));
  border-color: color-mix(in srgb, var(--ttt-primary) 82%, var(--ttt-bg));
}}
.stButton > button:disabled {{
  color: var(--ttt-text-muted);
  border-color: var(--ttt-rule-soft);
  background: transparent;
}}

/* ---------- inputs ---------- */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
textarea {{
  background: var(--ttt-surface-alt) !important;
  color: var(--ttt-text) !important;
  border: var(--ttt-hair) !important;
  border-radius: var(--ttt-radius) !important;
  font-family: var(--ttt-body-font) !important;
  font-size: var(--ttt-t-body) !important;
}}
[data-testid="stTextInput"] input:focus, textarea:focus {{
  border-color: var(--ttt-primary) !important;
  box-shadow: var(--ttt-glow);
}}
[data-baseweb="select"] > div {{
  background: var(--ttt-surface-alt) !important;
  border-color: var(--ttt-rule) !important;
  border-radius: var(--ttt-radius) !important;
  font-size: var(--ttt-t-body);
}}
[data-testid="stMetric"] {{
  background: transparent;
  border: none;
  border-top: 2px solid var(--ttt-ink);
  border-radius: 0;
  padding: 0.6rem 0 0.4rem;
}}
[data-testid="stMetricLabel"] p {{
  font-size: var(--ttt-t-label) !important;
  text-transform: uppercase;
  letter-spacing: var(--ttt-track-label);
  color: var(--ttt-text-muted) !important;
}}
[data-testid="stMetricValue"] {{
  font-family: var(--ttt-body-font);
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  color: var(--ttt-text);
}}
[data-testid="stTabs"] button[role="tab"] {{
  font-family: var(--ttt-body-font);
  font-size: var(--ttt-t-meta);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ttt-text-muted);
}}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {{
  color: var(--ttt-text);
  border-bottom-color: var(--ttt-primary);
}}
hr {{ border: none; border-top: var(--ttt-hair); margin: var(--ttt-s4) 0; }}

/* ---------- surfaces ---------- */
.ttt-card {{
  background: var(--ttt-surface);
  border: var(--ttt-hair);
  border-radius: var(--ttt-radius);
  padding: var(--ttt-s3) 1.1rem;
}}
.ttt-eyebrow {{
  font-size: var(--ttt-t-label);
  letter-spacing: var(--ttt-track-label);
  text-transform: uppercase;
  color: var(--ttt-text-muted);
  margin-bottom: var(--ttt-s2);
  padding-bottom: var(--ttt-s1);
  border-bottom: var(--ttt-hair-soft);
}}
.ttt-muted {{ color: var(--ttt-text-muted); font-size: var(--ttt-t-meta); }}
.ttt-mono {{ font-variant-numeric: tabular-nums; }}

/* ---------- hero ---------- */
.ttt-hero {{
  display: flex;
  flex-wrap: wrap;
  gap: var(--ttt-s5);
  align-items: center;
  justify-content: space-between;
  padding: var(--ttt-s5) 0 var(--ttt-s4);
  border-top: 3px solid var(--ttt-ink);
  border-bottom: var(--ttt-hair);
}}
.ttt-hero h1 {{
  font-size: clamp(2.4rem, 8vw, 4rem);
  line-height: 1;
  margin: var(--ttt-s3) 0 var(--ttt-s2);
  letter-spacing: -0.03em;
  font-weight: 700;
}}
.ttt-hero .ttt-lede {{
  max-width: 42ch;
  color: var(--ttt-text-muted);
  font-size: 0.95rem;
  line-height: 1.65;
}}
.ttt-hero-copy {{ flex: 1 1 320px; }}
.ttt-hero-art {{ flex: 0 1 220px; display: flex; justify-content: center; }}
.ttt-kicker {{
  display: inline-block;
  font-size: var(--ttt-t-label);
  letter-spacing: var(--ttt-track-label);
  text-transform: uppercase;
  color: var(--ttt-text-muted);
}}
.ttt-featurerow {{
  display: flex; flex-wrap: wrap;
  gap: var(--ttt-s2) var(--ttt-s3);
  margin-top: var(--ttt-s4);
}}
.ttt-chip {{
  font-size: var(--ttt-t-meta);
  color: var(--ttt-text-muted);
  padding-left: 0.85rem;
  position: relative;
}}
.ttt-chip::before {{
  content: "+";
  position: absolute; left: 0;
  color: var(--ttt-primary);
}}

/* ---------- player strip ---------- */
.ttt-seats {{
  display: flex; flex-wrap: wrap;
  border: var(--ttt-hair);
  border-radius: var(--ttt-radius);
  overflow: hidden;
}}
.ttt-seat {{
  flex: 1 1 200px;
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 0.7rem 0.85rem;
  background: var(--ttt-surface);
  border-right: var(--ttt-hair-soft);
  border-left: 3px solid transparent;
  transition: border-color 120ms linear, background 120ms linear;
}}
.ttt-seat:last-child {{ border-right: none; }}
/* Whose turn it is. One rule moves; nothing pulses. */
.ttt-seat.active {{
  border-left-color: var(--ttt-primary);
  background: var(--ttt-surface-alt);
}}
.ttt-seat .avatar {{ font-size: 1.25rem; line-height: 1; }}
.ttt-seat .who {{ display: flex; flex-direction: column; line-height: 1.35; min-width: 0; }}
.ttt-seat .who strong {{
  font-size: 0.9rem; font-weight: 700;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}
.ttt-seat .who span {{
  font-size: var(--ttt-t-label); color: var(--ttt-text-muted);
  letter-spacing: 0.1em; text-transform: uppercase;
}}
.ttt-seat .mark {{
  margin-left: auto;
  font-size: 1.1rem; font-weight: 700;
  width: 1.9rem; height: 1.9rem;
  display: grid; place-items: center;
  border: var(--ttt-hair);
}}
.ttt-seat .mark.x {{ color: var(--ttt-x-color); border-color: var(--ttt-x-color); }}
.ttt-seat .mark.o {{ color: var(--ttt-o-color); border-color: var(--ttt-o-color); }}
.ttt-dot {{
  width: 6px; height: 6px; display: inline-block;
  margin-right: 0.4rem; vertical-align: middle;
}}
.ttt-dot.on {{ background: var(--ttt-success); }}
.ttt-dot.off {{ background: transparent; box-shadow: inset 0 0 0 1px var(--ttt-text-muted); }}

/* ---------- status ---------- */
.ttt-status {{
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: var(--ttt-t-body);
  padding: 0.55rem 0;
  border-top: var(--ttt-hair);
  border-bottom: var(--ttt-hair);
}}
.ttt-status .glyph {{ color: var(--ttt-primary); font-weight: 700; }}
.ttt-banner {{
  text-align: center;
  padding: var(--ttt-s4) var(--ttt-s3);
  border: 2px solid var(--ttt-ink);
  border-radius: var(--ttt-radius);
  background: var(--ttt-surface);
  box-shadow: var(--ttt-lift);
  animation: ttt-stamp 220ms steps(4, end);
}}
.ttt-banner .ttt-eyebrow {{ border: none; margin-bottom: var(--ttt-s1); }}
.ttt-banner h2 {{
  margin: 0 0 var(--ttt-s2);
  font-size: 1.7rem;
  letter-spacing: -0.02em;
}}
/* The result inverts, matching the winning line on the board. */
.ttt-banner.win {{ background: var(--ttt-win); border-color: var(--ttt-win); }}
.ttt-banner.win h2, .ttt-banner.win .ttt-eyebrow, .ttt-banner.win p {{
  color: var(--ttt-bg) !important;
}}
.ttt-banner.loss {{ border-color: var(--ttt-danger); }}
.ttt-banner.loss h2 {{ color: var(--ttt-danger) !important; }}
.ttt-banner.draw {{ border-style: dashed; }}
.ttt-banner.draw h2 {{ color: var(--ttt-text-muted) !important; }}

.ttt-thinking {{
  display: inline-flex;
  align-items: baseline;
  gap: 0.1rem;
  font-size: var(--ttt-t-meta);
  color: var(--ttt-text-muted);
  letter-spacing: 0.1em;
  text-transform: uppercase;
}}
.ttt-thinking .tick {{
  width: 0.55ch;
  color: var(--ttt-primary);
  animation: ttt-blink 900ms steps(1, end) infinite;
}}
.ttt-thinking .tick:nth-child(2) {{ animation-delay: 300ms; }}
.ttt-thinking .tick:nth-child(3) {{ animation-delay: 600ms; }}
.ttt-thinking .label {{ margin-right: 0.4rem; }}

/* ---------- lists ---------- */
.ttt-log {{
  font-size: var(--ttt-t-meta);
  font-variant-numeric: tabular-nums;
  max-height: 260px; overflow-y: auto;
}}
.ttt-log-row {{
  display: flex; gap: 0.75rem; align-items: baseline;
  padding: 0.3rem 0;
  border-bottom: var(--ttt-hair-soft);
}}
.ttt-log-row:last-child {{ border-bottom: none; }}
.ttt-log-row .n {{ color: var(--ttt-text-muted); width: 2.2ch; text-align: right; }}
.ttt-log-row .m {{ font-weight: 700; width: 1.2ch; }}
.ttt-log-row .m.x {{ color: var(--ttt-x-color); }}
.ttt-log-row .m.o {{ color: var(--ttt-o-color); }}
.ttt-log-row .at {{ letter-spacing: 0.05em; }}
.ttt-chat {{ display: flex; flex-direction: column; max-height: 240px; overflow-y: auto; }}
.ttt-bubble {{
  padding: 0.35rem 0 0.35rem 0.75rem;
  border-left: 2px solid var(--ttt-rule);
  font-size: var(--ttt-t-body);
}}
.ttt-bubble.mine {{ border-left-color: var(--ttt-primary); }}
.ttt-bubble .who {{
  font-size: var(--ttt-t-label);
  color: var(--ttt-text-muted);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-right: 0.5rem;
}}
.ttt-badge {{
  display: flex; flex-direction: column; gap: var(--ttt-s1);
  padding: 0.85rem;
  border: var(--ttt-hair);
  border-radius: var(--ttt-radius);
  background: var(--ttt-surface);
  min-height: 108px;
}}
.ttt-badge.locked {{
  border-style: dashed;
  border-color: var(--ttt-rule-soft);
  background: transparent;
}}
.ttt-badge.locked .icon {{ filter: grayscale(1); opacity: 0.35; }}
.ttt-badge.locked .name {{ color: var(--ttt-text-muted); }}
.ttt-badge .icon {{ font-size: 1.35rem; line-height: 1; }}
.ttt-badge .name {{
  font-weight: 700; font-size: var(--ttt-t-meta);
  text-transform: uppercase; letter-spacing: 0.06em;
}}
.ttt-badge .desc {{ font-size: var(--ttt-t-label); color: var(--ttt-text-muted); line-height: 1.5; }}

/* Room code: one boxed character each, because it gets read out loud. */
.ttt-code {{ display: inline-flex; gap: 0.35rem; }}
.ttt-code b {{
  font-size: 1.7rem;
  font-weight: 700;
  line-height: 1;
  padding: 0.55rem 0.6rem;
  border: var(--ttt-hair);
  border-bottom: 2px solid var(--ttt-ink);
  border-radius: var(--ttt-radius);
  background: var(--ttt-surface);
  font-variant-numeric: tabular-nums;
}}

/* ---------- board ---------- */
/* Columns collapse to a single drawn grid: no gaps, hairlines shared. */
[data-testid="stHorizontalBlock"]:has([class*="st-key-cell-"]) {{
  gap: 0 !important;
  max-width: 460px;
  margin-inline: auto;
}}
[data-testid="stHorizontalBlock"]:has([class*="st-key-cell-"]) [data-testid="stColumn"],
[data-testid="stHorizontalBlock"]:has([class*="st-key-cell-"]) [data-testid="column"] {{
  padding: 0 !important;
  min-width: 0;
}}
[class*="st-key-cell-"] button {{
  position: relative;
  width: 100%;
  aspect-ratio: 1 / 1;
  min-height: 0;
  height: auto;
  margin: 0 -1px -1px 0;
  padding: 0;
  border: 1px solid var(--ttt-grid-line);
  border-radius: 0;
  background: var(--ttt-surface);
  font-family: var(--ttt-body-font) !important;
  font-size: clamp(1.5rem, 5.5vw, 2.6rem) !important;
  font-weight: 700;
  color: var(--ttt-text-muted);
  cursor: crosshair;
  transition: background 90ms linear, color 90ms linear;
}}
[class*="st-key-cell-"] button:hover:not([disabled]) {{
  background: var(--ttt-tint);
  color: var(--ttt-primary);
}}
[class*="st-key-cell-"] button:focus-visible {{
  outline: 2px solid var(--ttt-accent);
  outline-offset: -3px;
  z-index: 3;
}}
[class*="st-key-cell-"] button[disabled] {{
  opacity: 1;
  cursor: default;
  color: var(--ttt-text-muted);
}}
[class*="st-key-cell-"] button p {{ font-size: inherit !important; font-weight: inherit; }}
/* ::before is the coordinate, ::after is the move number. Both are set
   per-cell by board_css(); these rules only place them. */
[class*="st-key-cell-"] button::before {{
  position: absolute; top: 5px; left: 6px;
  font-size: 0.6rem; font-weight: 400;
  letter-spacing: 0.06em;
  color: var(--ttt-text-muted);
  opacity: 0.5;
}}
[class*="st-key-cell-"] button:hover:not([disabled])::before {{ opacity: 1; }}
[class*="st-key-cell-"] button[disabled]::before {{ opacity: 0.22; }}
[class*="st-key-cell-"] button::after {{
  position: absolute; bottom: 4px; right: 6px;
  font-size: 0.58rem; font-weight: 500;
  font-variant-numeric: tabular-nums;
  color: currentColor;
  opacity: 0.55;
}}

/* ---------- keyframes ---------- */
/* Stepped rather than eased: things here are printed, not animated. */
@keyframes ttt-stamp {{
  0% {{ opacity: 0; }}
  100% {{ opacity: 1; }}
}}
@keyframes ttt-set {{
  0% {{ opacity: 0; transform: translateY(-2px); }}
  100% {{ opacity: 1; transform: none; }}
}}
@keyframes ttt-blink {{
  0%, 49% {{ opacity: 1; }}
  50%, 100% {{ opacity: 0.15; }}
}}
@keyframes ttt-draw {{
  from {{ stroke-dashoffset: 200; }}
  to {{ stroke-dashoffset: 0; }}
}}

/* ---------- responsive ---------- */
@media (max-width: 720px) {{
  .block-container {{ padding: 1.4rem 0.85rem 3rem; }}
  .ttt-hero {{ padding-top: var(--ttt-s4); gap: var(--ttt-s3); }}
  .ttt-hero-art {{ order: -1; flex-basis: 150px; }}
  .ttt-seat {{ flex-basis: 100%; border-right: none; border-bottom: var(--ttt-hair-soft); }}
  [class*="st-key-cell-"] button {{ font-size: clamp(1.3rem, 12vw, 2.1rem) !important; }}
  .ttt-code b {{ font-size: 1.3rem; padding: 0.4rem 0.45rem; }}
}}
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{
    animation-duration: 0.001ms !important;
    transition-duration: 0.001ms !important;
  }}
}}
{motion}
</style>
"""


def _texture(theme: Theme) -> str:
    """The paper stock, as a comma-terminated list of background layers.

    Each theme gets the ruling that belongs to its stock, or none at all.  Only
    repeating gradients are used, so there is no image request and no
    `background-attachment: fixed` repaint on scroll.
    """
    if theme.key is ThemeName.LIGHT:  # graph paper: 5mm grid, heavier every 5th
        fine = f"color-mix(in srgb, {theme.grid_line} 34%, transparent)"
        major = f"color-mix(in srgb, {theme.grid_line} 62%, transparent)"
        return (
            f"repeating-linear-gradient(0deg, transparent 0 23px, {fine} 23px 24px),"
            f"repeating-linear-gradient(90deg, transparent 0 23px, {fine} 23px 24px),"
            f"repeating-linear-gradient(0deg, transparent 0 119px, {major} 119px 120px),"
            f"repeating-linear-gradient(90deg, transparent 0 119px, {major} 119px 120px),"
        )
    if theme.key is ThemeName.RETRO:  # fanfold: alternating bands
        band = f"color-mix(in srgb, {theme.grid_line} 22%, transparent)"
        return f"repeating-linear-gradient(0deg, {band} 0 44px, transparent 44px 88px),"
    if theme.key in (ThemeName.DARK, ThemeName.CYBERPUNK):  # drafting grid
        # Barely there: on dark stock a visible grid reads as noise, and it is
        # competing with the board, which is the only grid that matters.
        line = f"color-mix(in srgb, {theme.grid_line} 22%, transparent)"
        return (
            f"repeating-linear-gradient(0deg, transparent 0 39px, {line} 39px 40px),"
            f"repeating-linear-gradient(90deg, transparent 0 39px, {line} 39px 40px),"
        )
    return ""  # Duotone and Plain are flat stock.


def _motion_off() -> str:
    """CSS that disables decorative animation when the player turns it off."""
    return """
*, *::before, *::after { animation: none !important; transition: none !important; }
"""


# --------------------------------------------------------------------------- #
# Board
# --------------------------------------------------------------------------- #
def _board_size(board: Board) -> int:
    """Side length of the board, however the Board class exposes it."""
    size = getattr(board, "size", None)
    if isinstance(size, int) and size > 0:
        return size
    return max(1, round(len(board.cells) ** 0.5))


@_markup
def board_css(
    board: Board,
    winning_line: Sequence[int] | None = None,
    animations: bool = True,
    last_move: int | None = None,
    move_numbers: Mapping[int, int] | None = None,
) -> str:
    """Per-cell styling driven by widget keys (``st-key-cell-<index>``).

    Empty cells print their coordinate so players can name a square out loud.
    Played cells take their mark's colour, and carry their move number in the
    corner when ``move_numbers`` is supplied — pass ``{cell_index: ordinal}``
    from the move history to turn the board into a readable record of the game.
    Winning cells invert to solid ink, stamped in the order of the line.
    """
    size = _board_size(board)
    rules: list[str] = []
    winners = list(winning_line or ())
    winner_set = set(winners)

    for index, cell in enumerate(board.cells):
        selector = f'[class*="st-key-cell-{index}"] button'

        if cell == EMPTY:
            rules.append(f'{selector}::before {{ content: "{cell_name(index, size)}"; }}')
            continue

        if index in winner_set:
            continue  # the winning pass below owns these cells entirely

        colour = "var(--ttt-x-color)" if cell == Mark.X.value else "var(--ttt-o-color)"
        declarations = [
            f"color: {colour} !important",
            f"border-color: color-mix(in srgb, {colour} 40%, var(--ttt-grid-line))",
            f"background: color-mix(in srgb, {colour} 7%, var(--ttt-surface))",
        ]
        if animations and index == last_move:
            declarations.append("animation: ttt-set 160ms steps(3, end)")
        rules.append(f"{selector} {{ {'; '.join(declarations)}; }}")

        ordinal = (move_numbers or {}).get(index)
        if ordinal is not None:
            rules.append(f'{selector}::after {{ content: "{int(ordinal)}"; }}')

    for order, index in enumerate(winners):
        selector = f'[class*="st-key-cell-{index}"] button'
        declarations = [
            "color: var(--ttt-bg) !important",
            "background: var(--ttt-win)",
            "border-color: var(--ttt-win)",
            "position: relative",
            "z-index: 2",
        ]
        if animations:
            # Stagger along the line: it reads as the line being struck through.
            declarations.append(
                f"animation: ttt-stamp 140ms steps(2, end) {order * 90}ms both"
            )
        rules.append(f"{selector} {{ {'; '.join(declarations)}; }}")

        ordinal = (move_numbers or {}).get(index)
        if ordinal is not None:
            rules.append(f'{selector}::after {{ content: "{int(ordinal)}"; }}')

    return "<style>\n" + "\n".join(rules) + "\n</style>"


# --------------------------------------------------------------------------- #
# HTML fragments
# --------------------------------------------------------------------------- #
@_markup
def logo_svg(size: int = 200, animated: bool = True) -> str:
    """The mark: a game being written down, then struck through.

    Drawn in hairlines at the same weight as the rules elsewhere on the page,
    so the logo reads as part of the document rather than a badge stuck on it.
    """
    anim = "" if not animated else """
    <style>
      .lg-mark { opacity: 0; animation: lg-in 6s steps(1, end) infinite; }
      .lg-mark:nth-of-type(1) { animation-delay: 0s; }
      .lg-mark:nth-of-type(2) { animation-delay: 0.5s; }
      .lg-mark:nth-of-type(3) { animation-delay: 1s; }
      .lg-mark:nth-of-type(4) { animation-delay: 1.5s; }
      .lg-mark:nth-of-type(5) { animation-delay: 2s; }
      .lg-line { stroke-dasharray: 200; stroke-dashoffset: 200;
                 animation: lg-strike 6s linear infinite; }
      @keyframes lg-in { 0%, 5% { opacity: 0; } 6%, 88% { opacity: 1; } 89%, 100% { opacity: 0; } }
      @keyframes lg-strike { 0%, 42% { stroke-dashoffset: 200; opacity: 0; }
                             43% { opacity: 1; }
                             56%, 88% { stroke-dashoffset: 0; opacity: 1; }
                             89%, 100% { opacity: 0; } }
      @media (prefers-reduced-motion: reduce) {
        .lg-mark { opacity: 1; animation: none; }
        .lg-line { stroke-dashoffset: 0; animation: none; }
      }
    </style>"""
    return f"""
<svg viewBox="0 0 200 200" width="{size}" height="{size}" role="img"
     aria-label="A tic-tac-toe grid resolving into a winning diagonal"
     xmlns="http://www.w3.org/2000/svg">
  {anim}
  <g stroke="var(--ttt-grid-line)" stroke-width="1.5" shape-rendering="crispEdges">
    <rect x="12" y="12" width="176" height="176" fill="none"/>
    <line x1="70.7" y1="12" x2="70.7" y2="188"/>
    <line x1="129.3" y1="12" x2="129.3" y2="188"/>
    <line x1="12" y1="70.7" x2="188" y2="70.7"/>
    <line x1="12" y1="129.3" x2="188" y2="129.3"/>
  </g>
  <g fill="none" stroke-width="7" stroke-linecap="square">
    <g class="lg-mark" stroke="var(--ttt-x-color)" transform="translate(41.3,41.3)">
      <line x1="-13" y1="-13" x2="13" y2="13"/><line x1="13" y1="-13" x2="-13" y2="13"/>
    </g>
    <g class="lg-mark" stroke="var(--ttt-o-color)" transform="translate(100,41.3)">
      <circle cx="0" cy="0" r="14"/>
    </g>
    <g class="lg-mark" stroke="var(--ttt-x-color)" transform="translate(100,100)">
      <line x1="-13" y1="-13" x2="13" y2="13"/><line x1="13" y1="-13" x2="-13" y2="13"/>
    </g>
    <g class="lg-mark" stroke="var(--ttt-o-color)" transform="translate(158.7,100)">
      <circle cx="0" cy="0" r="14"/>
    </g>
    <g class="lg-mark" stroke="var(--ttt-x-color)" transform="translate(158.7,158.7)">
      <line x1="-13" y1="-13" x2="13" y2="13"/><line x1="13" y1="-13" x2="-13" y2="13"/>
    </g>
  </g>
  <line class="lg-line" x1="26" y1="26" x2="174" y2="174"
        stroke="var(--ttt-win)" stroke-width="4" stroke-linecap="square"/>
</svg>
"""


@_markup
def hero(subtitle: str, chips: Iterable[str], animated: bool = True) -> str:
    """Landing hero: the logo is the thesis — a game being written down."""
    chip_html = "".join(f'<span class="ttt-chip">{escape(chip)}</span>' for chip in chips)
    return f"""
<div class="ttt-hero">
  <div class="ttt-hero-copy">
    <span class="ttt-kicker">3×3 / 4×4 / 5×5 — two players, one grid</span>
    <h1>GRIDLOCK</h1>
    <p class="ttt-lede">{escape(subtitle)}</p>
    <div class="ttt-featurerow">{chip_html}</div>
  </div>
  <div class="ttt-hero-art">{logo_svg(200, animated)}</div>
</div>
"""


@_markup
def seat(
    name: str,
    avatar: str,
    mark: Mark,
    role_label: str,
    active: bool = False,
    online: bool | None = None,
) -> str:
    """A player card for the board header."""
    presence = ""
    if online is not None:
        presence = f'<span class="ttt-dot {"on" if online else "off"}"></span>'
    return f"""
<div class="ttt-seat {'active' if active else ''}">
  <span class="avatar">{escape(avatar)}</span>
  <span class="who"><strong>{escape(name)}</strong><span>{presence}{escape(role_label)}</span></span>
  <span class="mark {mark.value.lower()}">{mark.value}</span>
</div>
"""


@_markup
def seats_row(cards: Iterable[str]) -> str:
    """Wrap seat cards in a flex row."""
    return f'<div class="ttt-seats">{"".join(cards)}</div>'


@_markup
def status_line(text: str, icon: str = ">") -> str:
    """Single-line status strip."""
    return (
        f'<div class="ttt-status"><span class="glyph">{escape(icon)}</span>'
        f"<span>{escape(text)}</span></div>"
    )


@_markup
def thinking(label: str = "Thinking") -> str:
    """A cursor blinking while the engine searches."""
    ticks = '<span class="tick">.</span>' * 3
    return (
        f'<div class="ttt-thinking"><span class="label">{escape(label)}</span>{ticks}</div>'
    )


@_markup
def banner(title: str, subtitle: str, tone: str = "win") -> str:
    """Result banner (tone: ``win`` / ``loss`` / ``draw``)."""
    return f"""
<div class="ttt-banner {tone}">
  <div class="ttt-eyebrow">{escape(tone.upper())}</div>
  <h2>{escape(title)}</h2>
  <p class="ttt-muted">{escape(subtitle)}</p>
</div>
"""


@_markup
def card(body: str) -> str:
    """Wrap arbitrary HTML in the standard surface."""
    return f'<div class="ttt-card">{body}</div>'


@_markup
def eyebrow(text: str) -> str:
    """Small uppercase label."""
    return f'<div class="ttt-eyebrow">{escape(text)}</div>'


@_markup
def room_code(code: str) -> str:
    """Room code, one boxed character each, so it is easy to read out loud."""
    keys = "".join(f"<b>{escape(character)}</b>" for character in code)
    return f'<div class="ttt-code">{keys}</div>'


@_markup
def move_log(rows: Sequence[dict], limit: int = 30, size: int | None = None) -> str:
    """Render the move history.

    Pass ``size`` (the board's side length) to log squares in coordinate
    notation — ``b2`` rather than ``4`` — which matches what the board shows.
    """
    if not rows:
        return '<p class="ttt-muted">Moves are listed here as they are played.</p>'
    items = []
    for row in rows[-limit:]:
        mark = str(row.get("mark", "")).lower()
        cell = row.get("cell", row.get("cell_index", ""))
        where = cell_name(int(cell), size) if size and str(cell).isdigit() else str(cell)
        items.append(
            f'<div class="ttt-log-row">'
            f'<span class="n">{row.get("number", row.get("move_number", ""))}</span>'
            f'<span class="m {mark}">{escape(str(row.get("mark", "")))}</span>'
            f'<span class="at">{escape(where)}</span>'
            f'<span class="ttt-muted" style="margin-left:auto">'
            f'{escape(str(row.get("player_name", "")))}</span></div>'
        )
    return f'<div class="ttt-log">{"".join(items)}</div>'


@_markup
def chat_log(messages: Sequence[dict], my_id: str) -> str:
    """Render chat + reactions."""
    if not messages:
        return '<p class="ttt-muted">Say hello. Messages clear when the room closes.</p>'
    bubbles = []
    for message in messages:
        mine = message.get("player_id") == my_id
        body = escape(str(message.get("body", "")))
        if message.get("kind") == "reaction":
            body = f'<span style="font-size:1.3rem">{body}</span>'
        bubbles.append(
            f'<div class="ttt-bubble {"mine" if mine else ""}">'
            f'<span class="who">{escape(str(message.get("name", "Player")))}</span>{body}</div>'
        )
    return f'<div class="ttt-chat">{"".join(bubbles)}</div>'


@_markup
def badge(icon: str, name: str, description: str, unlocked: bool) -> str:
    """Achievement tile."""
    return f"""
<div class="ttt-badge {'' if unlocked else 'locked'}">
  <span class="icon">{escape(icon)}</span>
  <span class="name">{escape(name)}</span>
  <span class="desc">{escape(description)}</span>
</div>
"""


@_markup
def theme_swatch(name: ThemeName) -> str:
    """Small colour preview used in the settings page."""
    theme = get_theme(name)
    dots = "".join(
        f'<span style="width:16px;height:16px;background:{colour};'
        f'display:inline-block;margin-right:3px"></span>'
        for colour in (theme.primary, theme.secondary, theme.accent, theme.x_color, theme.o_color)
    )
    return (
        f'<div style="display:flex;align-items:center;gap:10px">'
        f'<span style="display:inline-flex;border:1px solid var(--ttt-rule);padding:2px 0 2px 3px">'
        f"{dots}</span>"
        f'<span class="ttt-muted">{escape(theme.label)}</span></div>'
    )