import tkinter as tk
import random
import time
import urllib.parse
from typing import List, Optional

class SpaceInvaderGame:
    def __init__(self, canvas: tk.Canvas, main_browser, alien_mascot):
        self.canvas = canvas
        self.main_browser = main_browser
        self.alien_mascot = alien_mascot
        
        self.game_active = False
        self.game_over = False
        self.game_score = 0
        self.player_x = 300
        self.player_y = 520
        self.lasers: List[List[float]] = []
        self.aliens: List[List[float]] = []
        self.alien_speed = 1.5
        self.alien_spawn_timer = 0

    def start_game(self):
        self.canvas.delete("all")
        self.game_active = True
        self.game_over = False
        self.game_score = 0
        self.player_x = 300
        self.player_y = 520
        self.lasers = []
        self.aliens = []
        self.alien_speed = 1.5
        self.alien_spawn_timer = 0
        
        # Lock viewport color to arcade deep space black
        self.canvas.config(bg="#000000")
        
        # CRUCIAL FOCUS FIX: Set focus to canvas so it immediately captures keyboard events on Windows!
        self.canvas.focus_set()
        
        # Bind keyboard events globally
        self.canvas.bind_all("<Left>", lambda e: self.move_player(-25))
        self.canvas.bind_all("<Right>", lambda e: self.move_player(25))
        self.canvas.bind_all("<space>", lambda e: self.fire_laser())
        self.canvas.bind_all("<Return>", lambda e: self.restart_game())
        
        # Trigger frame tick sequence
        self._game_tick()

    def stop_game(self):
        self.game_active = False
        # Safe unbinding of keyboard controls
        try:
            self.canvas.unbind_all("<Left>")
            self.canvas.unbind_all("<Right>")
            self.canvas.unbind_all("<space>")
            self.canvas.unbind_all("<Return>")
        except Exception:
            pass

    def restart_game(self):
        if self.game_over:
            self.start_game()

    def move_player(self, offset):
        if self.game_active and not self.game_over:
            cw = max(400, self.canvas.winfo_width())
            self.player_x = max(20, min(self.player_x + offset, cw - 20))

    def fire_laser(self):
        if self.game_active and not self.game_over:
            self.lasers.append([self.player_x, self.player_y - 20])

    def _game_tick(self):
        if not self.game_active:
            return
            
        if self.game_over:
            self.canvas.delete("game_el")
            cw = max(400, self.canvas.winfo_width())
            self.canvas.create_text(
                cw/2, 200, text="GAME OVER 👾",
                fill="#ff5722", font=("Arial", 36, "bold"), anchor="center", tags="game_el"
            )
            self.canvas.create_text(
                cw/2, 260, text=f"Final Score: {self.game_score}",
                fill="white", font=("Arial", 18, "bold"), anchor="center", tags="game_el"
            )
            self.canvas.create_text(
                cw/2, 320, text="Press ENTER to Restart",
                fill="#00adb5", font=("Arial", 14, "bold"), anchor="center", tags="game_el"
            )
            return
            
        # 1. Update laser coordinates
        active_lasers = []
        for l in self.lasers:
            l[1] -= 10
            if l[1] > 0:
                active_lasers.append(l)
        self.lasers = active_lasers
        
        # 2. Spawn aliens periodically (~1.2 seconds)
        self.alien_spawn_timer += 1
        if self.alien_spawn_timer >= 35:
            cw = max(400, self.canvas.winfo_width())
            self.aliens.append([random.randint(40, cw - 40), 20, 0])
            self.alien_spawn_timer = 0
            
        # 3. Update alien coordinates and leg animations
        active_aliens = []
        for a in self.aliens:
            a[1] += self.alien_speed
            a[2] = (a[2] + 1) % 4 # cycle 4 frames
            
            # Hit check with player
            if abs(a[0] - self.player_x) < 25 and abs(a[1] - self.player_y) < 25:
                self.game_over = True
                
            if a[1] < 540:
                active_aliens.append(a)
            else:
                self.game_over = True # hit bottom boundary
        self.aliens = active_aliens
        
        # 4. Collision calculations (Laser hits Alien)
        for l in self.lasers:
            for a in self.aliens:
                if abs(l[0] - a[0]) < 20 and abs(l[1] - a[1]) < 20:
                    try: self.lasers.remove(l)
                    except ValueError: pass
                    try: self.aliens.remove(a)
                    except ValueError: pass
                    self.game_score += 10
                    self.alien_speed += 0.04 # speed up difficulty!
                    break
                    
        # 5. Draw frame buffers
        self.canvas.delete("game_el")
        
        # Paint player rocket ship
        px, py = self.player_x, self.player_y
        self.canvas.create_polygon(px, py - 16, px - 12, py + 12, px + 12, py + 12, fill="#4caf50", outline="", tags="game_el")
        self.canvas.create_rectangle(px - 4, py + 12, px + 4, py + 16, fill="#ff5722", outline="", tags="game_el")
        
        # Paint lasers
        for l in self.lasers:
            self.canvas.create_rectangle(l[0] - 2, l[1] - 8, l[0] + 2, l[1], fill="#ff5722", outline="", tags="game_el")
            
        # Paint wiggling alien assets
        for a in self.aliens:
            self._draw_game_alien(a[0], a[1], a[2])
            
        # Score board
        self.canvas.create_text(
            30, 30, text=f"Score: {self.game_score}",
            fill="white", font=("Arial", 12, "bold"), anchor="nw", tags="game_el"
        )
        
        # Call recursively with safe winfo_exists checks on the canvas!
        if self.canvas.winfo_exists():
            self.canvas.after(30, self._game_tick)

    def _draw_game_alien(self, ax, ay, frame_idx):
        if not self.alien_mascot or not self.alien_mascot.winfo_exists():
            return
        sprite = self.alien_mascot.frames[frame_idx]
        p_size = 2 # 2px pixels
        offset_x = ax - 8
        offset_y = ay - 8
        for r, row in enumerate(sprite):
            for c, char in enumerate(row):
                if char == "X":
                    x1 = offset_x + c * p_size
                    y1 = offset_y + r * p_size
                    self.canvas.create_rectangle(x1, y1, x1 + p_size, y1 + p_size, fill="#00adb5", outline="", tags="game_el")
