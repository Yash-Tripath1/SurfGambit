"""
ui/mascot.py: the little alien from the welcome page, on the toolbar.

The animation reads its state from a callback instead of the settings file:
legacy re-read settings from disk inside every animation tick (four times a
second, forever), which is both slow and a good way to see torn JSON.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, List, Optional

FRAMES: List[List[str]] = [
    [
        "  X  X  ",
        " X XXX X",
        "XXXXXXXX",
        "XX XXX XX",
        "XXXXXXXX",
        "  XXXX  ",
        " X    X ",
        "X      X",
    ],
    [
        "  X  X  ",
        " X XXX X",
        "XXXXXXXX",
        "XX XXX XX",
        "XXXXXXXX",
        " XXXXXX ",
        "X      X",
        " X    X ",
    ],
    [
        "X X  X X",
        " X XXX X",
        "XXXXXXXX",
        "XX XXX XX",
        "XXXXXXXX",
        "  XXXX  ",
        " X    X ",
        "X      X",
    ],
    [
        "  X  X  ",
        "X XXXXXX",
        "XXXXXXXX",
        "X XXXX X",
        "XXXXXXXX",
        "  XXXX  ",
        " X    X ",
        "  X  X  ",
    ],
]

ACCENT = "#00adb5"
BUSY = "#ff5722"


class Mascot(tk.Canvas):
    def __init__(self, parent: tk.Widget, size: int = 26,
                 bg: str = "#24242a",
                 is_busy: Optional[Callable[[], bool]] = None,
                 is_enabled: Optional[Callable[[], bool]] = None,
                 on_hover: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0)
        self.size = size
        self.is_busy = is_busy or (lambda: False)
        self.is_enabled = is_enabled or (lambda: True)
        self.on_hover = on_hover
        self.frame = 0
        self._job = None
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self._job = self.after(120, self._tick)

    def _enter(self, _event=None) -> None:
        if self.on_hover:
            self.on_hover("\U0001F47E  we wa wu!")

    def _leave(self, _event=None) -> None:
        if self.on_hover:
            self.on_hover(None)

    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        busy = bool(self.is_busy())
        delay = 90 if busy else 260
        self.delete("all")
        if self.is_enabled():
            colour = BUSY if (busy and self.frame % 2 == 0) else ACCENT
            self._draw(FRAMES[self.frame % len(FRAMES)], colour)
            self.frame += 1
        self._job = self.after(delay, self._tick)

    def _draw(self, frame: List[str], colour: str) -> None:
        pixel = self.size / 8.0
        for row_index, row in enumerate(frame):
            for col_index, cell in enumerate(row):
                if cell != "X":
                    continue
                x1 = col_index * pixel
                y1 = row_index * pixel
                self.create_rectangle(x1, y1, x1 + pixel + 0.5, y1 + pixel + 0.5,
                                      fill=colour, outline="")

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
