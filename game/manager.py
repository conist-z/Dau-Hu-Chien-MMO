from dataclasses import dataclass, field
from typing import Dict, Optional

import asyncio

from game.actions import MoveAction
from game.collision import Collision
from game.map_loader import MapData, load_map
from game.rules import apply_move
from game.state import ActionResult, GameState, Player
from rendering.camera import Camera
from PIL import Image


@dataclass
class ScenarioRuntime:
    channel_id: int
    message_id: Optional[int]
    state: GameState
    map_data: MapData
    collision: Collision
    members: Dict[int, object] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dirty: bool = False
    composite: Optional[Image.Image] = None
    camera: Optional[Camera] = None
    _save_task: Optional[asyncio.Task] = None
    pending_player: Optional[Player] = None
    # UI/form state (per channel, not persisted): step size + auto-move.
    step_size: int = 1
    auto_running: bool = False
    auto_armed: bool = False
    auto_user_id: Optional[int] = None
    auto_direction: Optional[object] = None
    auto_task: Optional[asyncio.Task] = None


class GameManager:
    def __init__(self, assets_dir):
        self.assets_dir = assets_dir
        self.runtimes: Dict[int, ScenarioRuntime] = {}
        self.renderer = None
        self.db = None

    def create_runtime(self, channel_id: int, map_id: str, message_id: Optional[int] = None) -> ScenarioRuntime:
        map_data = load_map(map_id, self.assets_dir)
        state = GameState(scenario_id=channel_id, map_id=map_id)
        rt = ScenarioRuntime(
            channel_id=channel_id,
            message_id=message_id,
            state=state,
            map_data=map_data,
            collision=Collision(map_data),
            camera=Camera.auto(map_data),
        )
        self.runtimes[channel_id] = rt
        return rt

    def get_runtime(self, channel_id: int) -> Optional[ScenarioRuntime]:
        return self.runtimes.get(channel_id)

    def remove_runtime(self, channel_id: int) -> None:
        rt = self.runtimes.pop(channel_id, None)
        if rt is not None:
            if rt._save_task is not None and not rt._save_task.done():
                rt._save_task.cancel()
            if rt.auto_running and rt.auto_task is not None and not rt.auto_task.done():
                rt.auto_task.cancel()
            rt.auto_running = False
            rt.auto_armed = False

    async def dispatch(self, channel_id: int, action: MoveAction):
        rt = self.runtimes[channel_id]
        async with rt.lock:
            result = apply_move(rt.state, action, rt.collision)
            rt.dirty = rt.dirty or result.state_changed
        if result.state_changed and self.db is not None:
            p = rt.state.get_player(action.user_id)
            if p is not None:
                self._schedule_save(rt, p)
        return rt, result

    def _schedule_save(self, rt: ScenarioRuntime, player: Player) -> None:
        """Debounce DB writes: at most one save task per channel in flight.

        Keeps the per-move critical path free of DB I/O; the pending player is
        flushed on the next tick or explicitly via flush_runtime/flush_all.
        """
        rt.pending_player = player
        if rt._save_task is None or rt._save_task.done():
            rt._save_task = asyncio.create_task(self._flush_save(rt))

    async def _flush_save(self, rt: ScenarioRuntime) -> None:
        p = rt.pending_player
        rt.pending_player = None
        if p is not None and self.db is not None:
            from persistence.repositories import save_player

            await save_player(self.db, rt.channel_id, p)

    async def flush_runtime(self, rt: ScenarioRuntime) -> None:
        if rt._save_task is not None and not rt._save_task.done():
            await rt._save_task
        elif rt.pending_player is not None and self.db is not None:
            from persistence.repositories import save_player

            await save_player(self.db, rt.channel_id, rt.pending_player)
            rt.pending_player = None

    async def flush_all(self) -> None:
        for rt in self.runtimes.values():
            await self.flush_runtime(rt)
