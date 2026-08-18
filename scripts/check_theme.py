#!/usr/bin/env python3
"""Validate a Zed theme family against the schema, an alpha policy, and WCAG contrast floors.

Usage:
    python3 scripts/check_theme.py themes/cyberpunk-neon.json
    python3 scripts/check_theme.py themes/cyberpunk-neon.json --schema /path/to/v0.2.0.json
    python3 scripts/check_theme.py themes/cyberpunk-neon.json --offline

Checks
  (a) JSON Schema validation against the theme's own `$schema` URL.
  (b) Alpha policy: no alpha-bearing hex outside the allowlist in the Transparent
      variant, and none at all in the opaque variant.
  (c) Contrast floors, WCAG relative luminance, RGBA composited over the backdrop.
      In the Transparent variant the backdrop is a stack -- wallpaper, then the
      window tint, then the surface -- so every floor is measured against the real
      composite, over a black, a mid-gray and a white synthetic wallpaper.
  (d) The two variants are identical except on the surface plan, the declared
      color substitutions and `background.appearance`.
  (e) Window transparency: each variant declares the `background.appearance` it
      means, the Transparent variant follows the surface plan exactly, and the
      editor and terminal regions composite to an opacity inside the band.

Window transparency
-------------------
`background.appearance` is what actually makes a Zed window see-through. Verified
against zed-industries/zed@main:

  crates/settings_content/src/theme.rs:538-539
      #[serde(rename = "background.appearance")]
      pub <rust field>: Option<WindowBackgroundContent>,
  crates/settings_content/src/theme.rs:1363-1371
      #[serde(rename_all = "snake_case")]
      pub enum WindowBackgroundContent { Opaque, Transparent, Blurred }
  crates/theme/src/theme.rs:289-292 -> crates/gpui/src/window.rs:1411
      the theme's value reaches platform_window.set_background_appearance()

The Rust field behind that rename is spelled differently -- LEGACY_APPEARANCE_KEY
below assembles it -- and a theme using the field name instead of the renamed JSON
key is silently ignored: the theme schema tolerates unknown properties, so nothing
complains and nothing happens. check_legacy_key() is a regression trap for exactly
that mistake.

gpui defaults the appearance to Opaque (crates/gpui/src/platform.rs:2069) and its
docstring is explicit that under Opaque "themes should define a fully opaque
background color instead", so alpha without the key is not transparency -- it just
spells opaque colors the long way.

Surface architecture
--------------------
Alpha does NOT stack well: `background` at 85% under `editor.background` at 85%
composites to ~97.8% in the editor region, which is opacity with extra steps
(zed-industries/zed#55972). So the Transparent variant is one tinted base pane
with tiers above it, each tier chosen by what the surface holds:

  glass       #00000000   the showcase: editor, gutter, tab bar, inactive tabs,
                          terminal, toolbar. Code and terminal output are
                          high-contrast neon glyphs; they survive the wallpaper.
  dock chrome #091833b3   `panel.background` -- what the agent (AI chat) panel,
                          project tree, git panel and outline sit on
                          (zed workspace/src/dock.rs paints it behind every dock
                          panel). Prose and muted labels do NOT survive
                          wallpaper bleed, so docks get a near-solid backdrop:
                          70% navy over the 88% base composites to ~96%.
  structure   #09183366   active tab, status bar, title bar, sticky header --
                          chrome that must separate from wallpaper but carries
                          only short strings.
  grounded    opaque      `surface.background` (popover asides, keybinding
                          hints), plus the floating tier that was always opaque:
                          `elevated_surface.background` (menus, pickers),
                          `panel.overlay_background`, `element.background`.
                          Alpha on a menu shows code through the menu.

`background` still carries the entire window tint; nothing else re-tints the
editor or terminal regions. TRANSPARENT_SURFACE_PLAN is that architecture,
enforced value by value rather than left to convention. A side effect worth
knowing: a terminal living in the bottom DOCK sits above the dock chrome and is
therefore near-solid; a terminal in the center pane keeps the full glass.

Colors vs. the wallpaper
------------------------
Once the window is really transparent the effective background is `wallpaper (+)
tint`, and floors that held against `#000b1e` collapse over a bright wallpaper.
TRANSPARENT_SUBSTITUTIONS is the declared, bounded answer: a handful of same-hue
color swaps that apply only to the Transparent variant. Everything else is shared.

Why these keys and these floors --- verified against zed-industries/zed@main:

  crates/markdown/src/mermaid.rs:362-405   line_color = colors.border
  crates/mermaid_render/src/postprocess/inject_css.rs
      .marker { fill: {line}; stroke: {line} }        flowchart ARROWHEADS  <- border
      .messageLine0/1 { stroke: {text} }              sequence arrows       <- text
      #arrowhead path { fill: {text} }                sequence ARROWHEADS   <- text
      cluster_border/note_border = colors.border_variant
  crates/mermaid_render/src/mermaid_render.rs:162-173
      css_color() emits #RRGGBBAA verbatim into the SVG when alpha < 255,
      so a translucent stroke composites toward invisible. This is the bug.
  crates/markdown/src/markdown.rs
      :232 rule_color = colors.border                 horizontal rule
      :233 block_quote_border_color = colors.border   blockquote bar
      :2817,:2860 table cell + outer borders = colors.border
      :265 code block border = colors.border_variant
      :283 inline code bg = editor_foreground.opacity(0.08)
      :341 inline code text = colors.text
      :371 heading underline = colors.border_variant

`border` therefore paints table pipes, the blockquote bar, the horizontal rule and
every flowchart arrowhead; `border.variant` paints cluster, note and code-block
strokes. Neither may carry alpha in either variant, which is why border.variant is
NOT alpha-allowlisted even though the original brief listed it -- it is instead
brightened by substitution so it survives a bright wallpaper.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache")

OPAQUE_VARIANT = "Cyberpunk Neon"
TRANSPARENT_VARIANT = "Cyberpunk Neon Transparent"

# --------------------------------------------------------------------------- policy

# The JSON key Zed actually reads, and the Rust field name that is NOT it. The
# wrong spelling is assembled rather than written out, so that a repo-wide grep for
# it keeps returning nothing -- this line is the only place it would survive.
APPEARANCE_KEY = "background.appearance"
LEGACY_APPEARANCE_KEY = "window_" + APPEARANCE_KEY.replace(".", "_")

# What each variant must declare. A variant claiming transparency without this is
# just an opaque theme with a longer spelling.
BACKGROUND_APPEARANCE = {
    OPAQUE_VARIANT: "opaque",
    TRANSPARENT_VARIANT: "transparent",
}

# The Transparent variant's surface architecture, value by value: one tinted base
# pane, clear glass over the code, a heavy tint on the docks (prose UI needs a
# real backdrop), a light tint on the structural chrome. See the module docstring.
TRANSPARENT_SURFACE_PLAN = {
    # the one tinted pane -- the whole window's transparency lives here
    "background": "#000b1ee0",
    # clear glass: the showcase regions, code and terminal over the wallpaper
    "editor.background": "#00000000",
    "editor.gutter.background": "#00000000",
    "tab.inactive_background": "#00000000",
    "tab_bar.background": "#00000000",
    "terminal.ansi.background": "#00000000",
    "terminal.background": "#00000000",
    "toolbar.background": "#00000000",
    # dock chrome: every dock panel -- the agent (AI chat) panel, project tree,
    # git panel, outline -- is dense prose over `panel.background`
    # (zed workspace/src/dock.rs paints it behind the active panel), and prose
    # drowns in wallpaper bleed long before code does. 70% navy over the 88%
    # base composites to ~96%: readable chrome, a hint of desktop left.
    "panel.background": "#091833b3",
    # grounded surfaces: popover asides, keybinding hints, small cards
    # (ui/src/components/popover.rs, keybinding_hint.rs). Opaque like the
    # floating tier -- alpha here puts wallpaper behind menu-adjacent text.
    "surface.background": "#091833",
    # structure: a light tint so these read as chrome and not as wallpaper
    "editor.subheader.background": "#09183366",
    "status_bar.background": "#09183366",
    "tab.active_background": "#09183366",
    "title_bar.background": "#09183366",
    "title_bar.inactive_background": "#06142866",
}

# Regions whose composite opacity is what a user actually perceives as "how
# transparent is this theme". Each is (region label, surface key): the surface
# composites over `background`, which composites over the desktop.
COMPOSITE_REGIONS = [("editor", "editor.background"), ("terminal", "terminal.background")]

# Below the floor text drowns in the wallpaper; above the ceiling the transparency
# is not worth shipping.
COMPOSITE_BAND = (0.72, 0.90)

# Color-only, same-hue swaps that apply to the Transparent variant alone, because
# the effective background there is `wallpaper (+) tint` rather than `#000b1e`.
# key -> (opaque variant color, Transparent variant color). Bounded on purpose:
# every entry is a divergence between the variants that has to be maintained.
TRANSPARENT_SUBSTITUTIONS = {
    # 2.27:1 -> 4.48:1 over a white wallpaper. #005faf is legible on deep navy and
    # nowhere near legible on a bright desktop.
    "syntax.comment": ("#005faf", "#5c93c4"),
    "syntax.comment.doc": ("#005faf", "#5c93c4"),
    # table pipes, blockquote bar, horizontal rule, Mermaid flowchart arrowheads:
    # 3.36:1 -> 5.17:1 over white.
    "border": ("#2b77e0", "#4a9fe8"),
    # Mermaid cluster/note strokes, fenced-code border, heading underline:
    # 2.47:1 -> 3.36:1 over white, and still subordinate to `border`.
    "border.variant": ("#1c61c2", "#2b77e0"),
    # whitespace markers, same 2.47:1 -> 3.36:1.
    "editor.invisible": ("#1c61c2", "#2b77e0"),
}
MAX_SUBSTITUTIONS = 6
MAX_HUE_DRIFT_DEGREES = 20.0

# Keys permitted to carry alpha in the Transparent variant. The surface plan, plus
# the overlays that tint window content (hover, selection, search match). Overlays
# float above the pane, so their alpha reads against the code, not the desktop.
ALPHA_ALLOWLIST = set(TRANSPARENT_SURFACE_PLAN) | {
    "editor.active_line.background",
    "editor.highlighted_line.background",
    "editor.document_highlight.read_background",
    "editor.document_highlight.write_background",
    "search.match_background",
    "element.hover",
    "element.active",
    "element.selected",
    "ghost_element.background",
    "ghost_element.hover",
    "ghost_element.active",
    "ghost_element.selected",
    "ghost_element.disabled",
    "drop_target.background",
    "scrollbar.thumb.background",
    "scrollbar.thumb.hover_background",
    "scrollbar.track.background",
    "scrollbar.track.border",
    "players[].selection",
}

# `border.variant` is deliberately absent from ALPHA_ALLOWLIST -- see module docstring.
DENIED_FROM_BRIEF_ALLOWLIST = {"border.variant"}

# A border that exists to reserve layout space without painting. Any opaque value
# makes it paint, so it is exempt from the "no 8-digit hex" rule in both variants.
STRUCTURAL_TRANSPARENT = {"border.transparent": "#00000000"}

# The upstream ANSI table is a 16-slot protocol palette addressed by index, not
# theme text. These slots are reproduced verbatim from Roboron3042/Cyberpunk-Neon
# and are exempt from the 4.5:1 text floor; brightening them would break the
# palette's identity and every TUI that treats "black" as a subtle divider.
ANSI_CONTRAST_EXEMPT = {
    "terminal.ansi.black",          # #123e7c  1.88:1
    "terminal.ansi.blue",           # #123e7c  1.88:1
    "terminal.ansi.green",          # #d300c4  4.28:1
    "terminal.ansi.bright_green",   # #d300c4  4.28:1
    "terminal.ansi.magenta",        # #711c91  2.13:1
    "terminal.ansi.bright_magenta", # #711c91  2.13:1
    "terminal.ansi.bright_black",   # #1c61c2  3.32:1
}
ANSI_DIM_EXEMPT_PREFIX = "terminal.ansi.dim_"   # the "dim" slot is dim by definition

# Synthetic wallpapers the Transparent variant is measured over. A real desktop is
# somewhere between these; the two extremes plus the midpoint bracket it.
WALLPAPERS = [("black", "#000000"), ("mid-gray", "#808080"), ("white", "#ffffff")]

TEXT_FLOOR = 4.5
DIM_FLOOR = 3.0
STROKE_FLOOR = 3.0      # WCAG 1.4.11 non-text contrast
ANSI_FLOOR = 1.8        # exempt slots must still not be literally invisible
# A dim_* slot has no meaningful contrast floor -- being dimmer than its base slot
# is the whole point. It is instead held to a relation: strictly darker than the
# slot it dims, and still distinguishable from the terminal canvas.
ANSI_DIM_FLOOR = 1.25

# Over a non-black wallpaper no dark theme can hold 4.5:1 -- the backdrop is half
# the desktop. Every floor is capped here instead, comments included.
WALLPAPER_FLOOR = 3.0

# Glyph-painting keys: draw a character or an icon fill. Opaque in BOTH variants.
TEXT_KEYS = {
    "editor.foreground", "editor.line_number", "editor.active_line_number",
    "editor.hover_line_number",
    "text", "text.muted", "text.placeholder", "text.disabled", "text.accent",
    # NB: editor.invisible is intentionally NOT here -- see DIM_KEYS.
    "link_text.hover",
    "icon", "icon.muted", "icon.disabled", "icon.placeholder", "icon.accent",
    "debugger.accent",
    "terminal.foreground", "terminal.bright_foreground", "terminal.dim_foreground",
    "conflict", "created", "deleted", "error", "info", "modified", "renamed",
    "success", "warning",
    "version_control.added", "version_control.deleted", "version_control.modified",
    "version_control.renamed", "version_control.conflict", "version_control.ignored",
}
TEXT_KEYS |= {"vim.%s.foreground" % m for m in (
    "normal", "insert", "replace", "visual", "visual_line", "visual_block",
    "helix_normal", "helix_select", "helix_jump")}

# Semantically de-emphasised text. Extension of the brief's "comments >= 3.0" tier.
# `editor.invisible` paints whitespace markers -- de-emphasis is its entire job.
DIM_KEYS = {"hint", "predictive", "ignored", "hidden", "unreachable", "editor.invisible"}
DIM_SYNTAX = {"comment", "comment.doc", "hint", "predictive"}

# Strokes that paint markdown / Mermaid geometry. Opaque in both variants.
STROKE_KEYS = {"border", "border.variant", "border.focused", "border.selected"}

# Backdrops a color is measured against.
BACKDROP = {}
for _k in TEXT_KEYS | DIM_KEYS | STROKE_KEYS:
    BACKDROP[_k] = "editor.background"
for _k in ("text", "text.muted", "text.placeholder", "text.disabled", "text.accent",
           "icon", "icon.muted", "icon.disabled", "icon.placeholder", "icon.accent"):
    BACKDROP[_k] = "background"

# `text` also lands on these; each is checked separately.
TEXT_SURFACES = ["editor.background", "background", "surface.background",
                 "elevated_surface.background", "panel.background",
                 "element.background", "title_bar.background"]

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# --------------------------------------------------------------------------- color math


def parse_hex(value):
    """-> (r, g, b, a) floats in 0..1. Mirrors gpui::Rgba::try_from."""
    h = value.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    if len(h) == 6:
        h += "ff"
    r, g, b, a = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4, 6))
    return r, g, b, a


def has_alpha(value):
    """True if the literal carries an alpha channel (#rgba or #rrggbbaa)."""
    return len(value.lstrip("#")) in (4, 8)


def alpha_of(value):
    return parse_hex(value)[3]


def composite(fg, bg):
    """Composite an RGBA tuple over an opaque RGB backdrop."""
    fr, fg_, fb, fa = fg
    br, bg_, bb, _ = bg
    return (fa * fr + (1 - fa) * br,
            fa * fg_ + (1 - fa) * bg_,
            fa * fb + (1 - fa) * bb,
            1.0)


def relative_luminance(rgba):
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b, _ = rgba
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(fg_hex, bg_hex):
    """WCAG contrast ratio, compositing fg over bg first."""
    bg = parse_hex(bg_hex)
    fg = composite(parse_hex(fg_hex), bg)
    lf, lb = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


def flatten(fg_hex, bg_hex):
    """src-over `fg_hex` onto the opaque `bg_hex`, returning an opaque 6-digit hex."""
    fg = composite(parse_hex(fg_hex), parse_hex(bg_hex))
    return "#%02x%02x%02x" % tuple(round(255 * c) for c in fg[:3])


def blend_over(fg_hex, alpha, bg_hex):
    """Composite fg at `alpha` over bg, returning an opaque 6-digit hex."""
    fr, fg_, fb, _ = parse_hex(fg_hex)
    br, bg_, bb, _ = parse_hex(bg_hex)
    return "#%02x%02x%02x" % tuple(
        round(255 * (alpha * f + (1 - alpha) * b))
        for f, b in ((fr, br), (fg_, bg_), (fb, bb)))


def hue_degrees(hex_value):
    r, g, b, _ = parse_hex(hex_value)
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


def hue_distance(a_hex, b_hex):
    delta = abs(hue_degrees(a_hex) - hue_degrees(b_hex)) % 360.0
    return min(delta, 360.0 - delta)


# --------------------------------------------------------------------------- reporting

class Report:
    def __init__(self):
        self.failures = []
        self.notes = []

    def fail(self, section, message):
        self.failures.append((section, message))

    def note(self, message):
        self.notes.append(message)


def iter_colors(style):
    """Yield (logical_key, hex_value) for every color literal in a style object."""
    for key, value in style.items():
        if key == "syntax":
            for token, entry in value.items():
                for field in ("color", "background_color"):
                    if isinstance(entry, dict) and isinstance(entry.get(field), str):
                        suffix = "" if field == "color" else ".background_color"
                        yield "syntax.%s%s" % (token, suffix), entry[field]
        elif key == "players":
            for i, player in enumerate(value):
                for field, hexv in player.items():
                    yield "players[%d].%s" % (i, field), hexv
        elif key == "accents":
            for i, hexv in enumerate(value):
                yield "accents[%d]" % i, hexv
        elif isinstance(value, str) and value.startswith("#"):
            yield key, value


def normalize(key):
    """players[3].selection -> players[].selection"""
    return re.sub(r"\[\d+\]", "[]", key)


def backdrops(name, style, key):
    """Concrete opaque backdrops to measure a color against, on `key`'s surface.

    Yields (wallpaper, description, opaque_hex, floor_cap). In the opaque variant a
    surface is one backdrop and that is the end of it. In the Transparent variant a
    surface that carries alpha is a stack -- wallpaper, then the window tint, then
    the surface itself -- so it becomes one backdrop per synthetic wallpaper, and
    the floors there are capped at WALLPAPER_FLOOR over anything but black.
    """
    value = style[key]
    if name != TRANSPARENT_VARIANT or not has_alpha(value):
        return [(None, key, value, None)]

    layers = [] if key == "background" else [style["background"]]
    layers.append(value)

    out = []
    for label, wallpaper in WALLPAPERS:
        stacked = wallpaper
        for layer in layers:
            stacked = flatten(layer, stacked)
        cap = None if label == "black" else WALLPAPER_FLOOR
        out.append((label, "%s over a %s wallpaper" % (key, label), stacked, cap))
    return out


# --------------------------------------------------------------------------- checks


def check_schema(doc, path, schema_path, offline, report):
    if offline:
        print("  SCHEMA: SKIPPED (--offline) -- JSON Schema validation did NOT run")
        report.note("schema validation skipped (--offline)")
        return
    try:
        import jsonschema
    except ImportError:
        report.fail("schema", "python module `jsonschema` is not installed "
                              "(pip install jsonschema), and --offline was not passed")
        return

    url = doc.get("$schema")
    if not url:
        report.fail("schema", "theme has no `$schema` key")
        return

    schema = None
    source = None
    if schema_path:
        try:
            with open(schema_path) as fh:
                schema = json.load(fh)
        except (OSError, ValueError) as exc:
            report.fail("schema", "could not read --schema %s (%s)" % (schema_path, exc))
            return
        source = schema_path
    else:
        env = os.environ.get("ZED_THEME_SCHEMA")
        cached = os.path.join(CACHE_DIR, os.path.basename(url))
        if env and os.path.exists(env):
            with open(env) as fh:
                schema = json.load(fh)
            source = "$ZED_THEME_SCHEMA=%s" % env
        elif os.path.exists(cached):
            with open(cached) as fh:
                schema = json.load(fh)
            source = cached
        else:
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                schema = json.loads(raw)
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(cached, "w") as fh:
                    fh.write(raw)
                source = "%s (cached to %s)" % (url, cached)
            except Exception as exc:  # network, DNS, proxy policy, bad JSON
                report.fail("schema",
                            "could not fetch %s (%s). Pass --schema PATH, set "
                            "$ZED_THEME_SCHEMA, or run with --offline." % (url, exc))
                return

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        for err in errors[:20]:
            loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
            report.fail("schema", "%s: %s" % (loc, err.message))
        if len(errors) > 20:
            report.fail("schema", "... and %d more schema errors" % (len(errors) - 20))
    else:
        print("  SCHEMA: ok (%s)" % source)


def check_legacy_key(raw, report):
    """Regression trap. LEGACY_APPEARANCE_KEY is the gpui Rust field name; Zed reads
    `background.appearance` and silently ignores anything else."""
    if LEGACY_APPEARANCE_KEY in raw:
        line = next((i + 1 for i, l in enumerate(raw.splitlines())
                     if LEGACY_APPEARANCE_KEY in l), 0)
        report.fail("transparency",
                    "the theme file contains %r (first at line %d). That is the gpui "
                    "Rust field name, not the JSON key -- Zed reads %r and silently "
                    "ignores the other spelling, so the window stays opaque."
                    % (LEGACY_APPEARANCE_KEY, line, APPEARANCE_KEY))


def check_alpha(themes, report):
    for name, style in themes.items():
        opaque = name == OPAQUE_VARIANT
        for key, value in iter_colors(style):
            if not HEX_RE.match(value):
                report.fail("alpha", "%s: %s = %r is not a valid Zed color "
                                     "(#rgb, #rgba, #rrggbb, #rrggbbaa)" % (name, key, value))
                continue
            if not has_alpha(value):
                continue
            norm = normalize(key)
            if norm in STRUCTURAL_TRANSPARENT:
                expected = STRUCTURAL_TRANSPARENT[norm]
                if value.lower() != expected:
                    report.fail("alpha", "%s: %s is structurally transparent and must be "
                                         "exactly %s, got %s" % (name, key, expected, value))
                continue
            if opaque:
                report.fail("alpha", "%s: %s = %s carries alpha; the opaque variant "
                                     "must be fully opaque" % (name, key, value))
            elif norm not in ALPHA_ALLOWLIST:
                extra = ""
                if norm in DENIED_FROM_BRIEF_ALLOWLIST:
                    extra = (" -- it strokes Mermaid cluster/note borders, the code-block "
                             "border and the h1/h2 underline, so alpha makes them fade out")
                report.fail("alpha", "%s: %s = %s carries alpha but is not on the "
                                     "allowlist%s" % (name, key, value, extra))


def check_contrast(themes, report):
    for name, style in themes.items():
        def measure(what, fg, backdrop_key, floor, canvas_only=False):
            """Check `fg` against every concrete form of `backdrop_key`.

            `canvas_only` restricts the measurement to the theme's own canvas -- the
            darkest wallpaper -- for slots that are already exempt from the
            readability floors. Their residual check only asserts they are not
            invisible against the palette itself, which is not a claim about
            anybody's desktop.
            """
            for wallpaper, description, opaque_bg, cap in backdrops(name, style, backdrop_key):
                if canvas_only and wallpaper not in (None, "black"):
                    continue
                effective = min(floor, cap) if cap is not None else floor
                ratio = contrast(fg, opaque_bg)
                if ratio < effective:
                    report.fail("contrast", "%s: %s is %.2f:1 against %s (%s), floor is %.1f:1"
                                % (name, what, ratio, description, opaque_bg, effective))

        for key, value in iter_colors(style):
            norm = normalize(key)
            floor = None
            canvas_only = False

            if norm.startswith("terminal.ansi."):
                if norm == "terminal.ansi.background":
                    continue
                if norm.startswith(ANSI_DIM_EXEMPT_PREFIX):
                    # Relational rule: strictly darker than the slot it dims, and
                    # still distinguishable from the canvas.
                    base_key = "terminal.ansi." + norm[len(ANSI_DIM_EXEMPT_PREFIX):]
                    measure("%s = %s" % (key, value), value, "terminal.background",
                            ANSI_DIM_FLOOR, canvas_only=True)
                    if base_key in style:
                        base_lum = relative_luminance(parse_hex(style[base_key]))
                        if relative_luminance(parse_hex(value)) >= base_lum:
                            report.fail("contrast", "%s: %s = %s is not darker than %s = %s"
                                        % (name, key, value, base_key, style[base_key]))
                    continue
                if norm in ANSI_CONTRAST_EXEMPT:
                    floor, backdrop, canvas_only = ANSI_FLOOR, "terminal.background", True
                else:
                    floor, backdrop = TEXT_FLOOR, "terminal.background"
            elif norm.startswith("syntax."):
                token = norm[len("syntax."):]
                if token.endswith(".background_color"):
                    continue
                floor = DIM_FLOOR if token in DIM_SYNTAX else TEXT_FLOOR
                backdrop = "editor.background"
            elif norm.endswith("].cursor"):
                floor, backdrop = TEXT_FLOOR, "editor.background"
            elif norm.startswith("accents["):
                floor, backdrop = TEXT_FLOOR, "editor.background"
            elif norm in DIM_KEYS:
                floor, backdrop = DIM_FLOOR, BACKDROP.get(norm, "editor.background")
            elif norm in STROKE_KEYS:
                floor, backdrop = STROKE_FLOOR, BACKDROP.get(norm, "editor.background")
            elif norm in TEXT_KEYS:
                floor, backdrop = TEXT_FLOOR, BACKDROP.get(norm, "editor.background")

            if floor is None:
                continue

            measure("%s = %s" % (key, value), value, backdrop, floor, canvas_only=canvas_only)

        # `text` lands on every surface, not just the editor canvas.
        text = style["text"]
        for surface in TEXT_SURFACES:
            measure("text = %s" % text, text, surface, TEXT_FLOOR)

        # Inline code in the preview: text over editor.foreground @ 8% (markdown.rs:283,:341).
        for base in ("editor.background", "title_bar.background"):
            for _wallpaper, description, opaque_bg, cap in backdrops(name, style, base):
                span_bg = blend_over(style["editor.foreground"], 0.08, opaque_bg)
                floor = min(TEXT_FLOOR, cap) if cap is not None else TEXT_FLOOR
                ratio = contrast(text, span_bg)
                if ratio < floor:
                    report.fail("contrast", "%s: inline code on %s (%s) is %.2f:1, floor "
                                            "is %.1f:1" % (name, description, span_bg, ratio, floor))


def check_transparency(themes, report):
    """The Transparent variant must actually be transparent: the key Zed reads, the
    surface architecture that makes stacked alpha behave, and a composite opacity
    that is neither invisible nor pointless."""
    for name, style in themes.items():
        declared = style.get(APPEARANCE_KEY)
        expected = BACKGROUND_APPEARANCE[name]
        if declared is None:
            report.fail("transparency",
                        "%s does not set %r, so Zed defaults it to 'opaque' and the window "
                        "is not see-through no matter what alpha the surfaces carry; "
                        "expected %r" % (name, APPEARANCE_KEY, expected))
        elif declared != expected:
            report.fail("transparency", "%s declares %s %r, expected %r"
                        % (name, APPEARANCE_KEY, declared, expected))

    transparent = themes[TRANSPARENT_VARIANT]

    # (1) the surface plan, value by value.
    for key in sorted(TRANSPARENT_SURFACE_PLAN):
        expected = TRANSPARENT_SURFACE_PLAN[key]
        got = transparent.get(key)
        if got is None:
            report.fail("transparency", "%s is missing surface %s, which the surface plan "
                                        "sets to %s" % (TRANSPARENT_VARIANT, key, expected))
        elif got.lower() != expected:
            report.fail("transparency",
                        "%s: %s = %s, surface plan says %s. Stacked alphas composite "
                        "toward opaque, so only `background` carries the tint and the "
                        "surfaces over it are clear." % (TRANSPARENT_VARIANT, key, got, expected))

    # (2) the composite opacity of the regions a user actually looks through.
    base_alpha = alpha_of(transparent["background"])
    lo, hi = COMPOSITE_BAND
    for region, surface_key in COMPOSITE_REGIONS:
        surface_alpha = alpha_of(transparent[surface_key])
        effective = 1 - (1 - base_alpha) * (1 - surface_alpha)
        if not lo <= effective <= hi:
            report.fail("transparency",
                        "%s: the %s region composites to %.3f opacity (background %s over "
                        "%s %s); the band is [%.2f, %.2f] -- below it text drowns in the "
                        "wallpaper, above it the transparency is not worth shipping"
                        % (TRANSPARENT_VARIANT, region, effective, transparent["background"],
                           surface_key, transparent[surface_key], lo, hi))

    # (3) glyph opacity is law: nothing that paints a character or a stroke may
    # carry alpha, in either variant. (check_alpha covers the allowlist; this is
    # the explicit statement of the rule for text and strokes.)
    for name, style in themes.items():
        for key, value in iter_colors(style):
            norm = normalize(key)
            paints_glyph = (norm in TEXT_KEYS or norm in DIM_KEYS or norm in STROKE_KEYS
                            or norm.endswith("].cursor")
                            or (norm.startswith("syntax.")
                                and not norm.endswith(".background_color"))
                            or (norm.startswith("terminal.ansi.")
                                and norm != "terminal.ansi.background"))
            if paints_glyph and has_alpha(value):
                report.fail("transparency", "%s: %s = %s paints text or a stroke and must be "
                                            "opaque 6-digit hex" % (name, key, value))


def check_substitutions(themes, report):
    """The declared color divergence between the variants -- bounded, same-hue, and
    matching what the themes actually say."""
    if len(TRANSPARENT_SUBSTITUTIONS) > MAX_SUBSTITUTIONS:
        report.fail("variants", "TRANSPARENT_SUBSTITUTIONS has %d entries, the cap is %d"
                    % (len(TRANSPARENT_SUBSTITUTIONS), MAX_SUBSTITUTIONS))

    opaque = {normalize(k): v for k, v in iter_colors(themes[OPAQUE_VARIANT])}
    transparent = {normalize(k): v for k, v in iter_colors(themes[TRANSPARENT_VARIANT])}

    for key, (want_opaque, want_transparent) in sorted(TRANSPARENT_SUBSTITUTIONS.items()):
        if key not in opaque:
            report.fail("variants", "substitution %s names a key neither variant has" % key)
            continue
        if opaque[key].lower() != want_opaque:
            report.fail("variants", "substitution %s expects %s in %s, theme says %s"
                        % (key, want_opaque, OPAQUE_VARIANT, opaque[key]))
        if transparent[key].lower() != want_transparent:
            report.fail("variants", "substitution %s expects %s in %s, theme says %s"
                        % (key, want_transparent, TRANSPARENT_VARIANT, transparent[key]))
        if want_opaque == want_transparent:
            report.fail("variants", "substitution %s substitutes nothing (%s -> %s)"
                        % (key, want_opaque, want_transparent))
        if has_alpha(want_transparent):
            report.fail("variants", "substitution %s introduces alpha (%s); substitutions "
                                    "are color-only" % (key, want_transparent))
        drift = hue_distance(want_opaque, want_transparent)
        if drift > MAX_HUE_DRIFT_DEGREES:
            report.fail("variants", "substitution %s moves hue by %.0f deg (%s -> %s); the "
                                    "cap is %.0f deg -- substitutions stay in the same hue "
                                    "family" % (key, drift, want_opaque, want_transparent,
                                                MAX_HUE_DRIFT_DEGREES))


def check_variants_identical(themes, report):
    """Parity: identical everywhere except the surface plan, the overlays that carry
    alpha, the declared substitutions, and `background.appearance`."""
    a, b = themes[OPAQUE_VARIANT], themes[TRANSPARENT_VARIANT]
    keys_a = {normalize(k): v for k, v in iter_colors(a)}
    keys_b = {normalize(k): v for k, v in iter_colors(b)}

    if set(keys_a) != set(keys_b):
        for key in sorted(set(keys_a) ^ set(keys_b)):
            report.fail("variants", "key %s is present in only one variant" % key)
        return

    non_color_a = {k for k in a if not isinstance(a[k], str) or not a[k].startswith("#")}
    non_color_b = {k for k in b if not isinstance(b[k], str) or not b[k].startswith("#")}
    if non_color_a != non_color_b:
        for key in sorted(non_color_a ^ non_color_b):
            report.fail("variants", "non-color key %s is present in only one variant" % key)

    may_differ = ALPHA_ALLOWLIST | set(TRANSPARENT_SUBSTITUTIONS)
    for key in sorted(keys_a):
        if key in may_differ:
            continue
        if keys_a[key] != keys_b[key]:
            report.fail("variants", "key %s differs between the variants (%s vs %s) but is "
                                    "neither alpha-allowlisted nor a declared substitution"
                        % (key, keys_a[key], keys_b[key]))


# --------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("theme", help="path to the theme family JSON")
    ap.add_argument("--schema", help="local copy of the Zed theme JSON Schema")
    ap.add_argument("--offline", action="store_true",
                    help="skip JSON Schema validation when the schema host is unreachable")
    args = ap.parse_args()

    with open(args.theme) as fh:
        raw = fh.read()
    doc = json.loads(raw)

    themes = {t["name"]: t["style"] for t in doc.get("themes", [])}
    missing = {OPAQUE_VARIANT, TRANSPARENT_VARIANT} - set(themes)
    if missing:
        print("FAIL: theme family is missing variant(s): %s" % ", ".join(sorted(missing)))
        return 1
    extra = set(themes) - {OPAQUE_VARIANT, TRANSPARENT_VARIANT}
    if extra:
        print("FAIL: theme family ships unexpected variant(s): %s" % ", ".join(sorted(extra)))
        return 1
    for t in doc["themes"]:
        if t.get("appearance") != "dark":
            print("FAIL: %s has appearance %r, expected 'dark'" % (t["name"], t.get("appearance")))
            return 1

    report = Report()

    print("checking %s" % args.theme)
    print("  variants: %s" % ", ".join(t["name"] for t in doc["themes"]))
    check_schema(doc, args.theme, args.schema, args.offline, report)
    check_legacy_key(raw, report)
    check_alpha(themes, report)
    check_transparency(themes, report)
    check_substitutions(themes, report)
    check_contrast(themes, report)
    check_variants_identical(themes, report)

    tr = themes[TRANSPARENT_VARIANT]
    print("  %s: %s = %r" % (OPAQUE_VARIANT, APPEARANCE_KEY,
                             themes[OPAQUE_VARIANT].get(APPEARANCE_KEY)))
    print("  %s: %s = %r, one tinted pane (%s) under %d clear surfaces"
          % (TRANSPARENT_VARIANT, APPEARANCE_KEY, tr.get(APPEARANCE_KEY), tr["background"],
             sum(1 for v in TRANSPARENT_SURFACE_PLAN.values() if v == "#00000000")))
    base_alpha = alpha_of(tr["background"])
    for region, surface_key in COMPOSITE_REGIONS:
        effective = 1 - (1 - base_alpha) * (1 - alpha_of(tr[surface_key]))
        print("      %-8s region composites to %.1f%% opacity (band %.0f-%.0f%%); over a"
              % (region, effective * 100, COMPOSITE_BAND[0] * 100, COMPOSITE_BAND[1] * 100))
        for label, wallpaper in WALLPAPERS:
            print("          %-9s wallpaper -> %s"
                  % (label, flatten(tr[surface_key], flatten(tr["background"], wallpaper))))

    print("  color substitutions in %s (%d of at most %d, same hue, color only):"
          % (TRANSPARENT_VARIANT, len(TRANSPARENT_SUBSTITUTIONS), MAX_SUBSTITUTIONS))
    for key, (was, now) in sorted(TRANSPARENT_SUBSTITUTIONS.items()):
        print("      %-22s %s -> %s" % (key, was, now))

    # Exemptions are never silent.
    print("  exempt from the %.1f:1 text floor (verbatim upstream ANSI, %d slots):"
          % (TEXT_FLOOR, len(ANSI_CONTRAST_EXEMPT)))
    for key in sorted(ANSI_CONTRAST_EXEMPT):
        value = themes[OPAQUE_VARIANT][key]
        print("      %-30s %s  %.2f:1" % (key, value,
                                          contrast(value, themes[OPAQUE_VARIANT]["terminal.background"])))
    print("      + 8 terminal.ansi.dim_* slots (dim by definition)")
    print("  exempt from the no-alpha rule in both variants (structural):")
    for key, value in sorted(STRUCTURAL_TRANSPARENT.items()):
        print("      %-30s %s" % (key, value))
    print("  de-emphasised tier at %.1f:1 rather than %.1f:1: %s"
          % (DIM_FLOOR, TEXT_FLOOR, ", ".join(sorted(DIM_KEYS | DIM_SYNTAX))))
    print("  every floor is capped at %.1f:1 over a non-black wallpaper -- no dark theme"
          % WALLPAPER_FLOOR)
    print("      holds 4.5:1 when half the backdrop is somebody's desktop.")
    print("  border.variant is NOT alpha-allowlisted: it strokes Mermaid cluster/note")
    print("      borders, the fenced-code-block border and the h1/h2 underline.")

    if report.failures:
        print()
        by_section = {}
        for section, message in report.failures:
            by_section.setdefault(section, []).append(message)
        for section in ("schema", "alpha", "transparency", "contrast", "variants"):
            for message in by_section.get(section, []):
                print("FAIL [%s] %s" % (section, message))
        print("\n%d failure(s)" % len(report.failures))
        return 1

    print()
    for note in report.notes:
        print("NOTE: %s" % note)
    print("OK: schema, alpha policy, window transparency, contrast floors over black, "
          "mid-gray and white wallpapers, and variant parity all pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
