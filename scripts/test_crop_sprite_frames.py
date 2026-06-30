#!/usr/bin/env python3
"""Tiny self-check for export resizing helpers."""

from PIL import Image

from crop_sprite_frames import resize_rgba_for_export, resolve_output_size, scaled_size


def main() -> int:
    image = Image.new("RGBA", (10, 6), (0, 0, 0, 0))
    image.paste((240, 120, 40, 255), (2, 1, 8, 5))

    resized = resize_rgba_for_export(image, {"width": 5, "height": 3})
    assert resized.mode == "RGBA"
    assert resized.size == (5, 3)
    assert scaled_size((101, 63), 25) == {"width": 25, "height": 16}
    assert resolve_output_size({"width": 10, "height": 6}, None, 50) == {"width": 5, "height": 3}
    assert resolve_output_size({"width": 10, "height": 6}, {"width": 7, "height": 9}, 50) == {"width": 7, "height": 9}
    assert resized.getchannel("A").getextrema()[1] > 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
