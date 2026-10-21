#!/usr/bin/env python3
"""
SurfGambit — a browser built from scratch in Python.

Entry point. The engine lives in `core/` (DOM, CSS, layout, network, storage)
and the user interface in `ui/` (application shell, renderer, internal pages,
game). Nothing outside the standard library is imported anywhere.

    python3 browser.py
    python3 browser.py https://example.com
    python3 browser.py --version
"""

from __future__ import annotations

import os
import sys

VERSION = "1.0"


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv or "-V" in argv:
        print(f"SurfGambit {VERSION}")
        return 0
    if "--help" in argv or "-h" in argv:
        print(__doc__.strip())
        return 0

    try:
        import tkinter  # noqa: F401
    except ImportError:
        return _fail(
            "SurfGambit needs tkinter.\n"
            "  Debian/Ubuntu : sudo apt install python3-tk\n"
            "  Fedora        : sudo dnf install python3-tkinter\n"
            "  macOS/Windows : included with the python.org installer"
        )

    # Make `core` and `ui` importable no matter where the script is launched
    # from (double-clicking the file, a shortcut, `python path/to/browser.py`).
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    from ui.app import BrowserApp

    app = BrowserApp()
    urls = [arg for arg in argv if not arg.startswith("-")]
    if urls:
        tab = app.current_tab()
        if tab is not None:
            target = app.resolve_input(urls[0])
            if target:
                tab.navigate(target, push_history=False)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
