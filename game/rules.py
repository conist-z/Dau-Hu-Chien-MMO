from typing import Optional

from game.actions import (
    AimAction,
    AimResetAction,
    AttackAction,
    BreakBlockAction,
    MoveAction,
    PlaceBlockAction,
    TurnAction,
)
from game import blocks as blocks_mod
from game.blocks import BlockGrid, get_block
from game.collision import Collision
from game.state import ActionResult, Direction, GameState, Player
from game.weather import WeatherState, compute_modifiers
from game.zombies import (
    BARE_HAND_ATTACK_DAMAGE,
    ZOMBIE_DROP_TABLE,
    ZOMBIE_PLAYER_ATTACK_DAMAGE,
    remove_zombie,
)

# Blocks that count as STONE for the pickaxe gate (user rule: đá chỉ đập
# được bằng cúp đất trở lên). Data-driven: add ids here without logic edits.
STONE_BLOCK_IDS = {"stone"}


def _held_item_id(state: GameState, player: Player) -> Optional[str]:
    """The item the player is 'holding': the first hotbar slot carrying a
    weapon-class item with stock in the bag. The hotbar is a PROJECTION of
    the first HOTBAR_SLOTS stacks of the ordered bag, so this is just: scan
    the bag front-to-back for a weapon. Reads the runtime-scoped maps stashed
    on the GameState by the manager (pure rule layer stays discord-free);
    None when bare-handed."""
    inventories = getattr(state, "inventories", None) or {}
    inv = inventories.get(player.user_id)
    if inv is None:
        return None
    from game.items import WEAPON_ITEM_IDS

    for _slot, iid in inv.hotbar().items():
        if iid in WEAPON_ITEM_IDS and inv.count(iid) > 0:
            return iid
    return None


def _has_weapon(state: GameState, player: Player) -> bool:
    return _held_item_id(state, player) is not None

# Build Mode aim cursor: max Chebyshev distance (tiles) from the player to the
# placement target. Keeps building local — no placing across the whole map.
AIM_RANGE = 3


def apply_move(state: GameState, action: MoveAction, collision: Collision) -> ActionResult:
    """Turn toward the pressed direction, then step if the tile is free.

    Turning in place is always allowed (that is how the player aims 🧱/🔨 at a
    neighbouring tile without stepping onto it); only the step itself can be
    blocked. ``moved`` is True only when the position actually changed.
    """
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(success=False, reason="no_player", state_changed=False)
    if not player.alive:
        return ActionResult(success=False, reason="dead", state_changed=False)
    if not player.visible:
        return ActionResult(success=False, reason="invisible", state_changed=False)

    moved = False
    dx, dy = action.direction.vector
    target = (player.x + dx, player.y + dy)
    zombies = getattr(state, "zombies", {})
    candidates = zombies.values() if isinstance(zombies, dict) else (zombies or [])
    zombie_on_target = any(
        zombie.alive and (zombie.x, zombie.y) == target
        for zombie in candidates
    )
    if not zombie_on_target and collision.can_move(player.x, player.y, action.direction):
        player.x, player.y = target
        moved = True

    turned = player.direction != action.direction.name
    player.direction = action.direction.name

    if not moved and not turned:
        return ActionResult(success=False, reason="blocked", state_changed=False)
    return ActionResult(success=True, state_changed=True, moved=moved)


def _facing_tile(player: Player) -> tuple:
    dx, dy = Direction[player.direction].vector
    return player.x + dx, player.y + dy


def _tile_has_player(state: GameState, x: int, y: int, exclude: int) -> bool:
    return any(
        p.x == x and p.y == y and p.user_id != exclude
        for p in state.get_visible_players()
    )


ATTACK_RANGE = 3


def _nearest_in_range(state, x: int, y: int, candidates, max_range: int):
    valid = [
        enemy for enemy in candidates
        if enemy.alive and max(abs(enemy.x - x), abs(enemy.y - y)) <= max_range
    ]
    return min(valid, key=lambda enemy: (max(abs(enemy.x - x), abs(enemy.y - y)), enemy.zombie_id), default=None)


def _target_tile(player: Player) -> tuple:
    """The square the player aims at: the aim-cursor tile when one is active
    (the renderer highlights exactly this square), otherwise the facing tile."""
    if player.aim_active:
        return player.x + player.aim_dx, player.y + player.aim_dy
    return _facing_tile(player)


