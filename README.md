# Cyberpunk Neon for Zed

A Zed theme extension porting [Roboron3042/Cyberpunk-Neon](https://github.com/Roboron3042/Cyberpunk-Neon)
— neon cyan and magenta on deep navy.

The family ships two dark variants:

| Variant                | Notes                                                                                                                                            |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `Cyberpunk Neon`       | `window_background_appearance: "blurred"`, window surfaces at 85% — the desktop shows through the editor, panels, tabs, terminal and title bar.  |
| `Cyberpunk Neon Solid` | `window_background_appearance: "opaque"`, every color fully opaque. Same palette, no see-through.                                                |

### Tuning the translucency

Both variants share one palette; the translucent one is that palette with an
alpha suffix on the 15 surfaces that sit against the desktop. To change the
strength, edit the last two hex digits of those surfaces in
`themes/cyberpunk-neon.json` — `d9` is 85%, `bf` is 75%, `f2` is 95% — then
re-run the checker, which enforces that they all share one alpha value.

Surfaces that float *above* window content — `elevated_surface.background`
(popovers, the completion menu), `panel.overlay_background`,
`element.background` — deliberately stay opaque. Alpha there shows your code
through menus rather than showing the desktop.

`blurred` is documented in gpui as "not always supported"; where the platform
or compositor can't do it, use `"transparent"` for plain alpha, or
`"opaque"`/the Solid variant to turn the effect off.

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

`scripts/check_theme.py` validates the family against the Zed theme JSON
Schema, an alpha policy (which keys may carry alpha, and none at all in the
Solid variant), WCAG contrast floors, and parity between the two variants:

```sh
pip install jsonschema
python3 scripts/check_theme.py themes/cyberpunk-neon.json
```

Contrast floors are enforced against a **dark desktop** — the case a dark theme
is picked for. Because the translucent variant lets the wallpaper through, no
theme can guarantee contrast against an arbitrary one, so shortfalls over
lighter desktops are reported as grouped advisories rather than failures.

Schema validation fetches `https://zed.dev/schema/themes/v0.2.0.json` and
caches it under `scripts/.cache/`. Where that host is unreachable, pass a local
copy with `--schema PATH` (or set `$ZED_THEME_SCHEMA`), or skip that one check
with `--offline` — the alpha, contrast and parity checks still run.

To eyeball the result, open `test/markdown-torture.md` in Zed with the theme
active and toggle the markdown preview.

## License and attribution

The Zed port in this repository is MIT licensed (see [LICENSE](LICENSE)).

The color palette is derived from
[Roboron3042/Cyberpunk-Neon](https://github.com/Roboron3042/Cyberpunk-Neon),
which is licensed **CC-BY-SA-4.0**. That license is share-alike, so
redistributing a derived palette under MIT is arguably inconsistent with it —
if you plan to publish this to the Zed extension store, settle the license
question first (relicensing the theme JSON under CC-BY-SA-4.0 while keeping
the tooling MIT is the usual resolution).
