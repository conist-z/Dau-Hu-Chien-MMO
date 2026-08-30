from game.actions import MoveAction
from game.collision import Collision
from game.state import ActionResult, GameState


def apply_move(state: GameState, action: MoveAction, collision: Collision) -> ActionResult:
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(success=False, reason="no_player", state_changed=False)
    if not player.visible:
        return ActionResult(success=False, reason="invisible", state_changed=False)
    if not collision.can_move(player.x, player.y, action.direction):
        return ActionResult(success=False, reason="blocked", state_changed=False)
    dx, dy = action.direction.vector
    player.x += dx
    player.y += dy
    player.direction = action.direction.name
    return ActionResult(success=True, state_changed=True)
