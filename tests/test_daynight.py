"""Tests for the manual in-game time override (/time set on web chat)."""

import pytest

from rendering import daynight


def test_set_ingame_time_pins_clock():
    sec = daynight.set_ingame_time(22 * 3600)  # 22:00 -> night
    assert sec == 22 * 3600
    # Clock keeps advancing: a moment later it is >= the pinned value (mod day).
    after = daynight.ingame_seconds()
    drift = (after - (22 * 3600)) % daynight.SECONDS_PER_DAY
    assert drift < 60  # advanced by less than a minute of game time


def test_set_ingame_time_none_restores_natural_cycle():
    daynight.set_ingame_time(12 * 3600)
    daynight.set_ingame_time(None)
    natural = daynight._GAME_BASE_SEC + (
        __import__("time").time() - daynight._GAME_EPOCH
    ) * daynight.SECONDS_PER_DAY / daynight.GAME_DAY_LENGTH_REAL_SECONDS
    assert daynight._GAME_OFFSET_SEC == 0.0
    drift = (daynight.ingame_seconds() - natural) % daynight.SECONDS_PER_DAY
    assert min(drift, daynight.SECONDS_PER_DAY - drift) < 60  # wrap-aware


def test_night_window_contains_presets():
    from game.zombies import is_night

    assert is_night(22 * 3600)       # night preset spawns zombies
    assert not is_night(12 * 3600)   # day preset is zombie-free
