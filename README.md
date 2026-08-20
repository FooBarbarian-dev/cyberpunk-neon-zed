# Cyberpunk Neon for Zed

A Zed theme extension porting [Roboron3042/Cyberpunk-Neon](https://github.com/Roboron3042/Cyberpunk-Neon)
— neon cyan and magenta on deep navy.

The family ships two dark variants:

| Variant                      | Notes                                                                                                                                                    |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Cyberpunk Neon`             | `"background.appearance": "opaque"`, every color fully opaque. The default.                                                                              |
| `Cyberpunk Neon Transparent` | `"background.appearance": "transparent"`, the code and terminal regions composite to 89.5% — your desktop is visible through the code — while the docks (AI chat, project tree, git panel) and the title/status bars sit on near-solid chrome so prose stays readable, and the AI chat's message boxes keep a visible fill. |

`background.appearance` is the key Zed actually reads
([`crates/settings_content/src/theme.rs:538`](https://github.com/zed-industries/zed/blob/main/crates/settings_content/src/theme.rs),
`#[serde(rename = "background.appearance")]`), and its values are `"opaque"`,
`"transparent"` and `"blurred"`
([same file, `enum WindowBackgroundContent`](https://github.com/zed-industries/zed/blob/main/crates/settings_content/src/theme.rs)).
Note the `#[serde(rename = ...)]`: the Rust struct field behind that key is spelled
differently, and a theme that uses the *field* name instead of the renamed JSON key
is silently ignored, because the theme schema tolerates unknown properties. Nothing
complains and nothing happens. The checker traps that spelling as a regression, and
assembles it at runtime so a repo-wide grep for it stays clean.

### How the transparency is built

Alpha does not stack the way it looks like it should. `background` at 85% under
`editor.background` at 85% composites to ~97.8% in the editor region — opacity
with extra steps ([zed#55972](https://github.com/zed-industries/zed/issues/55972)).
So the Transparent variant is **one tinted pane with tiers above it**, each tier
chosen by what kind of content it holds:

- `background` = `#000b1ed6` carries most of the window tint (84% navy).
- **Tinted glass** (`#000b1e59`, 35% navy) over the showcase — code and terminal
  output are high-contrast neon glyphs and survive the wallpaper:
  `editor.background`, `editor.gutter.background`, `terminal.background`,
  `terminal.ansi.background`. The tint is not decoration: the AI chat's message
  cards, tool-call cards and input box are painted with `editor.background`
  ([`agent_ui/.../thread_view.rs`](https://github.com/zed-industries/zed/blob/main/crates/agent_ui/src/conversation_view/thread_view.rs)),
  so fully clear glass there makes every box in the agent thread vanish into
  the dock chrome behind it. 35% over the 84% base keeps the code region at
  89.5% while giving those boxes a visible fill again.
- **Clear glass** (`#00000000`) on chrome inside the code region that carries no
  prose: `tab_bar.background`, `tab.inactive_background`, `toolbar.background`.
- **Dock chrome** — `panel.background` = `#091833e6` (90% navy, ~98% composited).
  Zed paints this behind every dock panel
  ([`workspace/src/dock.rs`](https://github.com/zed-industries/zed/blob/main/crates/workspace/src/dock.rs)):
  the agent (AI chat) panel, project tree, git panel, outline. Those are prose
  and muted labels, which drown in wallpaper bleed long before code does, so the
  docks read as near-solid chrome.
- **Structure** inside the code region keeps a light tint:
  `tab.active_background`, `editor.subheader.background` at `#09183366` (40%).
- **Boundary chrome** — `title_bar.background`, `status_bar.background` at
  `#091833cc` (80%, ~97% composited). They frame the window with project and
  branch names and diagnostics, so they sit heavier than in-region structure.
- **Grounded and floating surfaces stay fully opaque** —
  `elevated_surface.background` (context menus, pickers, the completion menu),
  `surface.background` (popover asides, keybinding hints),
  `panel.overlay_background`, `element.background`. Alpha there shows your code
  through menus rather than showing the desktop.

The result: the editor and terminal regions composite to **89.5% opacity**, which
the checker enforces to stay inside `[0.72, 0.90]` — below that floor text drowns
in the wallpaper, above that ceiling the transparency isn't worth shipping.

One consequence of the dock tier worth knowing: a terminal living in the
**bottom dock** sits above the dock chrome and is therefore near-solid; a
terminal in the **center pane** keeps the 89.5% glass. If you want the
see-through terminal, put it in the center (or lower the `panel.background`
alpha — see below).

Every color that paints a glyph or a stroke — foregrounds, all `syntax` colors,
line numbers, cursors, the whole ANSI table — is opaque 6-digit hex in both
variants. Transparency lives in surfaces only.

### Tuning the transparency

Three dials, all in `themes/cyberpunk-neon.json`, each mirrored by an entry in
`TRANSPARENT_SURFACE_PLAN` in `scripts/check_theme.py`:

- **How see-through the code is**: the last two hex digits of `background`
  (`cc` is 80%, `d6` is 84%, `e0` is 88%).
- **How visible the AI chat's boxes are**: the last two hex digits of
  `editor.background` (and its three siblings — gutter and the two terminal
  keys). This is the fill of the agent thread's message cards and input box;
  it also adds onto the code region's composite, so raising one usually means
  lowering the other to stay inside the `[0.72, 0.90]` band.
- **How solid the docks are**: the last two hex digits of `panel.background`
  (`b3` is 70%, `cc` is 80%, `e6` is 90%). Lower it if you want more desktop
  showing through the AI chat and the project tree, raise it if prose still
  fights your wallpaper.

Update the matching plan entry and re-run the checker: it enforces the plan value
by value, re-derives the region opacity, and re-measures every contrast floor
against the new composite. Do not try to create *transparency* by stacking alpha
across the mid-surfaces (editor, tab bar, terminal) — alphas composite toward
opaque, which is the bug this architecture exists to avoid; `background` alone
decides how much desktop comes through the glass.

To get frosted glass instead of a clear view, change the one word
`"transparent"` to `"blurred"` (and the expected value in the checker's
`BACKGROUND_APPEARANCE`). Be aware that on macOS `blurred` goes through
`NSVisualEffectView` and largely frosts the wallpaper away
([zed discussion #53795](https://github.com/zed-industries/zed/discussions/53795));
gpui also documents it as "not always supported".

### Why five colors differ between the variants

Once the window is really transparent the effective background is
`wallpaper ⊕ tint`, and floors that held against `#000b1e` collapse over a bright
desktop. The Transparent variant therefore substitutes five colors — same hue
family, color only, declared in `TRANSPARENT_SUBSTITUTIONS`:

| Key                                  | Opaque    | Transparent | Why                                                                                              |
| ------------------------------------ | --------- | ----------- | ------------------------------------------------------------------------------------------------ |
| `syntax.comment`, `syntax.comment.doc` | `#005faf` | `#5c93c4`   | 2.27:1 → 4.48:1 over a white wallpaper; `#005faf` is legible on deep navy and nowhere else.       |
| `border`                             | `#2b77e0` | `#4a9fe8`   | Table pipes, blockquote bar, horizontal rule and Mermaid flowchart arrowheads: 3.36:1 → 5.17:1.  |
| `border.variant`                     | `#1c61c2` | `#2b77e0`   | Mermaid cluster/note strokes, fenced-code border, heading underline: 2.47:1 → 3.36:1.            |
| `editor.invisible`                   | `#1c61c2` | `#2b77e0`   | Whitespace markers, same 2.47:1 → 3.36:1.                                                        |

Everything else is one shared palette.

### The three highlight colors that must carry alpha

Zed's rendered-markdown element — chat replies in the agent (AI chat) panel,
hover documentation, notifications — paints its highlight quads **after** the
glyphs ([`crates/markdown/src/markdown.rs`](https://github.com/zed-industries/zed/blob/main/crates/markdown/src/markdown.rs),
`Element::paint` runs `text.paint()` first and `paint_highlights()` on top of
it; the selection color comes from `element.selection_background` and search
matches from the two `search.*_background` keys). A fully opaque highlight
there is a redaction bar: select a chat reply and the quad covers the very
words it highlights. The same three keys paint *under* the glyphs in the code
editor, where alpha simply composites against the canvas.

So these three keys carry alpha **in both variants** — the one deliberate
exception to "the opaque variant is fully opaque":

| Key                              | Value       | Flattened on the editor canvas                            |
| -------------------------------- | ----------- | --------------------------------------------------------- |
| `element.selection_background`   | `#0abdc63d` | `#023646` — matches the editor's own selection (`#023848`) |
| `search.match_background`        | `#ea00d94d` | `#470856` — the magenta this theme always used             |
| `search.active_match_background` | `#f5780059` | `#563114` — the active-match orange                        |

The checker holds them to an alpha band (`GLYPH_OVERLAY_ALPHA_BAND`) — below it
the highlight disappears, above it the glyphs drown — and measures `text`
*through* each wash against the usual contrast floors.

## The semantic color map

The chrome is deliberately navy — that restraint *is* the Cyberpunk Neon
identity — and every accent hue carries one meaning, so color is information
rather than decoration:

| Hue                            | Meaning                                                                                                                            |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Cyan** `#0abdc6`             | content and *you are here*: body text, icons, the active line number, the active indent guide (`#0a7f88`), info, selection washes   |
| **Magenta** `#ea00d9`          | focus and emphasis: focused/selected borders, keywords, functions, search matches, conflicts, drop targets                          |
| **Orange** `#f57800`           | attention: warnings, modified files, the active search match, link hover, the debugger, strings/constants                           |
| **Green** `#00ff00`            | added / created / insert mode                                                                                                       |
| **Red** `#ff0000`              | deleted / errors / replace mode                                                                                                     |
| **Violet** `#b854de`/`#9d8fd6` | the speculative tier: variables and namespaces, and — dimmed — AI edit predictions (`predictive`)                                   |
| **Steel blue** `#5c93c4`       | de-emphasis: comments (Transparent), hidden/ignored files, placeholders, tooling inlay `hint`s                                      |

Two distinctions in that table exist specifically to carry information that
identical colors were hiding:

- **`hint` vs `predictive`** — inlay hints from tooling stay steel blue, while
  AI edit predictions render dim violet `#9d8fd6`. Both are ghost text in the
  buffer; the hue is the only way to tell what a compiler says from what a
  model guesses.
- **Active indent guides** (`editor.indent_guide_active`,
  `panel.indent_guide_active`) are dim cyan `#0a7f88` rather than generic
  blue, extending the *you-are-here* rule the active line number already
  follows.

When adding a color, extend this table first — a hue that means nothing should
stay navy.

## Installation

### From the extension store

Open `zed: extensions` and search for **Cyberpunk Neon**, then pick a variant
from `theme selector: toggle`.

### As a dev extension

```sh
git clone https://github.com/FooBarbarian-dev/cyberpunk-neon-zed
```

In Zed, run `zed: install dev extension` (or the **Install Dev Extension**
button on the extensions page) and select the cloned directory. Zed reads
`extension.toml` at the root and auto-discovers every theme family in
`themes/`, so both variants appear in the theme selector immediately.

Errors during install show up in `zed: open log`.

## Troubleshooting: the theme is correct but nothing is transparent

If `Cyberpunk Neon Transparent` is selected, `python3 scripts/check_theme.py`
passes, and the window is still solid, the theme is not the problem — stop editing
JSON and check the platform:

- **Linux/X11** needs a running compositor (picom, or your desktop environment's
  own). Without one, `"transparent"` has nothing to composite against.
- **Wayland** needs the session to allow transparent surfaces; some
  window-decoration setups override it.
- **Zed itself** has shipped versions where a correct config did nothing at all
  ([zed#38995](https://github.com/zed-industries/zed/issues/38995),
  [zed#41230](https://github.com/zed-industries/zed/issues/41230)). Update Zed and
  re-check before assuming the theme is at fault.

Correct theme + no transparency ⇒ compositor or Zed version, in that order.

## Repository layout

```
extension.toml            # extension manifest — required for Zed to install this at all
themes/
  cyberpunk-neon.json     # the theme family (both variants)
scripts/
  check_theme.py          # schema + alpha-policy + WCAG contrast checker
test/
  markdown-torture.md     # fixture exercising markdown, Mermaid, code blocks and tables
```

## Validating a change

`scripts/check_theme.py` validates the family against the Zed theme JSON Schema
and against the rules this theme is built on:

```sh
pip install jsonschema
python3 scripts/check_theme.py themes/cyberpunk-neon.json
```

- **Appearance**: each variant declares the `background.appearance` it means, and
  the pre-rename Rust field name may not appear in the theme file at all.
- **Surface plan**: the Transparent variant's window surfaces must match
  `TRANSPARENT_SURFACE_PLAN` value by value, and the editor and terminal regions
  must composite to an opacity inside `[0.72, 0.90]`.
- **Alpha policy**: which keys may carry alpha, none in the opaque variant except
  the three glyph-overlay highlights (which rendered markdown paints *over* the
  text, so they must carry alpha in both variants), and nothing that paints a
  glyph or a stroke in either.
- **Contrast**: WCAG floors for both variants. The opaque variant is measured
  against its own surfaces; the Transparent variant is measured against the real
  composite — `wallpaper ⊕ background ⊕ surface`, src-over — over a **black**, a
  **mid-gray** and a **white** synthetic wallpaper. Over black the full floors
  apply (4.5:1 text, 3.0:1 for the de-emphasised tier); over the lighter
  wallpapers every floor is capped at 3.0:1, comments included, because no dark
  theme holds 4.5:1 when half the backdrop is somebody's desktop.
- **Parity**: the variants must be identical except on the surface plan, the
  alpha-carrying overlays, the declared substitutions, and `background.appearance`.

Schema validation fetches `https://zed.dev/schema/themes/v0.2.0.json` and
caches it under `scripts/.cache/`. Where that host is unreachable, pass a local
copy with `--schema PATH` (or set `$ZED_THEME_SCHEMA`), or skip that one check
with `--offline` — the alpha, transparency, contrast and parity checks still run.

To eyeball the result, open `test/markdown-torture.md` in Zed with the theme
active and toggle the markdown preview. Worth doing once over a dark wallpaper and
once over a light one when you touch the Transparent variant.

## License and attribution

The Zed port in this repository is MIT licensed (see [LICENSE](LICENSE)).

The color palette is derived from
[Roboron3042/Cyberpunk-Neon](https://github.com/Roboron3042/Cyberpunk-Neon),
which is licensed **CC-BY-SA-4.0**. That license is share-alike, so
redistributing a derived palette under MIT is arguably inconsistent with it —
if you plan to publish this to the Zed extension store, settle the license
question first (relicensing the theme JSON under CC-BY-SA-4.0 while keeping
the tooling MIT is the usual resolution).
