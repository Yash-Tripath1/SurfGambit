import tkinter as tk
import time
from typing import List, Tuple, Optional

# ============================================================
# SurfGambit Custom Video (.sgv) Specification
# ============================================================
# An .sgv file represents a custom-decoded, lightweight 8-bit
# animation vector stream. It contains a list of frames, where
# each frame is a grid of pixel characters (color mappings).
# This allows real-time, hardware-independent retro video playback
# completely from scratch inside our Tkinter canvas at 24fps.

class SGVVideo:
    def __init__(self, title: str, fps: int = 15):
        self.title = title
        self.fps = fps
        self.frames: List[List[str]] = []
        self.colors: List[str] = ["#000000", "#00adb5", "#ff5722", "#4caf50", "#ffffff"]

    def add_frame(self, grid: List[str]):
        # Each string in grid is a row of color index characters (0-9)
        self.frames.append(grid)

# Create a beautiful built-in short movie: "Rocket Space Launch"
# 0 = Black, 1 = Turquoise, 2 = Orange, 3 = Green, 4 = White
LAUNCH_MOVIE = SGVVideo("SurfGambit Rocket Launch", fps=12)

# Frame templates representing a rocket rising and flames bursting!
for step in range(24):
    offset = step % 8
    flame_frame = step % 3
    # Build grid strings
    grid = []
    for r in range(12):
        row_chars = ["0"] * 16
        
        # Rocket Body (Y position moves up as step increases)
        ry = r + (step // 2) - 4
        if 0 <= ry < 12:
            # Nose cone
            if ry == 2:
                row_chars[7] = "1"
                row_chars[8] = "1"
            # Cockpit/window
            elif ry == 3:
                row_chars[6] = "1"
                row_chars[7] = "4"
                row_chars[8] = "4"
                row_chars[9] = "1"
            # Body tube
            elif ry in (4, 5, 6, 7):
                row_chars[6] = "1"
                row_chars[7] = "1"
                row_chars[8] = "1"
                row_chars[9] = "1"
                # Fins
                if ry == 7:
                    row_chars[5] = "2"
                    row_chars[10] = "2"
            # Flame propulsion (cycles sizes)
            elif ry == 8:
                if flame_frame == 0:
                    row_chars[7] = "2"
                    row_chars[8] = "2"
                elif flame_frame == 1:
                    row_chars[6] = "2"
                    row_chars[7] = "2"
                    row_chars[8] = "2"
                    row_chars[9] = "2"
                else:
                    row_chars[7] = "2"
                    row_chars[8] = "2"
                    
            elif ry == 9 and flame_frame != 0:
                row_chars[7] = "2"
                row_chars[8] = "2"
                
        # Clouds passing down the screen
        cy = (r - step) % 12
        if cy in (0, 1) and r != ry:
            row_chars[2] = "4"
            row_chars[3] = "4"
        if cy in (5, 6) and r != ry:
            row_chars[12] = "4"
            row_chars[13] = "4"
            
        grid.append("".join(row_chars))
    LAUNCH_MOVIE.add_frame(grid)


class SGVPlayer:
    def __init__(self, canvas: tk.Canvas, video: SGVVideo, x: int = 150, y: int = 100, scale: int = 20):
        self.canvas = canvas
        self.video = video
        self.x = x
        self.y = y
        self.scale = scale
        
        self.current_frame_idx = 0
        self.is_playing = False
        self.loop_id: Optional[str] = None

    def start(self):
        self.is_playing = True
        self._play_loop()

    def stop(self):
        self.is_playing = False
        if self.loop_id:
            self.canvas.after_cancel(self.loop_id)
            self.loop_id = None

    def draw_current_frame(self):
        self.canvas.delete("video_frame")
        
        if not (0 <= self.current_frame_idx < len(self.video.frames)):
            return
            
        frame = self.video.frames[self.current_frame_idx]
        p_size = self.scale
        
        # Draw background backdrop
        self.canvas.create_rectangle(
            self.x, self.y, self.x + 16 * p_size, self.y + 12 * p_size,
            fill="#111111", outline="#222222", width=2, tags="video_frame"
        )
        
        # Draw pixels
        for r, row in enumerate(frame):
            for c, char in enumerate(row):
                color_idx = int(char)
                if color_idx > 0 and color_idx < len(self.video.colors):
                    color = self.video.colors[color_idx]
                    px1 = self.x + c * p_size
                    py1 = self.y + r * p_size
                    self.canvas.create_rectangle(
                        px1, py1, px1 + p_size, py1 + p_size,
                        fill=color, outline="", tags="video_frame"
                    )
                    
        # Draw controls bar (Seek bar + stats)
        bar_y = self.y + 12 * p_size + 15
        bar_w = 16 * p_size
        self.canvas.create_rectangle(self.x, bar_y, self.x + bar_w, bar_y + 6, fill="#333333", outline="", tags="video_frame")
        
        # Draw progress seek dot
        progress_ratio = (self.current_frame_idx + 1) / len(self.video.frames)
        dot_x = self.x + int(bar_w * progress_ratio)
        self.canvas.create_oval(dot_x - 6, bar_y - 3, dot_x + 6, bar_y + 9, fill="#00adb5", outline="", tags="video_frame")
        
        # Draw Title and frame stats
        self.canvas.create_text(
            self.x, bar_y + 25, text=f"🎥 {self.video.title}",
            fill="white", font=("Arial", 12, "bold"), anchor="nw", tags="video_frame"
        )
        self.canvas.create_text(
            self.x + bar_w, bar_y + 25, text=f"Frame: {self.current_frame_idx + 1}/{len(self.video.frames)}",
            fill="#888", font=("Arial", 10), anchor="ne", tags="video_frame"
        )

    def _play_loop(self):
        if not self.is_playing:
            return
            
        self.draw_current_frame()
        self.current_frame_idx = (self.current_frame_idx + 1) % len(self.video.frames)
        
        # Loop at the video's FPS interval
        interval_ms = int(1000 / self.video.fps)
        if self.canvas.winfo_exists():
            self.loop_id = self.canvas.after(interval_ms, self._play_loop)
