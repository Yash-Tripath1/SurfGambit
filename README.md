# 🌐 SurfGambit Browser

> A web browser built completely from scratch in Python. No Chromium. No WebView. No borrowed rendering engines. Just raw sockets, a custom HTML/CSS parser, a layout engine, and a pixel painter drawing everything onto a Tkinter canvas.

![Python](https://img.shields.io/badge/Python-3.9+-blue?style=flat-square)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)
![Tests](https://img.shields.io/badge/tests-57%20passing-brightgreen?style=flat-square)

![SurfGambit on text.npr.org](docs/screenshots/npr.png)

<sub>text.npr.org &middot; real TLS, real cascade, real layout. No WebView.</sub>

---

## What is this?

SurfGambit is a desktop web browser that opens real internet connections, parses
HTML character by character, runs a real CSS cascade, solves layout geometry
itself, and paints every pixel on screen through its own rendering pipeline.

It is not fast and it is not Chrome. But every line is written from scratch, it
is standard-library only, and it renders real websites.

---

## What changed in 3.0

3.0 finishes the engine work started in 2.0 and turns SurfScript into something
you can actually build against.

**Performance (Phase 2)**

| Area | 2.0 | 3.0 |
| --- | --- | --- |
| Painting | every box painted, every time | **viewport culling** — only the visible band becomes canvas items. `en.wikipedia.org/wiki/Cat` (23 626 boxes): 41 804 canvas items / 705 ms → ~1 600 items / 30 ms |
| Scrolling | full redraw on every scroll | native Tk scrolling inside a 2.5-screen band, one culled repaint when you leave it |
| HTTP | one request at a time | `ThreadPoolExecutor(8)` for resources + **disk cache** (`<sha1>.meta`/`.body`, 64 MiB, LRU) with `If-None-Match` / `If-Modified-Since` revalidation and `304` handling |
| Subresources | re-downloaded on every visit | served from cache; Reload (`Ctrl+R`) forces a refresh |
| History | JSON write on every visit | debounced on a writer thread, flushed on exit |
| Downloads | buffered in memory | streamed to disk in 64 KiB chunks with progress, cancel and `Content-Length` validation |

**Scripting (Phase 3)** — SurfScript is now a real interpreter, not a DOM-tweak
toy: `if/else`, `while`, `for (;;)`, `for…of`, functions, closures, recursion,
arrays, objects, `&&`/`||`, ternary, compound assignment, `++`/`--`, comments,
string/array methods, `Math`, `JSON`, `parseInt`, and a DOM with
`createElement` / `appendChild` / `classList` / `style` / `addEventListener` /
`querySelectorAll` / bubbling events / inline `onclick` / `setInterval`.
Every script runs inside a step and wall-clock budget, with no network or
filesystem access. Try `surfgambit://demo` (Settings → JavaScript → On).

**Product (Phase 4)**

- **Download manager** (`Ctrl+J`) with live progress, size and a Cancel button
- **DevTools**: JS **console REPL** (evaluates in the page's own context), new
  **Network** tab (every request with status, time, bytes, cache hits), DOM
  inspector and layout dump
- richer context menu: open image, save image, inspect element, copy link
- loading spinner in the status bar, per-tab titles, `docs/ARCHITECTURE.md`
- **57 self-checks** (`python -m tests.run`) and GitHub Actions on Python
  3.9 / 3.11 / 3.12

---

## What changed in 2.0

Version 2.0 is a ground-up rewrite of the engine behind the same UI. The old
single-file modules are kept as thin compatibility layers, so code that imported
`parser`, `layout`, `network`, `cookies`, `accounts`, `css_compiler`,
`surfscript` or `game` keeps working.

**Engine**

| Area | 1.x | 2.0 |
| --- | --- | --- |
| HTML | regex-ish tag scan, `>` inside quotes broke it | spec-shaped state machine: quoted attributes, comments, bogus tags, implied `html/head/body`, RAWTEXT/RCDATA (`<script>`, `<style>`, `<textarea>`, `<title>`), entity decoding, iterative builder safe at 10k+ depth |
| CSS | `([^{]+)\{([^}]+)\}` regex, no `@media` | real tokenizer: comments, strings with braces, nested `@media`/`@supports`, `!important`, correct origin + specificity + source order, inheritance, shorthand expansion (`margin`, `padding`, `border`, `font`, `background`, `text-decoration`, `list-style`), `em`/`rem`/`%`/`vh`/`vw` units |
| External CSS | discarded | `<link rel=stylesheet>` fetched, parsed and cascaded |
| Layout | block + inline only | block (collapsing margins, `auto` centring, `max-width`, anonymous blocks), inline (line boxes, baselines, `text-align: justify`, `white-space: pre/pre-wrap/pre-line`, `white-space: nowrap`), inline-block, tables (colspan, shrink-to-fit), **flexbox** and **CSS grid** |
| Scripting | none | `core/js` — a documented JavaScript subset (`let/const`, arithmetic, `document.getElementById/querySelector/querySelectorAll`, `textContent`, `innerHTML`, `style.*`, `classList`, `console.log`), opt-in in Settings |
| Network | GET only, unverified TLS | GET **and** POST (urlencoded + multipart), verified TLS by default, gzip/deflate, chunked decoding, 10-hop redirects with correct method rewriting, per-hop cookies and Referer, truncation detection |
| Cookies | any site could set `.com` | RFC 6265: domain-match, public-suffix rejection, path boundaries, `Max-Age=0` deletion, `Secure`, atomic persistence |
| Storage | JSON in the working directory | per-user data directory (`%APPDATA%`, `~/Library/Application Support`, `$XDG_DATA_HOME`, or `SURFGAMBIT_DATA_DIR`), atomic writes, corrupt files quarantined |
| Accounts | salted SHA-256, one round | PBKDF2-HMAC-SHA256, 600 000 rounds, constant-time comparison, 0600 file. Stored HTTP passwords removed (session-only) |
| Threading | `after()` called from worker threads | one UI queue drained on the main thread; stale responses dropped by generation counter |
| Downloads | any URL auto-saved as `.exe`, silently overwriting | explicit "Save as", sanitised filenames, `(2)` suffixes, `.part` staging, recorded in `surfgambit://downloads` |

**Browser**

- real menus, Ctrl+T/W/L/R/F/D/U/S, Alt+←/→, F11, F12
- persistent tabs with full back/forward history per tab
- find-in-page with live match count, zoom that actually re-lays-out
- text selection + copy, right-click link menu, "Save page as…", "View source"
- live form controls: text fields, passwords, checkboxes, selects, buttons, `GET` and `POST` submission
- developer tools: DOM tree, layout box dump, script console
- dark, consistent chrome on every platform

---

## Installation

### Run from source (recommended)

**Requirements:** Python 3.9+ with Tk. Nothing to `pip install`.

```bash
git clone https://github.com/Yash-Tripath1/SurfGambit.git
cd SurfGambit
python browser.py
```

Open a URL straight from the command line:

```bash
python browser.py https://news.ycombinator.com
```

If Tk is missing: `sudo apt install python3-tk` (Debian/Ubuntu),
`sudo dnf install python3-tkinter` (Fedora). It ships with the python.org
installers on Windows and macOS.

### Run the .exe (Windows)

Download `SurfGambit.exe` from the releases page and double-click it.

> **Note:** Windows Defender may flag the .exe — it is an unsigned
> PyInstaller binary, which is a known false positive. Build it yourself with
> `pyinstaller SurfGambit.spec` if you prefer.

---

## Project structure

```
SurfGambit/
├── browser.py              # entry point
├── core/                   # engine (no UI imports)
│   ├── dom/                # tokenizer, tree builder, DOM node
│   ├── style/              # CSS tokenizer, selector engine, cascade
│   ├── layout/             # box tree, block, inline, table, flex, grid
│   ├── net/                # HTTPClient, HTTPCache, RFC 6265 CookieJar
│   ├── js/                 # JavaScript subset + DOM bridge
│   ├── accounts.py         # local profiles (PBKDF2)
│   ├── storage.py          # per-user data directory, atomic writes
│   ├── store.py            # settings, bookmarks, history, downloads
│   ├── downloads.py        # download queue
│   └── search.py           # address-bar parsing + image search
├── ui/                     # Tk layer
│   ├── app.py              # window, menus, tabs, devtools, thread pool
│   ├── tab.py              # navigation, loading, painting, script events
│   ├── renderer.py         # canvas painter with viewport culling
│   ├── pages.py            # surfgambit:// pages
│   ├── mascot.py           # the alien
│   └── game.py             # Alien Defender
├── parser.py layout.py network.py cookies.py accounts.py
│   css_compiler.py surfscript.py game.py      # 1.x compatibility shims
└── tests/                  # self-checks (python -m tests.run)
```

Data lives outside the repository:

| OS | Location |
| --- | --- |
| Windows | `%APPDATA%\SurfGambit` |
| macOS | `~/Library/Application Support/SurfGambit` |
| Linux | `$XDG_DATA_HOME/surfgambit` (usually `~/.local/share/surfgambit`) |

Set `SURFGAMBIT_DATA_DIR` to override it.

---

## What it can handle

Works reasonably well:
- text-first sites (Hacker News, text.npr.org, example.com)
- Wikipedia — including the Vector skin's grid layout
- documentation sites and blogs with classic CSS
- GitHub's marketing pages

Still out of scope (deliberate):
- a full JavaScript engine — SurfScript is a documented subset (no `class`,
  `async/await`, regex literals, closures over loops with `var`, or modules)
- `position: absolute/fixed`, `float`, transforms, animations, container queries
- WebP/AVIF decoding (Tk only decodes PNG, GIF, PPM and PGM)
- JavaScript-heavy skins: Wikipedia's Vector 2022 lays out better with
  JavaScript **off** (the default). If a site's scripts rearrange the page,
  View → *Reload without scripts* (`Ctrl+Shift+R`) renders it the plain way.
- video/audio, WebSockets, HTTP/2 (the client speaks HTTP/1.1)

---

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| `Ctrl+T` / `Ctrl+W` | new tab / close tab |
| `Ctrl+L` | focus the address bar |
| `Ctrl+R` / `F5` | reload |
| `Ctrl+F` | find in page |
| `Ctrl+J` | download manager |
| `Ctrl+D` | bookmark this page |
| `Ctrl+U` | view source |
| `Ctrl+S` | save page |
| `Ctrl+` `+` / `-` / `0` | zoom in / out / reset |
| `Alt+←` / `Alt+→` | back / forward |
| `F11` / `F12` | full screen / developer tools |
| `Ctrl+Q` | quit |

---

## Tests

```bash
python -m tests.run            # 57 checks, no network, no display
python -m tests.run js cache   # a subset by group
python -m tests.run --verbose
```

Groups: `dom css layout cookies net cache paint js accounts storage store
downloads shims`. The suite forces a temporary `SURFGAMBIT_DATA_DIR`, so it can
never touch your real profile. `.github/workflows/tests.yml` runs it on Python
3.9, 3.11 and 3.12.

## Architecture

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) walks through the pipeline
(bytes → DOM → cascade → layout → canvas), the threading contract, the HTTP
cache, viewport culling and the SurfScript interpreter.

---

## Screenshots

| | |
| --- | --- |
| ![Wikipedia](docs/screenshots/wikipedia.png) | ![SurfScript demo](docs/screenshots/surfscript-demo.png) |
| Wikipedia's Vector layout, 23 626 boxes, culled to 176 painted | `surfgambit://demo` — counters, timers, DOM building |
| ![DevTools](docs/screenshots/devtools.png) | ![Settings](docs/screenshots/settings.png) |
| Console REPL + network panel + cache stats | Settings, including the JavaScript switch |

---

## Why build this?

To understand how browsers actually work — not at a "it parses HTML" level, but
at a "here is exactly how a raw TCP socket turns into pixels on a screen" level.

---

## Built with

- Python 3 standard library only: `socket`, `ssl`, `tkinter`, `gzip`, `zlib`,
  `hashlib`, `threading`, `urllib.parse`
- Zero external dependencies

---

## License

MIT — see [LICENSE](LICENSE).

*Made by Yash Tripathi*