def apply_attack(state: GameState, action: AttackAction, blocks: BlockGrid = None,
                 inventory=None) -> ActionResult:
    """Damage the nearest hostile in the player's 3-tile radius.

    When NO hostile is nearby, the swing falls back to breaking the block on
    the target square (the highlighted tile): the ⚔️ press never dead-ends
    while building — it becomes a pick/hammer against placed blocks."""
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(False, "no_player")
    if not player.alive:
        return ActionResult(False, "dead")

    zombies = getattr(state, "zombies", {})
    candidates = zombies.values() if isinstance(zombies, dict) else (zombies or [])
    zombie = _nearest_in_range(state, player.x, player.y, candidates, ATTACK_RANGE)
    if zombie is None:
        # No hostile around: break the block on the target square instead.
        if blocks is not None:
            from game import blocks as blocks_mod

            tx, ty = _target_tile(player)
            block_id = blocks.remove(tx, ty)
            if block_id is not None:
                if not blocks_mod.CREATIVE_MODE:
                    inventory.add(block_id, 1)
                return ActionResult(
                    True, state_changed=True, pos=(tx, ty), block_id=block_id,
                )
        return ActionResult(False, "no_target")
    # Damage depends on what the player is holding. A sword deals its tier
    # damage (dirt tier ~= 3 hits per zombie); other weapon-class tools
    # (axe/pickaxe) deal the legacy weapon damage; a bare hand punches for
    # BARE_HAND_ATTACK_DAMAGE.
    inventories = getattr(state, "inventories", None) or {}
    inv = inventories.get(player.user_id)
    from game.tools import sword_damage

    dmg = sword_damage(inv)
    if dmg <= 0:
        dmg = (
            ZOMBIE_PLAYER_ATTACK_DAMAGE
            if _has_weapon(state, player)
            else BARE_HAND_ATTACK_DAMAGE
        )
    zombie.hp = max(0, zombie.hp - dmg)
    target_id = zombie.zombie_id
    defeated = not zombie.alive
    drops = []
    if defeated:
        remove_zombie(state, target_id)
        for item_id, chance, qty in ZOMBIE_DROP_TABLE:
            # Keep the drop roll in the pure action rule so every attack path
            # has identical results and persistence remains adapter-free.
            import random
            if random.random() < chance:
                drops.append((item_id, qty))
    return ActionResult(
        True, state_changed=True,
        pos=(zombie.x, zombie.y), block_id="zombie",
        damage=dmg, target_id=target_id,
        target_defeated=defeated, drops=drops,
    )



def apply_turn(state: GameState, action: TurnAction) -> ActionResult:
    """Rotate the player in place — never steps, never blocked (Build tool 🔁).

    ``state_changed`` is False when the player already faces that direction,
    so the UI can cheap-ACK instead of re-uploading a frame.
    """
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(False, "no_player")
    if not player.alive:
        return ActionResult(False, "dead")
    if not player.visible:
        return ActionResult(False, "invisible")
    changed = player.direction != action.direction.name
    player.direction = action.direction.name
    return ActionResult(True, state_changed=changed, moved=False)


def apply_aim(state: GameState, action: AimAction) -> ActionResult:
    """Build Mode: move the aim cursor by (dx, dy), clamped to ``AIM_RANGE``.

    The cursor is an offset RELATIVE to the player, so it follows them when
    they step (like carrying a laser pointer). Always succeeds while the
    player exists; state_changed reflects whether the offset actually moved.
    """
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(False, "no_player")
    if not player.alive:
        return ActionResult(False, "dead")
    if not player.visible:
        return ActionResult(False, "invisible")
    nx = max(-AIM_RANGE, min(AIM_RANGE, player.aim_dx + action.dx))
    ny = max(-AIM_RANGE, min(AIM_RANGE, player.aim_dy + action.dy))
    changed = (nx, ny) != (player.aim_dx, player.aim_dy) or not player.aim_active
    player.aim_dx, player.aim_dy, player.aim_active = nx, ny, True
    return ActionResult(True, state_changed=changed, moved=False,
                        pos=(player.x + nx, player.y + ny))


def apply_aim_reset(state: GameState, action: AimResetAction) -> ActionResult:
    """Build Mode off: hide the aim cursor."""
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(False, "no_player")
    if not player.alive:
        return ActionResult(False, "dead")
    changed = player.aim_active
    player.aim_active = False
    player.aim_dx = player.aim_dy = 0
    return ActionResult(True, state_changed=changed, moved=False)


