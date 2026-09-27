"""One-time asset slicer for the v5 kit Equipment panel (demo equip UI).

Slices from the kit's psd_layers (already panel-local, V5 handoff), so we
can use exact local bboxes instead of re-measuring the source sheet:

  assets/psd_layers/Equipment/003_frame.png      -> ui/v5/layers/equip_frame.png
  assets/psd_layers/Equipment/004_title.png      -> ui/v5/layers/equip_title.png
  assets/psd_layers/Equipment/005_character.png  -> ui/v5/layers/equip_mannequin.png
  assets/psd_layers/Equipment/006_trinket.png    -> ui/v5/layers/equip_char_trinket.png
  assets/psd_layers/Equipment/007_ring.png       -> ui/v5/layers/equip_char_ring.png
  assets/psd_layers/Equipment/008_sells_light.png -> (reference; slots come
      from atoms/_slot_templates/Equipment__sells_light__0_0_14x14.png)
  assets/psd_layers/Equipment/012_sells_character.png -> equip_char_slots.png
      (the 7 character-slot surfaces composited in place — drawn behind the
      mannequin as the "empty socket" art, exactly like the kit preview)

Kit root (adjust if moved):
  C:/Users/phant/Downloads/Free-Basic-Pixel-Art-UI-SLICED-v5-RECONSTRUCTION-KIT/assets

Run once: .venv/Scripts/python scripts/slice_v5_equipment.py
"""

import os
import shutil

KIT = os.path.join(
    r"C:\Users\phant\Downloads",
    r"Free-Basic-Pixel-Art-UI-SLICED-v5-RECONSTRUCTION-KIT", "assets",
)
OUT = os.path.join("web_client", "public", "ui", "v5", "layers")

COPIES = [
    ("psd_layers/Equipment/003_frame.png", "equip_frame.png"),
    ("psd_layers/Equipment/004_title.png", "equip_title.png"),
    ("psd_layers/Equipment/005_character.png", "equip_mannequin.png"),
    ("psd_layers/Equipment/006_trinket.png", "equip_char_trinket.png"),
    ("psd_layers/Equipment/007_ring.png", "equip_char_ring.png"),
    ("psd_layers/Equipment/012_sells_character.png", "equip_char_slots.png"),
]
# Slot surface template (light 14x14) for the 4x4 right grid.
SLOT_TEMPLATE = (
    "atoms/_slot_templates/Equipment__sells_light__0_0_14x14.png",
    os.path.join("web_client", "public", "ui", "v5", "atoms", "equip_slot.png"),
)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    for src_rel, dst_name in COPIES:
        src = os.path.join(KIT, src_rel)
        dst = os.path.join(OUT, dst_name)
        shutil.copyfile(src, dst)
        print(f"OK {dst}")
    slot_src, slot_dst = SLOT_TEMPLATE
    os.makedirs(os.path.dirname(slot_dst), exist_ok=True)
    shutil.copyfile(os.path.join(KIT, slot_src), slot_dst)
    print(f"OK {slot_dst}")


if __name__ == "__main__":
    main()
