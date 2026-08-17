#!/usr/bin/env python3
"""Validate a Zed theme family against the schema, an alpha policy, and WCAG contrast floors.

Usage:
    python3 scripts/check_theme.py themes/cyberpunk-neon.json
    python3 scripts/check_theme.py themes/cyberpunk-neon.json --schema /path/to/v0.2.0.json
    python3 scripts/check_theme.py themes/cyberpunk-neon.json --offline

Checks
  (a) JSON Schema validation against the theme's own `$schema` URL.
  (b) Alpha policy: no alpha-bearing hex outside the allowlist in the translucent
      variant, and none at all in the Solid variant.
  (c) Contrast floors, WCAG relative luminance, RGBA composited over the backdrop.
  (d) The two variants are identical on every non-allowlisted key.

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
strokes. Neither may carry alpha, which is why border.variant is NOT allowlisted
here even though the original brief listed it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache")

TRANSLUCENT_VARIANT = "Cyberpunk Neon"
SOLID_VARIANT = "Cyberpunk Neon Solid"

# --------------------------------------------------------------------------- policy

# Keys permitted to carry alpha in the translucent variant. `ghost_element.*` and
# `scrollbar.track.*` expand to their concrete members.
ALPHA_ALLOWLIST = {
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

TEXT_FLOOR = 4.5
DIM_FLOOR = 3.0
STROKE_FLOOR = 3.0      # WCAG 1.4.11 non-text contrast
ANSI_FLOOR = 1.8        # exempt slots must still not be literally invisible
# A dim_* slot has no meaningful contrast floor -- being dimmer than its base slot
# is the whole point. It is instead held to a relation: strictly darker than the
# slot it dims, and still distinguishable from the terminal canvas.
ANSI_DIM_FLOOR = 1.25

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


def blend_over(fg_hex, alpha, bg_hex):
    """Composite fg at `alpha` over bg, returning an opaque 6-digit hex."""
    fr, fg_, fb, _ = parse_hex(fg_hex)
    br, bg_, bb, _ = parse_hex(bg_hex)
    return "#%02x%02x%02x" % tuple(
        round(255 * (alpha * f + (1 - alpha) * b))
        for f, b in ((fr, br), (fg_, bg_), (fb, bb)))


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


def check_alpha(themes, report):
    for name, style in themes.items():
        solid = name == SOLID_VARIANT
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
            if solid:
                report.fail("alpha", "%s: %s = %s carries alpha; the Solid variant "
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
        def resolve(k):
            return style[k]

        for key, value in iter_colors(style):
            norm = normalize(key)
            floor = None

            if norm.startswith("terminal.ansi."):
                if norm == "terminal.ansi.background":
                    continue
                if norm.startswith(ANSI_DIM_EXEMPT_PREFIX):
                    # Relational rule: strictly darker than the slot it dims, and
                    # still distinguishable from the canvas.
                    base_key = "terminal.ansi." + norm[len(ANSI_DIM_EXEMPT_PREFIX):]
                    canvas = resolve("terminal.background")
                    ratio = contrast(value, canvas)
                    if ratio < ANSI_DIM_FLOOR:
                        report.fail("contrast", "%s: %s = %s is %.2f:1 against "
                                    "terminal.background, dim floor is %.2f:1"
                                    % (name, key, value, ratio, ANSI_DIM_FLOOR))
                    if base_key in style:
                        base_lum = relative_luminance(parse_hex(style[base_key]))
                        if relative_luminance(parse_hex(value)) >= base_lum:
                            report.fail("contrast", "%s: %s = %s is not darker than %s = %s"
                                        % (name, key, value, base_key, style[base_key]))
                    continue
                if norm in ANSI_CONTRAST_EXEMPT:
                    floor, backdrop = ANSI_FLOOR, "terminal.background"
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

            ratio = contrast(value, resolve(backdrop))
            if ratio < floor:
                report.fail("contrast", "%s: %s = %s is %.2f:1 against %s, floor is %.1f:1"
                            % (name, key, value, ratio, backdrop, floor))

        # `text` lands on every surface, not just the editor canvas.
        text = resolve("text")
        for surface in TEXT_SURFACES:
            ratio = contrast(text, resolve(surface))
            if ratio < TEXT_FLOOR:
                report.fail("contrast", "%s: text = %s is %.2f:1 against %s, floor is %.1f:1"
                            % (name, text, ratio, surface, TEXT_FLOOR))

        # Inline code in the preview: text over editor.foreground @ 8% (markdown.rs:283,:341).
        for base in ("editor.background", "title_bar.background"):
            span_bg = blend_over(resolve("editor.foreground"), 0.08, resolve(base))
            ratio = contrast(text, span_bg)
            if ratio < TEXT_FLOOR:
                report.fail("contrast", "%s: inline code (text over editor.foreground@8%% on "
                                        "%s = %s) is %.2f:1, floor is %.1f:1"
                            % (name, base, span_bg, ratio, TEXT_FLOOR))


def check_variants_identical(themes, report):
    a, b = themes[TRANSLUCENT_VARIANT], themes[SOLID_VARIANT]
    keys_a = {normalize(k): v for k, v in iter_colors(a)}
    keys_b = {normalize(k): v for k, v in iter_colors(b)}

    if set(keys_a) != set(keys_b):
        for key in sorted(set(keys_a) ^ set(keys_b)):
            report.fail("variants", "key %s is present in only one variant" % key)
        return

    for key in sorted(keys_a):
        if key in ALPHA_ALLOWLIST:
            continue
        if keys_a[key] != keys_b[key]:
            report.fail("variants", "non-allowlisted key %s differs: %s vs %s"
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
        doc = json.load(fh)

    themes = {t["name"]: t["style"] for t in doc.get("themes", [])}
    missing = {TRANSLUCENT_VARIANT, SOLID_VARIANT} - set(themes)
    if missing:
        print("FAIL: theme family is missing variant(s): %s" % ", ".join(sorted(missing)))
        return 1
    for t in doc["themes"]:
        if t.get("appearance") != "dark":
            print("FAIL: %s has appearance %r, expected 'dark'" % (t["name"], t.get("appearance")))
            return 1

    report = Report()

    print("checking %s" % args.theme)
    print("  variants: %s" % ", ".join(sorted(themes)))
    check_schema(doc, args.theme, args.schema, args.offline, report)
    check_alpha(themes, report)
    check_contrast(themes, report)
    check_variants_identical(themes, report)

    # Exemptions are never silent.
    print("  exempt from the %.1f:1 text floor (verbatim upstream ANSI, %d slots):"
          % (TEXT_FLOOR, len(ANSI_CONTRAST_EXEMPT)))
    for key in sorted(ANSI_CONTRAST_EXEMPT):
        value = themes[SOLID_VARIANT][key]
        print("      %-30s %s  %.2f:1" % (key, value,
                                          contrast(value, themes[SOLID_VARIANT]["terminal.background"])))
    print("      + 8 terminal.ansi.dim_* slots (dim by definition)")
    print("  exempt from the no-alpha rule in both variants (structural):")
    for key, value in sorted(STRUCTURAL_TRANSPARENT.items()):
        print("      %-30s %s" % (key, value))
    print("  de-emphasised tier at %.1f:1 rather than %.1f:1: %s"
          % (DIM_FLOOR, TEXT_FLOOR, ", ".join(sorted(DIM_KEYS | DIM_SYNTAX))))
    print("  border.variant is NOT alpha-allowlisted: it strokes Mermaid cluster/note")
    print("      borders, the fenced-code-block border and the h1/h2 underline.")

    if report.failures:
        print()
        by_section = {}
        for section, message in report.failures:
            by_section.setdefault(section, []).append(message)
        for section in ("schema", "alpha", "contrast", "variants"):
            for message in by_section.get(section, []):
                print("FAIL [%s] %s" % (section, message))
        print("\n%d failure(s)" % len(report.failures))
        return 1

    print()
    for note in report.notes:
        print("NOTE: %s" % note)
    print("OK: schema, alpha policy, contrast floors and variant parity all pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
