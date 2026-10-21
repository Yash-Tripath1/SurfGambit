# SurfGambit architecture

SurfGambit is a browser engine written in the standard library: raw sockets, a
hand-written HTML tokenizer, a real CSS cascade, a box-model layout engine, a
Tk canvas painter and a small JavaScript interpreter. Nothing in `core/` imports
Tk; nothing in `ui/` re-implements engine rules.

```
browser.py                entry point (creates the app)
│
├── core/                 the engine: no Tk, no UI, unit-testable
│   ├── net/              client.py  cache.py  cookies.py
│   ├── dom/              tokenizer.py  tree.py  entities.py  parser.py
│   ├── style/            css.py  selectors.py  cascade.py  values.py
│   ├── layout/           box.py  block.py  inline.py  table.py  flex.py  grid.py  text.py
│   ├── image/            __init__.py (pipeline)  jpeg.py  png.py  bmp.py
│   ├── js/               surfscript.py (language)  dom_bridge.py (DOM + events)
│   ├── store.py          settings / bookmarks / history / downloads
│   ├── storage.py        atomic JSON, per-OS data directory
│   ├── downloads.py      queue, streaming, cancellation
│   ├── accounts.py       PBKDF2 credentials
│   └── search.py         address-bar parsing, Bing/Wikipedia results, image search
│
├── ui/                   everything that touches Tk
│   ├── app.py            window, menus, toolbar, threading, devtools
│   ├── tab.py            one browsing context: load, layout, paint, scroll
│   ├── renderer.py       layout tree → canvas items (culled)
│   ├── pages.py          the surfgambit:// internal pages
│   ├── mascot.py         the alien on the toolbar and start page
│   └── game.py           Alien Defender (surfgambit://game)
│
├── tests/run.py          self-checks (`python -m tests.run`)
└── docs/                 this file, screenshots
```

## Data flow

```
URL ──► HTTPClient ──► HTML text ──► tokenizer ──► DOM (core.dom.tree.HTMLNode)
                          │                            │
                          │ <link rel=stylesheet>      ▼
                          └──────────────► CSSParser ─► cascade (specificity,
                                                         inheritance, @media)
                                                       │
                                                       ▼
                                       compute_layout ─► LayoutBox tree
                                                       │   (block / inline /
                                                       │    table / flex / grid)
                                                       ▼
                                       Renderer (viewport band) ─► Tk canvas items
                                                       │
                                                       └─► form widgets (place)
```

Three rules keep this honest:

1. **Style is computed once per element**, into `node.style`, and layout only
   reads it. `em`, `%` and `vh/vw` are resolved with the viewport that was in
   force when the style was computed.
2. **Layout never paints.** A box knows its rect and its line boxes; the
   renderer decides what becomes a canvas item.
3. **Painting is disposable.** The canvas can be cleared and rebuilt at any
   time from the layout tree. That is what makes culling and resizing safe.

## Threading

Only the main thread touches Tk.

* `BrowserApp.submit(fn)` runs `fn` on a shared `ThreadPoolExecutor(8)`.
* Worker threads never call Tk; they post results with `app.ui(fn)`, which puts
  the callable on a `queue.Queue`.
* `_pump()` drains that queue on the main thread every 25 ms (max 60 callbacks
  per tick, so a burst cannot freeze the UI).
* Every load carries a `generation` number. A result whose generation is stale
  is dropped, which is how navigating away cancels in-flight work.

History writes are debounced: `Store.record_visit()` updates memory and arms a
0.8 s timer; the JSON write happens on that timer's thread. `Store.flush()` on
shutdown guarantees the last visit lands on disk.

## The HTTP cache (Phase 2)

`core/net/cache.py` is a two-level cache:

* **memory**: `OrderedDict` LRU keyed by URL (300 entries);
* **disk**: `<data>/cache/http/<sha1>.meta|.body`, atomic writes, evicted
  oldest-first when the directory passes 64 MiB or 800 entries.

Freshness follows the useful subset of RFC 9111: `Cache-Control: max-age`,
`Expires`, then a `Last-Modified` heuristic (10 % of document age, capped at a
day). Anything without metadata is stored *stale*, so the next navigation sends
`If-None-Match` / `If-Modified-Since`; a `304` refreshes the entry and the body
comes from disk. `force_refresh=True` (Reload) bypasses the lookup entirely.
`no-store` responses never touch the disk.

Every request is appended to `client.log` as a `RequestRecord`
(method, URL, status, ms, bytes, cached), which is what the devtools **Network**
tab renders.

## Images

`core/image` turns fetched bytes into something the canvas can draw, and it
owns the pixels rather than handing them to Tk:

| format | decoder | notes |
| --- | --- | --- |
| JPEG | `jpeg.py` | baseline + progressive, restart markers, CMYK, 1/2/4/8 scaled IDCT |
| PNG  | `png.py` | colour types 0/2/3/4/6, 1-16 bit, tRNS, Adam7, every filter |
| BMP  | `bmp.py` | uncompressed 24/32-bit, top-down and bottom-up |
| GIF  | Tk | passed through untouched |

