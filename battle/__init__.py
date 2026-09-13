"""Generic, data-driven turn battle engine.

Pure logic only: no discord.py, no IO, no PKM concepts, no items.
Customize by editing registries in battle/skills.py + chart in battle/rules.py
(or loading them from JSON) — the engine itself never changes.
"""
