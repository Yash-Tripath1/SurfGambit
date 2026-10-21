"""
game.py — compatibility surface for the v1 game module.

The game itself is `ui.game.SpaceInvaders` (scoped key bindings, cancellable
animation loop, Escape to leave). The v1 class name is kept as an alias.
"""

from __future__ import annotations

from ui.game import SpaceInvaders

SpaceInvaderGame = SpaceInvaders

__all__ = ["SpaceInvaders", "SpaceInvaderGame"]
