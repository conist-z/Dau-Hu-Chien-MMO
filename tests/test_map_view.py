import discord
from discord_ui.map_view import MapView


class _FakeManager:
    def get_runtime(self, channel_id):
        return None


def _cids(view):
    return {item.custom_id.split(":")[-1] for item in view.children}


def test_map_view_layout_tool_rail_right_and_empty_centre():
    view = MapView(555010, _FakeManager())
    cids = _cids(view)

    # All 8 directions + 3 tool buttons + 1 disabled spacer (empty centre) present.
    for key in ["t_refresh", "t_step", "t_auto", "nw", "n", "ne", "w", "s", "e", "sw", "se", "blank"]:
        assert key in cids, key

    # Tool rail sits on the RIGHT column (row == its d-pad row), not its own row.
    by_cid = {item.custom_id.split(":")[-1]: item for item in view.children}
    assert by_cid["t_refresh"].row == 0
    assert by_cid["t_step"].row == 1
    assert by_cid["t_auto"].row == 2

    # Centre cell (row 1, middle column) is the empty/disabled spacer.
    centre = [it for it in view.children if it.row == 1 and not it.custom_id.endswith(("w", "e", "t_step"))]
    assert len(centre) == 1 and centre[0].disabled, "centre must be an empty disabled cell"


def test_tool_step_shows_count_label():
    class RT:
        step_size = 3
        auto_running = False
        auto_armed = False

    class M:
        def get_runtime(self, cid):
            return RT()

    view = MapView(555011, M())
    assert "\u23cf" in (view.tool_step.emoji.name or "")  # ⏏️ icon kept
    assert view.tool_step.label == "x3"
