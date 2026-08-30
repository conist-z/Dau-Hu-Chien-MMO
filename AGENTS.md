# Discord Map Game — Engineering Rules

You are implementing a Discord-native multiplayer map game.

The game screen MUST live entirely inside a Discord message. Do NOT create or require an external web frontend.

## Architecture

```
Discord UI → GameManager → GameState / Actions / Rules → Persistence → Renderer
```

## Core principles

1. Discord is a frontend/transport layer only.
2. Game logic must not depend on discord.py objects.
3. Rendering must not mutate game state.
4. Persistence must not know about Discord UI.
5. Commands and button callbacks must be thin adapters.
6. All scenarios are isolated by channel_id.
7. Each scenario has one primary Discord message.
8. Movement is represented by Actions.
9. State transitions are deterministic.
10. All maps are data-driven.
11. Tiled JSON is the source format for map data.
12. Avatar images must be cached.
13. Persistent Discord Views must use `timeout=None` and explicit `custom_id` values.
14. Restore persistent views after restart.
15. Never use a global asyncio lock for all maps.
16. Use one runtime/queue/lock per channel.
17. Never hard-code collision coordinates.
18. Never hard-code map dimensions.
19. Never store image binaries in SQLite.
20. Never swallow exceptions.
21. Never block the async event loop with synchronous network I/O.
22. Do not assume hard-coded Discord rate limits.
23. Handle 429/retry behavior through the Discord library/API semantics.
24. Never create a new Discord message for each player movement.
25. Prefer editing the existing game message.
26. Keep files small and responsibilities isolated.
27. Write tests for every game rule.
28. Never introduce unnecessary abstractions.
29. Implement the smallest working vertical slice first.
30. Do not implement future RPG systems before the movement/map MVP is stable.

## MVP features

- /startmap, /joinmap, /leave-map, /map, /mapreset, /mapinfo
- 8-direction movement
- Tiled map loading
- tile collision
- multiple players
- Discord avatar tokens
- SQLite persistence
- persistent buttons
- restart recovery

## Suggested modules

```
game/         state.py actions.py collision.py map_loader.py rules.py manager.py
discord_ui/   commands.py map_view.py interactions.py errors.py
rendering/    renderer.py avatar.py layers.py camera.py
persistence/  database.py migrations.py repositories.py
tests/        test_state.py test_collision.py test_map_loader.py test_renderer.py test_database.py
```

## Workflow per step

1. inspect existing code
2. explain the intended change briefly
3. implement the smallest coherent change
4. run tests
5. run a syntax/type/import check
6. report changed files
7. report any remaining risks

Do not proceed to the next phase until the current acceptance tests pass.
