# SurfGambit: The Two Week Journey of Building My Own Browser 🌐

> *A from-scratch Python browser built by a student who had no idea what he was getting into. This isn't a tutorial. This is just what actually happened.*

---

## Why did I even do this?

Honestly? I saw someone build a research site using Lovable and thought, wait, if people can build websites that easily, what's the hardest thing I could build instead? Somehow my brain landed on "a browser." An actual browser. From scratch.

The rules I set for myself were simple: no Chromium, no WebView, no borrowing someone else's rendering engine. Raw Python. Raw sockets. Everything from scratch. If I was going to build a browser, I was going to actually build one, not just skin someone else's work and call it mine.

The name SurfGambit felt right. It's a gamble. A gambit. You sacrifice something (time, sanity, sleep) hoping it pays off :)

---

## Week 1: "Wait, how does a browser even work?"

### Day 1-2: Making the internet talk to me

The first thing a browser needs to do is actually fetch a webpage. Sounds simple. It's not.

I had to write code that opens a raw internet connection (a TCP socket), wraps it in encryption for HTTPS websites, sends a properly formatted request by hand, and reads back whatever the server sends. No shortcuts. No `requests` library. Just the socket.

The first time it actually returned HTML from a real website felt genuinely unreal. Like, I just talked to Google's servers directly. With code I wrote.

Also had to handle stuff like:
- Pages that redirect you to a different URL before showing content (redirect loops are a thing apparently)
- Compressed responses: servers send compressed data to save bandwidth and I had to decompress it on the fly
- Chunked responses: some servers send data in pieces instead of all at once

Files at the end of this: `network.py`, a basic `browser.py` window, and a `parser.py` that could read HTML tags.

### Day 3-4: Teaching the browser to read HTML

HTML is just text. But turning that text into something a browser can actually understand and display is a whole different problem.

I built an HTML parser, basically a program that reads through HTML character by character, figures out what's a tag vs what's actual text, and builds a tree structure out of it. Like how `<b>hello</b>` means "hello is bold"; the parser has to understand that relationship.

The tricky parts:
- Tags that never close, like `<br>` or `<img>`
- Tags that auto-close other tags (a `<p>` closes the previous `<p>` automatically in HTML)
- Special characters like `&amp;` that need to be decoded into their actual symbols

Then came `layout.py`, teaching the browser where to actually *place* things on screen. Text flows left to right. Elements stack top to bottom. Line wrapping when you hit the edge. Margins and padding. It sounds boring but getting a paragraph to actually wrap correctly took way longer than I expected.

### Day 5-6: It looks like a real browser now??

Added the actual browser chrome, the UI around the webpage:
- A proper tab bar (multiple tabs that actually work independently)
- Address bar you can type in
- Back and forward buttons with a history stack
- A reload button
- A bookmarks button (⭐) that saves pages to a local JSON file
- `surfgambit://bookmarks` and `surfgambit://history`: internal pages that show your bookmarks and history, rendered by SurfGambit's own engine

The mascot showed up around here too: an animated 8-bit alien (👾) in the toolbar that wiggles. Why? Because why not.

### Day 7: First LinkedIn post

Posted about it. Got 66 views at 2am. Lesson learned: don't post at 2am.

---

## Week 2: Actually making it do real things

### Cookie support

Cookies are how websites remember you. Log into something, close the tab, come back, and the site still knows who you are because of cookies.

I built a cookie manager that intercepts the "Set-Cookie" headers servers send, saves them locally in a `cookies.json` file, and automatically sends them back when you revisit the same site. Sessions persist across restarts now.

### Images load

Got PNG and GIF images rendering. Had to fetch the image bytes separately (a second network request per image), decode them, and place them in the layout flow with the correct dimensions. JPEGs showed a clean placeholder back then, because the standard library has no JPEG decoder. That was a deliberate tradeoff, not a bug; the rewrite below decodes them properly.

The most annoying bug here: Tkinter silently deletes images from memory if you don't keep a reference to them in Python. So images would load, flash on screen for one second, then vanish. Took way too long to figure out why.

### Form submissions work

You can actually type in search bars and press enter now. This was a big one.

The browser detects `<form>` and `<input>` elements, renders actual text boxes using Tkinter widgets, and when you submit, it grabs all the input values, URL-encodes them, and navigates to the result page. DuckDuckGo searches work properly through SurfGambit's own search bar.

### Text selection + copy

Click and drag on any text, right-click, hit Copy. It works. This sounds basic but implementing it on a raw canvas, where there's no built-in text selection concept, meant tracking mouse coordinates, figuring out which rendered words fall inside the drag box, sorting them in reading order, and joining them back into a string for the clipboard.

### Dark mode with smart contrast

