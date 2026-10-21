"""
ui/game.py — Alien Defender, the offline easter egg (surfgambit://game).

Compared with v1's game.py:

* bindings are scoped to the canvas and removed on exit, instead of
  `bind_all` handlers that kept firing (and kept firing twice) after the game
  was closed
* the animation loop is a single `after` job that is cancelled in `stop()`,
  so leaving the page does not leak a 60 Hz callback that repaints a dead canvas
* Escape leaves the game and returns to the welcome page
"""

from __future__ import annotations

import random
import tkinter as tk
from typing import List, Optional

WIDTH = 820
HEIGHT = 560
TICK_MS = 16
TAG = "gameitem"

SPRITES = [
    ["  X  X  ", " X XXX X", "XXXXXXXX", "XX XXX XX", "XXXXXXXX", "  XXXX  ",
     " X    X ", "X      X"],
    ["  X  X  ", " X XXX X", "XXXXXXXX", "XX XXX XX", "XXXXXXXX", " XXXXXX ",
     "X      X", " X    X "],
]


class SpaceInvaders:
    def __init__(self, canvas: tk.Canvas, app) -> None:
        self.canvas = canvas
        self.app = app
        self._job: Optional[str] = None
        self._bindings: List[tuple] = []
        self.running = False

        self.player_x = WIDTH / 2
        self.player_y = HEIGHT - 40
        self.lasers: List[list] = []
        self.aliens: List[list] = []
        self.score = 0
        self.lives = 3
        self.over = False
        self.frame = 0
        self.spawn_timer = 0
        self.enemy_speed = 1.1

    # ------------------------------------------------------------------
    def start(self) -> None:
        self.canvas.delete("all")
        try:
            self.canvas.config(bg="#050507", scrollregion=(0, 0, WIDTH, HEIGHT))
        except tk.TclError:
            pass
        self.running = True
        self._bind("<Left>", lambda e: self.move(-26))
        self._bind("<Right>", lambda e: self.move(26))
        self._bind("<a>", lambda e: self.move(-26))
        self._bind("<d>", lambda e: self.move(26))
        self._bind("<space>", lambda e: self.fire())
        self._bind("<Return>", lambda e: self.restart())
        self._bind("<Escape>", lambda e: self.quit())
        self.canvas.focus_set()
        self._tick()

    def _bind(self, sequence: str, handler) -> None:
        self.canvas.bind(sequence, handler)
        self._bindings.append((sequence, handler))

    def stop(self) -> None:
        self.running = False
        if self._job is not None:
            try:
                self.canvas.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        for sequence, _handler in self._bindings:
            try:
                self.canvas.unbind(sequence)
            except Exception:
                pass
        self._bindings = []
        try:
            self.canvas.delete(TAG)
        except Exception:
            pass

    def quit(self) -> None:
        self.stop()
        tab = self.app.current_tab()
        if tab is not None:
            tab.navigate("surfgambit://welcome", push_history=False)

    # ------------------------------------------------------------------
    def restart(self) -> None:
        if not self.over:
            return
        self.score = 0
        self.lives = 3
        self.over = False
        self.lasers = []
        self.aliens = []
        self.spawn_timer = 0
        self.enemy_speed = 1.1
        self.player_x = WIDTH / 2

    def move(self, delta: float) -> None:
        if not self.over:
            self.player_x = max(30, min(WIDTH - 30, self.player_x + delta))

    def fire(self) -> None:
        if not self.over and len(self.lasers) < 4:
            self.lasers.append([self.player_x, self.player_y - 18])

    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if not self.running:
            return
        if not self.over:
            self._step()
        self._draw()
        self._job = self.canvas.after(TICK_MS, self._tick)

    def _step(self) -> None:
        self.frame += 1
        self.spawn_timer += 1
        if self.spawn_timer > max(24, 70 - self.score // 40):
            self.spawn_timer = 0
            self.aliens.append([
                random.choice([60, 160, 260, 360, 460, 560, 660, 760]),
                -20.0,
                random.uniform(0.6, 1.4) + self.enemy_speed * 0.2,
            ])

        for laser in list(self.lasers):
            laser[1] -= 11
            if laser[1] < -10:
                self.lasers.remove(laser)

        for alien in list(self.aliens):
            alien[1] += alien[2] + self.enemy_speed * 0.15
            if alien[1] > HEIGHT - 20:
                self.aliens.remove(alien)
                self._lose_life()
                continue
            for laser in list(self.lasers):
                if abs(laser[0] - alien[0]) < 34 and abs(laser[1] - alien[1]) < 22:
                    self.aliens.remove(alien)
                    self.lasers.remove(laser)
                    self.score += 10
                    self.enemy_speed = min(3.2, 1.1 + self.score / 900)
                    break

    def _lose_life(self) -> None:
        self.lives -= 1
        if self.lives <= 0:
            self.over = True

    # ------------------------------------------------------------------
    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete(TAG)

        def item(kind, *args, **kwargs):
            kwargs["tags"] = TAG
            getattr(canvas, kind)(*args, **kwargs)

        item("create_text", WIDTH / 2, 22, text="ALIEN DEFENDER", fill="#00adb5",
             font=("Courier", 16, "bold"))
        item("create_text", 18, 22, text=f"SCORE {self.score:05d}", anchor="w",
             fill="#e6e6e6", font=("Courier", 12), tags=TAG)
        item("create_text", WIDTH - 18, 22, text="\u2665 " * max(0, self.lives),
             anchor="e", fill="#ff5f6d", font=("Courier", 12), tags=TAG)
        item("create_line", 0, 38, WIDTH, 38, fill="#1d2b2d")

        sprite = SPRITES[(self.frame // 12) % 2]
        for x, y, _speed in self.aliens:
            self._draw_sprite(sprite, x - 16, y - 16, 4, "#ff7043")

        for x, y in self.lasers:
            item("create_line", x, y, x, y + 12, fill="#00e5ff", width=3)

        # player
        item("create_polygon", self.player_x, self.player_y - 16,
             self.player_x - 18, self.player_y + 12,
             self.player_x + 18, self.player_y + 12,
             fill="#00adb5", outline="")
        item("create_rectangle", self.player_x - 4, self.player_y + 4,
             self.player_x + 4, self.player_y + 14, fill="#00adb5", outline="")

        if self.over:
            item("create_text", WIDTH / 2, HEIGHT / 2 - 10, text="GAME OVER",
                 fill="#ff5f6d", font=("Courier", 34, "bold"))
            item("create_text", WIDTH / 2, HEIGHT / 2 + 34,
                 text=f"score {self.score}  \u00b7  press Return to play again",
                 fill="#e6e6e6", font=("Courier", 13))
        item("create_text", WIDTH / 2, HEIGHT - 12,
             text="arrows / A D to move    space to fire    Esc to leave",
             fill="#4d5a5c", font=("Courier", 10))

    def _draw_sprite(self, rows: List[str], x0: float, y0: float, pixel: int,
                     colour: str) -> None:
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                if cell != "X":
                    continue
                x = x0 + c * pixel
                y = y0 + r * pixel
                self.canvas.create_rectangle(x, y, x + pixel, y + pixel,
                                             fill=colour, outline="", tags=TAG)
