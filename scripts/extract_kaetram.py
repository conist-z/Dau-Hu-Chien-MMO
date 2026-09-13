"""Extract Kaetram-Open into kaetram_extract/ — fully categorized.

Source: _kaetram_src (shallow clone of Kaetram/Kaetram-Open, MPL-2.0 + OPL,
assets CC-BY-SA 3.0). Idempotent: re-running rebuilds the extract tree.

Layout (numbered for reading order, see README.md there):
  01_mobs_sprites/    every mob sprite sheet PNG (166 mobs), one file each
  02_mobs_stats/      mobs.json + ONE JSON PER MOB (hp, level, aggro, drops,
                      attack/defense stats, bonuses, skills)
  03_mob_ai/          mob AI/behaviour TypeScript (mob.ts, handler.ts, combat,
                      points, hit) + per-mob plugin behaviours (boss AI)
  04_items/           items.json split per item + crafting recipes + stores
  05_npcs/            npcs.json split per NPC + NPC sprite sheets
  06_maps/            world.json (1152x1008 Tiled-derived) + parser tool
  07_tilesets/        all client tileset sheets + object/tree/rock/bush sprites
  08_player_sprites/  player skins + equipment (weapon/helmet/cape/...)
  09_fx_audio/        effects, projectiles, effectentity, overlays, audio
  10_interface/       HUD/UI slices, equipment icons, skill icons, cursors
  11_data_other/      spawns.json, rocks.json, trees.json, achievements,
                      quests, abilities, minigames, fishing, foraging, tables
  12_engine_reference/ map parsing/areas/skills/plugins server code by topic
  _license/           LICENSE + OPL terms + attribution README
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "_kaetram_src"
OUT = Path(__file__).resolve().parent.parent / "kaetram_extract"

CLIENT = SRC / "packages" / "client"
SERVER = SRC / "packages" / "server"
TOOLS = SRC / "packages" / "tools"
COMMON = SRC / "packages" / "common"

SPRITES = CLIENT / "public" / "img" / "sprites"
SDATA = SERVER / "data"


def reset_dir(p: Path) -> Path:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)
    return p


def copy_tree(src: Path, dst: Path) -> int:
    """Copy a directory tree, returning the file count."""
    if not src.is_dir():
        return 0
    n = 0
    for f in sorted(src.rglob("*")):
        if f.is_file():
            rel = f.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            n += 1
    return n


def split_json(src_file: Path, dst_dir: Path, key_field: str = "key"):
    """Split a dict-of-definitions JSON into one file per entry."""
    data = json.loads(src_file.read_text(encoding="utf-8"))
    dst_dir.mkdir(parents=True, exist_ok=True)
    for entry_key, entry in data.items():
        slug = str(entry_key).replace("/", "_")
        out_name = entry.get(key_field, entry_key) if isinstance(entry, dict) else entry_key
        slug = str(out_name).replace("/", "_")
        (dst_dir / f"{slug}.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return len(data)


def main() -> None:
    if not SRC.exists():
        raise SystemExit("source missing: run `git clone --depth 1 https://github.com/Kaetram/Kaetram-Open.git _kaetram_src` first")

    stats = {}

    # ---- 01 mob sprites -----------------------------------------------------
    d = reset_dir(OUT / "01_mobs_sprites")
    stats["mob_sprites"] = copy_tree(SPRITES / "mobs", d)

    # ---- 02 mob stats (split per mob) --------------------------------------
    d = reset_dir(OUT / "02_mobs_stats")
    if (SDATA / "mobs.json").exists():
        data = json.loads((SDATA / "mobs.json").read_text(encoding="utf-8"))
        # mobs.json is a dict keyed by mob key
        per = {}
        for key, mob in data.items():
            per[key] = mob
            slug = str(mob.get("key", key)).replace("/", "_")
            (d / f"{slug}.json").write_text(json.dumps(mob, indent=2, ensure_ascii=False), encoding="utf-8")
        shutil.copy2(SDATA / "mobs.json", d / "_all_mobs.json")
        stats["mob_stats"] = len(per)

    # ---- 03 mob AI ----------------------------------------------------------
    d = reset_dir(OUT / "03_mob_ai")
    mob_dir = SERVER / "src" / "game" / "entity" / "character" / "mob"
    combat_dir = SERVER / "src" / "game" / "entity" / "character" / "combat"
    points_dir = SERVER / "src" / "game" / "entity" / "character" / "points"
    for src_dir in (mob_dir, combat_dir, points_dir):
        stats["mob_ai"] = stats.get("mob_ai", 0) + copy_tree(src_dir, d / src_dir.name)
    # Per-mob plugin behaviours (boss AI: skeletonking, ogrilord, etc.)
    plugins = SDATA / "plugins" / "mobs"
    if plugins.is_dir():
        stats["mob_ai_plugins"] = copy_tree(plugins, d / "plugins")

    # ---- 04 items ------------------------------------------------------------
    d = reset_dir(OUT / "04_items")
    if (SDATA / "items.json").exists():
        n = split_json(SDATA / "items.json", d / "per_item")
        shutil.copy2(SDATA / "items.json", d / "_all_items.json")
        stats["items"] = n
    stats["item_sprites"] = copy_tree(SPRITES / "items", d / "sprites")
    crafting = SDATA / "crafting"
    if crafting.is_dir():
        stats["crafting_files"] = copy_tree(crafting, d / "crafting")
    for f in ("stores.json", "tables.json"):
        if (SDATA / f).exists():
            shutil.copy2(SDATA / f, d / f)

    # ---- 05 npcs --------------------------------------------------------------
    d = reset_dir(OUT / "05_npcs")
    if (SDATA / "npcs.json").exists():
        n = split_json(SDATA / "npcs.json", d / "per_npc")
        shutil.copy2(SDATA / "npcs.json", d / "_all_npcs.json")
        stats["npcs"] = n
    stats["npc_sprites"] = copy_tree(SPRITES / "npcs", d / "sprites")

    # ---- 06 maps ---------------------------------------------------------------
    d = reset_dir(OUT / "06_maps")
    if (SDATA / "map" / "world.json").exists():
        shutil.copy2(SDATA / "map" / "world.json", d / "world.json")
    client_maps = CLIENT / "data" / "maps"
    if client_maps.is_dir():
        stats["client_maps"] = copy_tree(client_maps, d / "client_maps")
    stats["map_parser"] = copy_tree(TOOLS / "map", d / "parser_tool")

    # ---- 07 tilesets -------------------------------------------------------------
    d = reset_dir(OUT / "07_tilesets")
    stats["tilesets"] = copy_tree(CLIENT / "public" / "img" / "tilesets", d / "sheets")
    for sub, name in (("objects", "objects"), ("trees", "trees"), ("rocks", "rocks"),
                      ("bushes", "bushes"), ("fishspots", "fishspots")):
        stats[name] = copy_tree(SPRITES / sub, d / name)

    # ---- 08 player sprites ----------------------------------------------------------
    d = reset_dir(OUT / "08_player_sprites")
    stats["player_sprites"] = copy_tree(SPRITES / "player", d)

    # ---- 09 fx + audio ----------------------------------------------------------------
    d = reset_dir(OUT / "09_fx_audio")
    for sub in ("effects", "projectiles", "effectentity", "overlays", "pets", "skills"):
        stats[f"fx_{sub}"] = copy_tree(SPRITES / sub, d / sub)
    audio = CLIENT / "public" / "audio"
    if audio.is_dir():
        stats["audio"] = copy_tree(audio, d / "audio")

    # ---- 10 interface ---------------------------------------------------------------
    d = reset_dir(OUT / "10_interface")
    stats["interface"] = copy_tree(CLIENT / "public" / "img" / "interface", d)
    stats["icons"] = copy_tree(CLIENT / "public" / "img" / "icons", d / "icons")
    stats["cursors"] = copy_tree(SPRITES / "cursors", d / "cursors")

    # ---- 11 other data ------------------------------------------------------------------
    d = reset_dir(OUT / "11_data_other")
    for f in ("spawns.json", "rocks.json", "trees.json", "achievements.json",
              "abilities.json", "minigames.json", "fishing.json", "foraging.json",
              "effectentities.json"):
        if (SDATA / f).exists():
            shutil.copy2(SDATA / f, d / f)
    for sub in ("quests", "quest_bases"):
        p = SDATA / sub
        if p.is_dir():
            stats[sub] = copy_tree(p, d / sub)

    # ---- 12 engine reference --------------------------------------------------------------
    d = reset_dir(OUT / "12_engine_reference")
    game_src = SERVER / "src" / "game"
    for topic in ("map", "minigames", "globals", "info", "controllers", "network"):
        p = game_src / topic
        if p.is_dir():
            copy_tree(p, d / topic)
    # player systems (skills, quests, equipment, containers, abilities)
    player_src = game_src / "entity" / "character" / "player"
    if player_src.is_dir():
        copy_tree(player_src, d / "player_systems")
    # common network/types for protocol reference
    if COMMON.exists():
        copy_tree(COMMON / "network", d / "common_network")
        copy_tree(COMMON / "types", d / "common_types")

    # ---- license ----------------------------------------------------------------------------
    d = reset_dir(OUT / "_license")
    for f in ("LICENSE", "README.md"):
        if (SRC / f).exists():
            shutil.copy2(SRC / f, d / f)
    (d / "ATTRIBUTION.md").write_text(
        "# Attribution (REQUIRED by license)\n\n"
        "- Code & data: Kaetram-Open (MPL-2.0 + OPL-1.0) — https://github.com/Kaetram/Kaetram-Open\n"
        "- Assets (sprites, tilesets, audio): CC-BY-SA 3.0 — credit Kaetram + original BrowserQuest artists (Little Workshop).\n"
        "- OPL obligations: keep link to Kaetram in your credits; do not remove artist credits;\n"
        "  no AI-training / crypto / NFT use without permission; keep your code open-source.\n",
        encoding="utf-8",
    )

    # ---- index + report ----------------------------------------------------------------------
    total = sum(v for v in stats.values() if isinstance(v, int))
    lines = [
        "# Kaetram Extract — Index",
        "",
        f"Extracted from Kaetram/Kaetram-Open (shallow clone in `_kaetram_src/`).",
        f"Total files: **{total}** across categories below.",
        "",
        "| Folder | Contents | Files |",
        "|---|---|---|",
    ]
    desc = {
        "01_mobs_sprites": "166 mob sprite sheets (PNG, animation frames inside)",
        "02_mobs_stats": "Per-mob JSON: HP, level, aggro range, attack rate, movement, respawn, drops, attack/defense stats, bonuses, skills",
        "03_mob_ai": "Mob AI TypeScript: mob.ts (aggro/roam/respawn), handler.ts (plugin dispatch), combat/hit/points + per-boss plugins",
        "04_items": "Per-item JSON (weapons/armor/food/etc) + sprites + crafting recipes + stores",
        "05_npcs": "Per-NPC JSON + sprite sheets",
        "06_maps": "world.json (1152x1008), client map data, Tiled parser tool",
        "07_tilesets": "6 tilesheets + objects/trees/rocks/bushes/fishspots sprites",
        "08_player_sprites": "Player skins + equipment layers (weapon, helmet, chestplate, legplates, cape, shield)",
        "09_fx_audio": "Effects, projectiles, pets, skills, overlays + music/sounds",
        "10_interface": "HUD slices, equipment icons, skill icons, cursors, guild images",
        "11_data_other": "Spawns, rocks/trees definitions, achievements, quests, abilities, minigames, fishing, foraging",
        "12_engine_reference": "Server game code by topic: map/areas, minigames, player systems (skills/quests/equipment), network protocol",
        "_license": "LICENSE, README, ATTRIBUTION (must be kept)",
    }
    for folder in sorted(OUT.iterdir()):
        if not folder.is_dir():
            continue
        n = sum(1 for _ in folder.rglob("*") if _.is_file())
        lines.append(f"| `{folder.name}` | {desc.get(folder.name, '')} | {n} |")
    lines += ["", "## Mob AI mechanism coverage (03_mob_ai)", ""]
    for ts in sorted((OUT / "03_mob_ai").rglob("*.ts")):
        rel = ts.relative_to(OUT / "03_mob_ai")
        lines.append(f"- `{rel.as_posix()}`")
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"Extracted {total} tracked files into {OUT}")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
