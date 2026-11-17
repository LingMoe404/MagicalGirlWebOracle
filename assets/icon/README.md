# MagicalGirlWebOracle icon

Original artwork for MagicalGirlWebOracle: a magical orb with an eye and wings.
The set is generated from the same visual system as the other MagicalGirl app
icons (pearl / obsidian / frosted themes, idle / active / success / alert
states, 512px master and 16/32px LOD variants).

![MagicalGirlWebOracle](MagicalGirlWebOracle.png)

## Formats in this directory

| Form | File | Used by |
|---|---|---|
| 512px master | [`MagicalGirlWebOracle.png`](MagicalGirlWebOracle.png) | README, GitHub social preview, Docker Hub logo |
| Pearl theme | [`themes/MagicalGirlWebOracle-frosted.png`](themes/MagicalGirlWebOracle-frosted.png) | Light UI reference |
| Obsidian theme | [`themes/MagicalGirlWebOracle-obsidian.png`](themes/MagicalGirlWebOracle-obsidian.png) | Dark UI reference |
| Idle state | [`states/MagicalGirlWebOracle-idle.png`](states/MagicalGirlWebOracle-idle.png) | Default state reference |
| Success state | [`states/MagicalGirlWebOracle-success.png`](states/MagicalGirlWebOracle-success.png) | Future status indicator |
| Alert state | [`states/MagicalGirlWebOracle-alert.png`](states/MagicalGirlWebOracle-alert.png) | Future status indicator |
| Plated / unplated | [`packaging/`](packaging/) | Platform packaging reference |
| Adaptive foreground / background / composite | [`packaging/`](packaging/) | Android adaptive icon reference |
| 16px LOD | [`lod/MagicalGirlWebOracle-16.png`](lod/MagicalGirlWebOracle-16.png) | Tray / small favicon reference |
| 32px LOD | [`lod/MagicalGirlWebOracle-32.png`](lod/MagicalGirlWebOracle-32.png) | Admin sidebar (30px display) |
| Animated active (GIF / SVG) | [`animated/MagicalGirlWebOracle-active.gif`](animated/MagicalGirlWebOracle-active.gif), [`animated/MagicalGirlWebOracle-active.svg`](animated/MagicalGirlWebOracle-active.svg) | Future loading animation |
| Animated success / alert (SVG) | [`animated/`](animated/) | Future state transitions |
| Multi-size ICO (16–256) | [`ico/MagicalGirlWebOracle.ico`](ico/MagicalGirlWebOracle.ico) | Browser favicon / Windows shortcut |
| Pixel form | [`pixel/MagicalGirlWebOracle.png`](pixel/MagicalGirlWebOracle.png) | Pixel-art / retro variant |
| One UI teardrop form | [`oneui-teardrop/MagicalGirlWebOracle.png`](oneui-teardrop/MagicalGirlWebOracle.png) | Samsung One UI icon mask reference |
| Preview thumbnail | [`preview.webp`](preview.webp) | Fast-loading documentation preview |

## Where it is wired in

- `src/magicalgirl_web_oracle/admin/static/favicon.ico` — admin browser tab.
- `src/magicalgirl_web_oracle/admin/static/icon-512.png` — admin
  apple-touch-icon and PWA reference.
- `frontend/src/assets/icon-32.png` — admin sidebar brand mark.
- `frontend/src/assets/icon-512.png` — admin login panel.
- `frontend/public/favicon.ico` and `frontend/public/icon-512.png` — Vite
  public assets copied into the package at build time.

The remaining theme/state/packaging/animation forms are stored here so they are
available for future UI work and platform packaging.

## Usage

You may use the icon to refer to this project (for example in an article or a
list of MCP servers). Please do not use it in a way that suggests this project
endorses your fork or service. The artwork is distributed under the same MIT
license as the project; see the repository [`LICENSE`](../../LICENSE) and
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).
