from game.map_loader import MapData
from game.state import Direction


class Collision:
    def __init__(self, map_data: MapData):
        self.map_data = map_data

    def is_walkable(self, x: int, y: int) -> bool:
        w, h = self.map_data.width, self.map_data.height
        if x < 0 or y < 0 or x >= w or y >= h:
            return False
        return self.map_data.is_walkable(x, y)

    def can_move(self, x: int, y: int, direction: Direction) -> bool:
        dx, dy = direction.vector
        return self.is_walkable(x + dx, y + dy)
