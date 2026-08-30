from dataclasses import dataclass

from game.state import Direction


@dataclass
class MoveAction:
    user_id: int
    direction: Direction