`decode_for_display(data, max_dim)` sniffs the magic bytes (never the URL),
applies EXIF orientation, caps the pixel size, and returns an `ImageInfo` with
PNG bytes for `PhotoImage` plus the packed RGB(A) buffer. The tab fetches and
decodes on a worker thread, stores the result under the absolute URL, and the
renderer asks `get_image_for(url, w, h)` for a photo of exactly the CSS box;
`resize_rgb()` + `png_from_rgb()` do the scaling, so an image never spills into
its neighbours or leaves a letterbox gap. Both caches are LRU-bounded.

Accuracy against Pillow on the reference corpus: mean absolute error 0.02
(grey), 0.40 (4:4:4), 0.55 (photo), 5.70 (4:2:0, chroma smoothing), i.e. the
same picture to the eye. A 1280×800 photo decodes in 0.6 s at half scale.

## Search

The address bar does not load a search engine's page unless it can be read.
`core/search.py` fetches Bing's results HTML and extracts `b_algo` blocks
(unwrapping `/ck/a` redirects, stripping breadcrumb decorations), topping the
list up from the Wikipedia API when Bing returns too few. Google, DuckDuckGo,
Startpage and others answer plain HTTP clients with JavaScript shells or
captchas; `page_looks_blocked()` and the link-count check in `ui/tab.py`
recognise both and reroute to `surfgambit://search?q=…`, which renders results
with the engine itself. `surfgambit://images?q=…` does the same for Wikimedia
Commons thumbnails in a responsive card grid.

## Painting and culling (Phase 2)

A 40 000 px page has ~24 000 layout boxes. Turning all of them into Tk canvas
items costs ~700 ms and ~42 000 items, and every scroll step pays for it.

`Renderer.render(..., viewport=(top, bottom))` skips:
* boxes whose vertical extent does not intersect the band,
* inline items (words, images, widgets) outside it.

The tab keeps a band of 0.75 screens above and 1.75 below the viewport. Scrolling
inside the band is native Tk scrolling (no work at all); leaving it triggers one
culled repaint, debounced to 30 ms.

Measured on `en.wikipedia.org/wiki/Cat` (23 626 boxes):

| | items created | paint time |
|---|---:|---:|
| full page, no culling | 41 804 | 705 ms |
| culled band | ~1 600 | 30 ms |

Search hits are counted in a separate pass over the whole tree, so "12 matches"
stays correct even though only the visible slice is painted.

## SurfScript (Phase 3)

`core/js/surfscript.py` is the language; `core/js/dom_bridge.py` is the DOM.

* **Lexer**: hand-written scanner: numbers, strings with escapes, identifiers,
  `//` and `/* */` comments, longest-match operators.
* **Parser**: recursive descent with the usual precedence ladder:
  assignment → ternary → `||` → `&&` → equality → relational → additive →
  multiplicative → unary → postfix → call/member/index → primary.
  Statements: `let/const/var`, `if/else`, `while`, `do`, `for (;;)`,
  `for (x of y)`, `function`, `return`, `break`, `continue`, blocks.
* **Interpreter**: tree-walking evaluator with lexical `Scope` chains,
  closures, `this`, `arguments`, a shared `BoundMethod` for prototype calls, and
  Python-implemented prototypes for strings, arrays, numbers and objects.
* **Budget**: 300 000 steps and 1.5 s wall clock per script. A runaway `while
  (true)` raises `ScriptError` and is reported in the console panel; it never
  hangs the browser.
* **DOM**: `getElementById`, `querySelector(All)` (backed by the same selector
  engine the cascade uses), `createElement`, `createTextNode`, `appendChild`,
  `removeChild`, `insertBefore`, `classList`, `style` (camelCase → kebab-case),
  `textContent` / `innerText` / `innerHTML`, attributes, `dataset`, `children`,
  sibling/parent traversal, `addEventListener` / `removeEventListener`,
  `dispatchEvent`, `click()`.
* **Events**: bubbling from the target to `<html>`, `preventDefault()` /
  `stopPropagation()`, plus inline `onclick="…"` attributes with the element
  bound to `this`.
* **Timers**: `setTimeout` / `setInterval` / `clearTimeout`. The tab schedules
  them with Tk `after`, so they survive across repaints and stop on navigation.
* **Sandbox**: no network, no filesystem, no `eval`, no `Function`. A page can
  only reach the DOM it owns.

Scripts are **opt-in** (Settings → JavaScript). When the setting is off, the
same pages render and `<script>` is ignored.

## Testing

```
python -m tests.run              # no network, no display
python -m tests.run js paint     # one or more groups
python -m tests.run --verbose
```

Groups: `dom css layout cookies net netcache paint js accounts storage store
downloads shims`. Every check forces a temporary `SURFGAMBIT_DATA_DIR`, so
running the suite can never touch your real profile. CI runs the same command on
Python 3.9 / 3.11 / 3.12 (`.github/workflows/tests.yml`).

## Known limits

No `position: absolute/fixed`, no `float`, no transforms or animations, no
WebP/AVIF (Tk decodes PNG/GIF/PPM), no audio/video, no WebSockets, no HTTP/2, no
`<iframe>`, and SurfScript is a documented subset rather than a JS engine. The
README says the same thing in fewer words. The point is that the limits are
declared, not discovered.
