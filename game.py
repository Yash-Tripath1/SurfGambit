"""
game.py: compatibility surface for the legacy game module.

The game itself is `ui.game.SpaceInvaders` (scoped key bindings, cancellable
animation loop, Escape to leave). The legacy class name is kept as an alias.
"""

from __future__ import annotations

from ui.game import SpaceInvaders

SpaceInvaderGame = SpaceInvaders

__all__ = ["SpaceInvaders", "SpaceInvaderGame"]