Added a theme toggle. In dark mode, the canvas goes black (#000000). The smart part: if a website has a light background, text stays dark. If it's dark, text goes light. No more invisible black-on-black text.

### The Space Invaders thing

When a page fails to load (network error, site down, whatever), instead of showing a boring error message, SurfGambit launches a fully playable Space Invaders arcade game on the canvas.

Arrow keys to move. Space to shoot. Aliens descend. High score screen. ~33 FPS game loop. The aliens animate through the mascot's pixel art frames.

You can also launch it anytime by clicking the alien mascot in the toolbar.

This was absolutely not planned. It just happened.

### Image search (surfgambit://images)

Typed a keyword, get back a clean gallery of actual photos. Powered by Wikipedia's public image API: no ads, no tracking, no JavaScript. Just results.

### Loading animation

The mascot now goes into "Hyper-Speed Loading Mode" when a page is fetching: wiggling faster and flashing between orange and turquoise until the page loads, then calming back down. Small thing. Makes it feel alive.

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

- **JavaScript**: intentional non-goal. A JS engine is basically a second browser-sized project on its own.
- **Modern sites like Google, Twitter, Instagram**: they require JS to even load. DuckDuckGo HTML, Wikipedia, documentation sites, simple HTML pages all work great.
- **Video**: no native video decoding in Python's stdlib without external libraries.
- **Complex CSS layouts**: flexbox and grid are supported; floats and transforms are not.
- **JPEG images**: the standard library had no decoder, so these showed a placeholder. The rewrite below added one.

The browser wasn't built to replace Chrome. It was built to understand what Chrome actually does.

---

## What I actually learned

Before this project, "a browser fetches webpages" was the extent of my mental model. After two weeks:

- I know exactly what happens between you typing a URL and pixels appearing on screen
- I understand why CSS specificity works the way it does because I implemented the cascade myself
- I understand why JavaScript is a separate, enormous problem
- I understand why browser compatibility is so hard; every site does slightly wrong HTML and browsers have to handle it gracefully
- I understand why Chrome has hundreds of engineers and billions in funding

---

## What's next

The obvious next experiment is the opposite one: build the same browser with
Electron/WebView, the "easy" way, and write about what changes, what stays the
same, and what you actually lose when you skip the hard parts.

For now the browser you can run is the one described below: still from
scratch, still nothing borrowed.

---

*Built by Anadi Tripathi: student, builder, person who thought writing a browser from scratch in two weeks was a reasonable idea.*

---

# The rewrite

Writing a browser that works on simple pages is one thing. Going back through it
with a critical eye, writing down every defect and fixing them one at a time
(testing each fix instead of assuming) is the part that actually taught me how
the engine works.

## What was actually broken

An audit of the first tree turned up 15 reproducible defects. The worst of them:

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

## What changed

The engine moved into two packages: `core/` (no Tk imports, fully testable)
and `ui/` (Tk only). The old module names stay on disk as thin compatibility
shims, so `import parser`, `import layout`, `import network` and friends still
work exactly as before.

- **HTML**: a real state machine with quoted attributes, comments, implied
  `html/head/body`, RAWTEXT/RCDATA, entity decoding, an iterative builder that
  survives 20,000 levels of nesting.
- **CSS**: a real tokenizer (strings with braces, nested `@media`/`@supports`,
  `!important`), correct origin/specificity/source order, inheritance,
  shorthand expansion, `em`/`rem`/`%`/`vh`/`vw`, and `<link rel=stylesheet>`
  actually fetched and cascaded.
- **Layout**: block (collapsing margins, `auto` centring, anonymous blocks),
  inline (line boxes, baselines, `justify`, `white-space`), inline-block,
  tables with `colspan`, flexbox, and CSS grid. Wikipedia's Vector skin is a
  grid, and now it renders as one.
- **Network**: GET *and* POST, verified TLS, gzip/deflate, chunked, redirects
  with method rewriting and per-hop cookies.
- **Cookies**: RFC 6265, with domain match, public-suffix rejection, path
  boundaries, `Max-Age=0`.
- **Security**: PBKDF2-HMAC-SHA256 (600k rounds) for local profiles; stored
  HTTP passwords removed entirely; sanitised download paths; escaped output on
  every internal page.
- **Scripting**: `core/js`, a JavaScript subset bound to the real DOM, off by
  default.
- **UI**: menus, real shortcuts, per-tab history, find with match count, zoom
  that re-lays-out, text selection, save page, view source, dev tools, and one
  UI queue so no worker thread ever touches Tk.

## Then the parts that were still on paper

Three things had been written down and not built: speed, scripting, and the
small product details that make a browser feel finished.

**Speed.** The honest measurement: opening `en.wikipedia.org/wiki/Cat` produced
23 626 layout boxes, 41 804 canvas items and a 705 ms paint, and every scroll
step paid for all of it. The renderer now takes a viewport band and paints only
what intersects it, the tab keeps 2.5 screens of slack so normal scrolling is
native Tk scrolling, and leaving the band triggers one culled repaint: about
30 ms and 1 600 items. Same page, 23× fewer canvas items. Around that: a real
thread pool instead of a thread per image, an on-disk HTTP cache with
`If-None-Match` revalidation, streamed downloads with `Content-Length`
validation, and history writes moved off the UI thread.

**Scripting.** The first interpreter understood `let`, arithmetic and
`document.getElementById`. It could not run a `for` loop. It is now a lexer,
recursive-descent parser and tree-walking interpreter for a documented subset:
loops, functions, closures, recursion, arrays, objects, ternary, logical and
bitwise operators, regular expressions, `new`, `Date`, `Math`, `JSON`,
`setTimeout`/`setInterval`, plus a DOM with `createElement`, `appendChild`,
`classList`, `style`, `addEventListener`, bubbling events and inline `onclick`.
Every script runs with a step and wall-clock budget and no access to the network
or the filesystem. `surfgambit://demo` exercises all of it, and you can click it.

**Product.** Download manager with progress and cancel, a JavaScript console
that evaluates in the page's own context, a network panel that lists every
request and cache hit, save-image and inspect-element in the context menu, a
loading spinner, a test suite that runs without a screen, and continuous
integration.

The lesson the performance work taught: culling is the whole game. A canvas can
hold a page of any length if you stop pretending it has to hold all of it at
once.

*Built by Anadi Tripathi.*