def apply_place_block(
    state: GameState, action: PlaceBlockAction, collision: Collision,
    blocks: BlockGrid, inventory,
) -> ActionResult:
    """Place a block on the target tile. Consumes 1 material from the bag.

    Target = the facing tile by default; with an offset (Build Mode cursor)
    the tile at (player + dx,dy), which must be within ``AIM_RANGE`` and not
    the player's own tile.

    Rules: tile must be bare walkable ground (the overlay covers ground only),
    inside the map, free of blocks and free of other players.
    """
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(False, "no_player")
    if not player.alive:
        return ActionResult(False, "dead")
    if not player.visible:
        return ActionResult(False, "invisible")
    bdef = get_block(action.block_id)
    if bdef is None or not bdef.placeable:
        return ActionResult(False, "not_placeable")

    if action.dx is not None or action.dy is not None:
        dx = action.dx or 0
        dy = action.dy or 0
        if max(abs(dx), abs(dy)) > AIM_RANGE:
            return ActionResult(False, "out_of_range")
        if dx == 0 and dy == 0:
            return ActionResult(False, "own_tile")
        tx, ty = player.x + dx, player.y + dy
    else:
        tx, ty = _facing_tile(player)

    if not collision.is_walkable(tx, ty) or blocks.solid_at(tx, ty):
        return ActionResult(False, "blocked_tile")
    if _tile_has_player(state, tx, ty, action.user_id):
        return ActionResult(False, "tile_occupied")
    if not blocks_mod.CREATIVE_MODE:
        if not inventory.remove(action.block_id, 1):
            return ActionResult(False, "no_material")
    if not blocks.place(tx, ty, action.block_id):
        # Should be unreachable (checked above) — never lose the material.
        if not blocks_mod.CREATIVE_MODE:
            inventory.add(action.block_id, 1)
        return ActionResult(False, "already_block")
    return ActionResult(True, state_changed=True, pos=(tx, ty), block_id=action.block_id)


def apply_break_block(
    state: GameState, action: BreakBlockAction, blocks: BlockGrid, inventory,
) -> ActionResult:
    """Break the block on the target tile (the square the player aims at);
    the ground shows again and the material returns to the bag.

    The target tile is the aim-cursor tile while one is active, otherwise the
    facing tile — always the exact square the facing/aim highlight shows."""
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(False, "no_player")
    if not player.alive:
        return ActionResult(False, "dead")
    if not player.visible:
        return ActionResult(False, "invisible")

    if player.aim_active:
        tx, ty = player.x + player.aim_dx, player.y + player.aim_dy
    elif getattr(action, "dx", None) is not None or getattr(action, "dy", None) is not None:
        # Web client: break the tile under the MOUSE cursor (offset from the
        # player), clamped to the same build range as placing.
        dx = int(getattr(action, "dx", 0) or 0)
        dy = int(getattr(action, "dy", 0) or 0)
        if max(abs(dx), abs(dy)) > AIM_RANGE:
            return ActionResult(False, "out_of_range")
        tx, ty = player.x + dx, player.y + dy
    else:
        tx, ty = _facing_tile(player)
    block_id = blocks.get(tx, ty)
    if block_id is None:
        return ActionResult(False, "no_block")
    # STONE GATE (user rule): stone blocks can only be broken with a pickaxe
    # of dirt tier or better. Anyone else gets the "too_hard" reason.
    if block_id in STONE_BLOCK_IDS:
        inventories = getattr(state, "inventories", None) or {}
        from game.tools import has_pickaxe_tier

        held = inventories.get(player.user_id, inventory)
        if not has_pickaxe_tier(held):
            return ActionResult(False, "too_hard")
    blocks.remove(tx, ty)
    if not blocks_mod.CREATIVE_MODE:
        inventory.add(block_id, 1)
    return ActionResult(True, state_changed=True, pos=(tx, ty), block_id=block_id)


def apply_weather_regen(player: Player, ws: WeatherState) -> None:
    """Apply passive mana/hp regen from the current national weather.

    Called on each successful move so the modifier is observable while playing.
    Multipliers are clamped inside ``compute_modifiers`` (±20-50%).
    """
    m = compute_modifiers(ws)
    mana_gain = int(round(10 * (m.mana_regen_mult - 1.0)))
    hp_gain = int(round(10 * (m.hp_regen_mult - 1.0)))
    if mana_gain and player.mana < player.max_mana:
        player.mana = min(player.max_mana, player.mana + mana_gain)
    if hp_gain and player.hp < player.max_hp:
        player.hp = min(player.max_hp, player.hp + hp_gain)
