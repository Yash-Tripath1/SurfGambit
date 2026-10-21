# SurfGambit — The Two Week Journey of Building My Own Browser 🌐

> *A from-scratch Python browser built by a student who had no idea what he was getting into. This isn't a tutorial. This is just what actually happened.*

---

## Why did I even do this?

Honestly? I saw someone build a research site using Lovable and thought, wait, if people can build websites that easily, what's the hardest thing I could build instead? Somehow my brain landed on "a browser." An actual browser. From scratch.

The rules I set for myself were simple: no Chromium, no WebView, no borrowing someone else's rendering engine. Raw Python. Raw sockets. Everything from scratch. If I was going to build a browser, I was going to actually build one, not just skin someone else's work and call it mine.

The name SurfGambit felt right. It's a gamble. A gambit. You sacrifice something (time, sanity, sleep) hoping it pays off :)

---

## Week 1 — "Wait, how does a browser even work?"

### Day 1-2 — Making the internet talk to me

The first thing a browser needs to do is actually fetch a webpage. Sounds simple. It's not.

I had to write code that opens a raw internet connection (a TCP socket), wraps it in encryption for HTTPS websites, sends a properly formatted request by hand, and reads back whatever the server sends. No shortcuts. No `requests` library. Just the socket.

The first time it actually returned HTML from a real website felt genuinely unreal. Like — I just talked to Google's servers directly. With code I wrote.

Also had to handle stuff like:
- Pages that redirect you to a different URL before showing content (redirect loops are a thing apparently)
- Compressed responses — servers send compressed data to save bandwidth and I had to decompress it on the fly
- Chunked responses — some servers send data in pieces instead of all at once

Files at the end of this: `network.py`, a basic `browser.py` window, and a `parser.py` that could read HTML tags.

### Day 3-4 — Teaching the browser to read HTML

HTML is just text. But turning that text into something a browser can actually understand and display is a whole different problem.

I built an HTML parser — basically a program that reads through HTML character by character, figures out what's a tag vs what's actual text, and builds a tree structure out of it. Like how `<b>hello</b>` means "hello is bold" — the parser has to understand that relationship.

The tricky parts:
- Tags that never close, like `<br>` or `<img>`
- Tags that auto-close other tags (a `<p>` closes the previous `<p>` automatically in HTML)
- Special characters like `&amp;` that need to be decoded into their actual symbols

Then came `layout.py` — teaching the browser where to actually *place* things on screen. Text flows left to right. Elements stack top to bottom. Line wrapping when you hit the edge. Margins and padding. It sounds boring but getting a paragraph to actually wrap correctly took way longer than I expected.

### Day 5-6 — It looks like a real browser now??

Added the actual browser chrome — the UI around the webpage:
- A proper tab bar (multiple tabs that actually work independently)
- Address bar you can type in
- Back and forward buttons with a history stack
- A reload button
- A bookmarks button (⭐) that saves pages to a local JSON file
- `surfgambit://bookmarks` and `surfgambit://history` — internal pages that show your bookmarks and history, rendered by SurfGambit's own engine

The mascot showed up around here too — an animated 8-bit alien (👾) in the toolbar that wiggles. Why? Because why not.

### Day 7 — First LinkedIn post

Posted about it. Got 66 views at 2am. Lesson learned: don't post at 2am.

---

## Week 2 — Actually making it do real things

### Cookie support

Cookies are how websites remember you. Log into something, close the tab, come back — the site still knows who you are because of cookies.

I built a cookie manager that intercepts the "Set-Cookie" headers servers send, saves them locally in a `cookies.json` file, and automatically sends them back when you revisit the same site. Sessions persist across restarts now.

### Images load

Got PNG and GIF images rendering. Had to fetch the image bytes separately (a second network request per image), decode them, and place them in the layout flow with the correct dimensions. JPEGs just show a clean placeholder since the stdlib doesn't handle JPEG natively — deliberate tradeoff, not a bug.

