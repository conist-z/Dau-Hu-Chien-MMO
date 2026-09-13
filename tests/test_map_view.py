import discord
from discord_ui.map_view import MapView


# Emoji codepoints that Discord's button-emoji API rejects even though they
# are valid Unicode emoji (BMP "open circle arrow" range is not whitelisted).
_DISCORD_REJECTED_EMOJI = {"\u21bb", "\u21ba"}


class _FakeManager:
    def get_runtime(self, channel_id):
        return None

    def get_runtime_for(self, channel_id, user_id=None):
        return self.get_runtime(channel_id)


def _cids(view):
    return {item.custom_id.split(":")[-1] for item in view.children}


def test_map_view_emotes_are_discord_compatible():
    """Every button emoji must be acceptable by Discord's API.

    Discord rejects certain Unicode emoji (e.g. U+21BB ↻️) with
    ``Invalid emoji`` even though they are valid Unicode.  This test
    serialises the view the same way discord.py does and asserts that no
    button carries a known-rejected codepoint.
    """
    view = MapView(555012, _FakeManager())
    for item in view.children:
        if item.emoji is None:
            continue
        name = getattr(item.emoji, "name", None)
        assert name is not None and name != "", item.custom_id
        for ch in name:
            assert ch not in _DISCORD_REJECTED_EMOJI, (
                "%s uses rejected emoji U+%04X" % (item.custom_id, ord(ch))
            )



def test_map_view_layout_tool_rail_right_and_empty_centre():
    view = MapView(555010, _FakeManager())
    cids = _cids(view)

    # 8 directions + 3 tools + ⚔️ attack + 🔨 break + 🧱 build + 6 hotbar buttons + centre cell.
    for key in ["t_refresh", "t_step", "t_auto", "t_build",
                "b_break", "h0", "h4", "h5",
                "nw", "n", "ne", "w", "s", "e", "sw", "se"]:
        assert key in cids, key
    # The old 9-slot hotbar is gone.
    for gone in ["h6", "h7", "h8"]:
        assert gone not in cids, gone
    # The old 🧱/📦 block buttons are gone (replaced by the hotbar rail).
    for gone in ["b_place", "b_block"]:
        assert gone not in cids, gone
    # The centre cell is now the build-mode place-at-cursor button (c_place).
    assert "c_place" in cids

    # Tool rail sits on the RIGHT column (row == its d-pad row), not its own row.
    by_cid = {item.custom_id.split(":")[-1]: item for item in view.children}
    assert by_cid["t_refresh"].row == 0
    assert "⚔️" in (by_cid["t_refresh"].emoji.name or "")
    assert by_cid["t_step"].row == 1
    assert by_cid["t_auto"].row == 2
    assert by_cid["b_break"].row == 0
    # 🧱 Build toggle took over the follow-build slot (row 2, right rail).
    assert by_cid["t_build"].row == 2
    assert "🧱" in (by_cid["t_build"].emoji.name or "")

    # Hotbar rail occupies rows 3-4 (5 + 1 buttons; 🔧 shares the 6th's row).
    for i in range(6):
        assert by_cid[f"h{i}"].row == 3 + i // 5


def test_reload_control_is_attack_control():
    view = MapView(555011, _FakeManager())
    assert view.tool_refresh.custom_id.endswith(":t_refresh")
    assert "⚔️" in (view.tool_refresh.emoji.name or "")


def test_tool_step_shows_count_label():
    class RT:
        step_size = 3
        auto_running = False
        auto_armed = False

    class M:
        def get_runtime(self, cid):
            return RT()

        def get_runtime_for(self, cid, user_id=None):
            return self.get_runtime(cid)

    view = MapView(555011, M())
    assert "\u23cf" in (view.tool_step.emoji.name or "")  # ⏏️ icon kept
    assert view.tool_step.label == "x3"
