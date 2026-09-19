#!/usr/bin/env python3
"""Tiny self-check for export resizing helpers."""

from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from crop_sprite_frames import prepare_output_dir, resize_rgba_for_export, resolve_output_size, scaled_size


def main() -> int:
    image = Image.new("RGBA", (10, 6), (0, 0, 0, 0))
    image.paste((240, 120, 40, 255), (2, 1, 8, 5))

    resized = resize_rgba_for_export(image, {"width": 5, "height": 3})
    padded = resize_rgba_for_export(image, {"width": 5, "height": 5})
    assert resized.mode == "RGBA"
    assert resized.size == (5, 3)
    assert padded.size == (5, 5)
    assert padded.getbbox()[3] - padded.getbbox()[1] == 3
    assert scaled_size((101, 63), 25) == {"width": 25, "height": 16}
    assert resolve_output_size({"width": 10, "height": 6}, None, 50) == {"width": 5, "height": 3}
    assert resolve_output_size({"width": 10, "height": 6}, {"width": 7, "height": 9}, 50) == {"width": 7, "height": 9}
    assert resized.getchannel("A").getextrema()[1] > 0

    with TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for name in ("frame_0000.png", "frame_0011.png", "crop-meta.json", "animation-sequence.json", "contact_sheet_crop.jpg"):
            (folder / name).write_text("stale", encoding="utf-8")
        (folder / "keep.txt").write_text("user file", encoding="utf-8")
        prepare_output_dir(folder, overwrite=True)
        assert not list(folder.glob("frame_*.png"))
        assert not (folder / "crop-meta.json").exists()
        assert (folder / "keep.txt").exists()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
