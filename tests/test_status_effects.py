"""Status effects (user 25/09): bite rolls, DOT ticks, gates, skeleton rebirth."""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from game.status_effects import (  # noqa: E402
    EFFECTS, BAT_POISON, apply_bat_poison, apply_bite_effect, blocks_natural_regen,
    get_effect, heal_multiplier, payload, refresh_effect, stamina_cost_mult,
    tick_effects,
)


class P:
    """Minimal player stand-in (runtime fields the system touches)."""
    def __init__(self):
        self.hp = 100
        self.max_hp = 100
        self.stamina = 100.0
        self.last_damaged_at = None
        self.regen_bank = 0.0
        self.status_effects = []


def test_mob_hp_x5():
    from game.zombies import mob_stats
    assert mob_stats("zombie")["hp"] == 200
    assert mob_stats("skeleton")["hp"] == 280
    assert mob_stats("spider")["hp"] == 220
    assert mob_stats("slime")["hp"] == 250
    assert mob_stats("bat")["hp"] == 100
    assert mob_stats("rat")["hp"] == 60


def test_zombie_bite_infection_50pct():
    # Deterministic: rng that always rolls >= 0.5 -> no effect (50% gate).
    p = P()
    rng_high = random.Random()
    rng_high.random = lambda: 0.9  # above chance AND above the refresh roll
    assert apply_bite_effect(p, "zombie", rng_high) is None
    assert not p.status_effects
    # Always-below rng -> infection L1 applied.
    rng_low = random.Random()
    rng_low.random = lambda: 0.1
    eff = apply_bite_effect(p, "zombie", rng_low)
    assert eff is not None and eff["id"] == "infection" and eff["level"] == 1
    assert eff["dmg"] == 5 and eff["interval"] == 2.0
    assert abs(eff["until"] - time.monotonic() - 20.0) < 1.0


def test_spider_refresh_upgrade_to_l2():
    p = P()
    # First bite: land poison L1 (roll 0.1 < 0.7).
    rng = random.Random()
    rng.random = lambda: 0.1
    eff = apply_bite_effect(p, "spider", rng)
    assert eff["level"] == 1
    # Refresh bite: roll 0.1 < 0.5 upgrade -> L2 with 10 dmg / 1 s.
    eff2 = apply_bite_effect(p, "spider", rng)
    assert eff2["level"] == 2 and eff2["dmg"] == 10 and eff2["interval"] == 1.0
    # Still ONE poison entry (no stacking).
    assert len(p.status_effects) == 1
    # Refresh without upgrade (0.6: inside the 0.7 bite chance, above the
    # 0.5 upgrade roll): stays L2, clock reset.
    rng.random = lambda: 0.6
    before_until = p.status_effects[0]["until"]
    # monotonic() on this machine quantizes in ~15 ms steps, so a 0.01 s
    # sleep can land on the same tick and the strict > below flakes (seen
    # once per few runs). Sleep past TWO ticks to guarantee a fresh stamp.
    time.sleep(0.04)
    eff3 = apply_bite_effect(p, "spider", rng)
    assert eff3["level"] == 2
    assert eff3["until"] > before_until  # refreshed


def test_bat_light_poison_25pct():
    p = P()
    rng = random.Random()
    rng.random = lambda: 0.9
    assert apply_bite_effect(p, "bat", rng) is None  # above 25%
    rng.random = lambda: 0.1
    apply_bat_poison(p)  # the web pack rolls the chance itself, then calls this
    eff = get_effect(p, "poison")
    assert eff["level"] == 1 and eff["dmg"] == 1 and eff["interval"] == 3.0
    assert abs(eff["until"] - time.monotonic() - BAT_POISON["duration_s"]) < 1.0
    # Bat refresh never downgrades a spider L2 poison.
    p2 = P()
    rng.random = lambda: 0.1
    apply_bite_effect(p2, "spider", rng)
    apply_bite_effect(p2, "spider", rng)  # upgrade to L2
    apply_bat_poison(p2)
    assert get_effect(p2, "poison")["level"] == 2


def test_dot_tick_and_expiry():
    p = P()
    rng = random.Random()
    rng.random = lambda: 0.1
    apply_bite_effect(p, "zombie", rng)
    eff = p.status_effects[0]
    # Force the DOT due + expire now.
    eff["next_tick"] = time.monotonic() - 0.01
    hp_before = p.hp
    landed = tick_effects(p)
    assert landed == [("infection", 1, 5)]
    assert p.hp == hp_before - 5
    assert p.last_damaged_at is not None and p.regen_bank == 0.0
    # Expiry: past `until` -> removed.
    eff["until"] = time.monotonic() - 0.01
    landed = tick_effects(p)
    assert landed == [] and not p.status_effects


def test_gates():
    p = P()
    # Clean: regen allowed, stamina x1, full heals.
    assert not blocks_natural_regen(p)
    assert stamina_cost_mult(p) == 1.0
    assert heal_multiplier(p, is_potion=False) == 1.0
    assert heal_multiplier(p, is_potion=True) == 1.0
    # Infection: no regen, stamina x1.5, FOOD blocked, potions full.
    rng = random.Random(); rng.random = lambda: 0.1
    apply_bite_effect(p, "zombie", rng)
    assert blocks_natural_regen(p)
    assert stamina_cost_mult(p) == 1.5
    assert heal_multiplier(p, is_potion=False) == 0.0
    assert heal_multiplier(p, is_potion=True) == 1.0
    # Poison L2: no regen, stamina NORMAL, all heals cut 20%.
    p2 = P()
    apply_bite_effect(p2, "spider", rng)
    apply_bite_effect(p2, "spider", rng)  # -> L2
    assert blocks_natural_regen(p2)
    assert stamina_cost_mult(p2) == 1.0
    assert heal_multiplier(p2, is_potion=False) == 0.8
    assert heal_multiplier(p2, is_potion=True) == 0.8


def test_payload_shape():
    p = P()
    rng = random.Random(); rng.random = lambda: 0.1
    apply_bite_effect(p, "spider", rng)
    rows = payload(p)
    assert rows and len(rows[0]) == 3
    eid, secs, lvl = rows[0]
    assert eid == "poison" and secs <= 20 and lvl == 1


def test_skeleton_has_no_status():
    p = P()
    rng = random.Random(); rng.random = lambda: 0.1
    assert apply_bite_effect(p, "skeleton", rng) is None
    assert not p.status_effects


def test_skeleton_rebirth_rule_exists():
    # The rebirth is a rules.py branch; verify the constants it uses.
    from game.zombies import mob_stats
    assert mob_stats("skeleton")["hp"] == 280  # reborn at FULL hp = 280
