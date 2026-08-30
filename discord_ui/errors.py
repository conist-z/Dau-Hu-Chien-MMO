class GameError(Exception):
    pass


class MovementError(GameError):
    pass


class CollisionError(GameError):
    pass


class MapError(GameError):
    pass


class RenderError(GameError):
    pass


class PersistenceError(GameError):
    pass
