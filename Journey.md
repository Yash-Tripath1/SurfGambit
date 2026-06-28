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