The most annoying bug here: Tkinter silently deletes images from memory if you don't keep a reference to them in Python. So images would load, flash on screen for one second, then vanish. Took way too long to figure out why.

### Form submissions work

You can actually type in search bars and press enter now. This was a big one.

The browser detects `<form>` and `<input>` elements, renders actual text boxes using Tkinter widgets, and when you submit — it grabs all the input values, URL-encodes them, and navigates to the result page. DuckDuckGo searches work properly through SurfGambit's own search bar.

### Text selection + copy

Click and drag on any text, right-click, hit Copy. It works. This sounds basic but implementing it on a raw canvas — where there's no built-in text selection concept — meant tracking mouse coordinates, figuring out which rendered words fall inside the drag box, sorting them in reading order, and joining them back into a string for the clipboard.

### Dark mode with smart contrast

Added a theme toggle. In dark mode, the canvas goes black (#000000). The smart part: if a website has a light background, text stays dark. If it's dark, text goes light. No more invisible black-on-black text.

### The Space Invaders thing

When a page fails to load — network error, site down, whatever — instead of showing a boring error message, SurfGambit launches a fully playable Space Invaders arcade game on the canvas.

Arrow keys to move. Space to shoot. Aliens descend. High score screen. ~33 FPS game loop. The aliens animate through the mascot's pixel art frames.

You can also launch it anytime by clicking the alien mascot in the toolbar.

This was absolutely not planned. It just happened.

### Image search (surfgambit://images)

Typed a keyword, get back a clean gallery of actual photos. Powered by Wikipedia's public image API — no ads, no tracking, no JavaScript. Just results.

### Loading animation

The mascot now goes into "Hyper-Speed Loading Mode" when a page is fetching — wiggling faster and flashing between orange and turquoise until the page loads, then calming back down. Small thing. Makes it feel alive.

---

## What actually works

- Multi-tab browsing
- Raw socket HTTP/HTTPS fetching
- Custom HTML parser and DOM tree
- CSS cascade (tag selectors, class selectors, ID selectors, descendant selectors)
- Block and inline layout with word wrapping
- PNG and GIF images
- Clickable links with relative URL resolution
- Form submission and search bars
- Cookie storage and session persistence
- Bookmarks and history (stored locally)
- Text selection and clipboard copy
- Dark mode with smart contrast
- DuckDuckGo, Google (Lynx spoof), and Wikipedia as search engines
- Built-in image search via Wikipedia API
- Space Invaders offline mode
- Animated loading mascot
- Zoom controls
- Find on Page

## What doesn't work (and why that's fine)

- **JavaScript** — intentional non-goal. A JS engine is basically a second browser-sized project on its own.
- **Modern sites like Google, Twitter, Instagram** — they require JS to even load. DuckDuckGo HTML, Wikipedia, documentation sites, simple HTML pages all work great.
- **Video** — no native video decoding in Python's stdlib without external libraries.
- **Complex CSS layouts** — floats, flexbox, grid — partially supported but not perfect.
- **JPEG images** — stdlib limitation, shows a placeholder instead.

The browser wasn't built to replace Chrome. It was built to understand what Chrome actually does.

---

## What I actually learned

Before this project, "a browser fetches webpages" was the extent of my mental model. After two weeks:

- I know exactly what happens between you typing a URL and pixels appearing on screen
- I understand why CSS specificity works the way it does because I implemented the cascade myself
- I understand why JavaScript is a separate, enormous problem
- I understand why browser compatibility is so hard — every site does slightly wrong HTML and browsers have to handle it gracefully
- I understand why Chrome has hundreds of engineers and billions in funding

---

## What's next

v2 will be built with Electron/WebView, the "easy" way. As a direct comparison. The plan is to write about what changes, what's the same, and what you actually lose when you skip the hard parts.

SurfGambit v1 is done.

---

*Built by Anadi Tripathi (Yash): student, builder, person who thought writing a browser from scratch in two weeks was a reasonable idea *

---

# v2.0 — the rewrite (Oct 2026)

v1 was "a browser that works on simple pages". This is the pass that went
through the whole thing with a critical eye, wrote down every defect, and then
fixed them one by one, testing each fix instead of assuming.

## What was actually broken in v1

An audit of the v1 tree turned up 15 reproducible defects. The worst of them:

| # | Defect | Why it mattered |
| --- | --- | --- |
| 1 | `class="col"` → `RecursionError` | A single class name could hang the tab |
| 2 | `<link rel=stylesheet>` was thrown away | Every styled site lost its CSS |
| 3 | Any site could set `Domain=.com` | Total cookie isolation failure |
| 4 | TLS verification was disabled | No protection against interception |
| 5 | `Expires=not-a-date` raised | One bad header killed the request |
| 6 | 2,000-deep DOM → `RecursionError` | Hostile or generated pages crashed the browser |
| 7 | Passwords submitted in the URL | Leaked into history and logs |
| 8 | `@media` corrupted every rule after it | Most real stylesheets |
| 9 | CSS specificity ignored | `#id` lost to a later `.class` |
| 10 | `">"` inside an attribute shredded the tag | Ordinary `title="a > b"` broke pages |

…plus auto-downloading `.exe` files with silent overwrite, unescaped f-strings
in `surfgambit://` pages, a font-cache bug that underlined every link on the
page, worker threads calling `after()`, and a game whose `bind_all` handlers
outlived the game.

## What v2 changes

The engine moved into two packages — `core/` (no Tk imports, fully testable)
and `ui/` (Tk only). The old module names stay on disk as thin compatibility
shims, so `import parser`, `import layout`, `import network` and friends still
work exactly as before.

- **HTML**: a real state machine — quoted attributes, comments, implied
  `html/head/body`, RAWTEXT/RCDATA, entity decoding, iterative builder that
  survives 20,000 levels of nesting.
- **CSS**: a real tokenizer (strings with braces, nested `@media`/`@supports`,
  `!important`), correct origin/specificity/source order, inheritance,
  shorthand expansion, `em`/`rem`/`%`/`vh`/`vw`, and `<link rel=stylesheet>`
  actually fetched and cascaded.
- **Layout**: block (collapsing margins, `auto` centring, anonymous blocks),
  inline (line boxes, baselines, `justify`, `white-space`), inline-block,
  tables with `colspan`, **flexbox**, and **CSS grid** — Wikipedia's Vector
  skin is a grid, and now it renders as one.
- **Network**: GET *and* POST, verified TLS, gzip/deflate, chunked, redirects
  with method rewriting and per-hop cookies.
- **Cookies**: RFC 6265 — domain match, public-suffix rejection, path
  boundaries, `Max-Age=0`.
- **Security**: PBKDF2-HMAC-SHA256 (600k rounds) for local profiles; stored
  HTTP passwords removed entirely; sanitised download paths; escaped output on
  every internal page.
- **Scripting**: `core/js` — a documented JavaScript subset bound to the real
  DOM, off by default.
- **UI**: menus, real shortcuts, per-tab history, find with match count, zoom
  that re-lays-out, text selection, save page, view source, dev tools, and one
  UI queue so no worker thread ever touches Tk.

## How it was verified

- `python -m tests.run` — 41 headless engine checks, no network, no display.
- A 15-point reproduction harness that returned 15 `[BUG]` on v1 and 0 on v2.
- Live rendering in Xvfb: example.com, text.npr.org, news.ycombinator.com,
  en.wikipedia.org/wiki/Cat, github.com — screenshots compared against v1.

v1's own roadmap ("floats, percentage widths, cookie sessions on restart") is
mostly done; what is still deliberately out of scope is a real JS engine,
`position: absolute/fixed`, floats, transforms and WebP/AVIF decoding.

*Built by Yash Tripathi.*
