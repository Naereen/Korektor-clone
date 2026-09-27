"""Crop and annotate scanned question regions using Qwen coordinates."""

from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw


def is_valid_question_box(bbox_2d: object) -> bool:
    """Return whether a Qwen box is ordered, normalized, and large enough."""
    return bool(
        isinstance(bbox_2d, list)
        and len(bbox_2d) == 4
        and all(type(value) is int for value in bbox_2d)
        and 0 <= bbox_2d[0] < bbox_2d[2] <= 1000
        and 0 <= bbox_2d[1] < bbox_2d[3] <= 1000
        and bbox_2d[2] - bbox_2d[0] > 20
        and bbox_2d[3] - bbox_2d[1] > 20
    )


def qwen_bbox_to_pixels(bbox_2d: list[int], width: int, height: int) -> Tuple[int, int, int, int]:
    """Convert normalized Qwen ``[xmin, ymin, xmax, ymax]`` bounds to pixels."""
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    if not is_valid_question_box(bbox_2d):
        raise ValueError("Invalid Qwen bbox_2d")
    xmin, ymin, xmax, ymax = bbox_2d
    return (
        max(0, int((xmin / 1000.0) * width)),
        max(0, int((ymin / 1000.0) * height)),
        min(width, int((xmax / 1000.0) * width)),
        min(height, int((ymax / 1000.0) * height)),
    )


def _fallback_bounds(
    width: int, height: int, q_index: int, total_questions: int
) -> Tuple[int, int, int, int]:
    """Return a horizontal page slice for one one-based question index."""
    safe_total = max(1, total_questions)
    safe_index = min(max(1, q_index), safe_total)
    ymin = int(((safe_index - 1) / safe_total) * height)
    ymax = int((safe_index / safe_total) * height)
    return (0, ymin, width, max(ymin + 1, ymax))


def crop_and_annotate_question(
    image_path: str,
    bbox_2d: Optional[list[int]],
    output_path: str,
    q_index: int,
    total_questions: int,
) -> str:
    """Save a Qwen crop or a proportional fallback slice without raising.

    Valid boxes receive a three-percent page margin and a translucent red
    annotation. Invalid or absent boxes use a horizontal page slice based on
    the one-based question index.
    """
    try:
        with Image.open(image_path) as source:
            image = source.convert("RGBA")
            width, height = image.size
            target_bounds: Optional[Tuple[int, int, int, int]] = None

            if is_valid_question_box(bbox_2d):
                target_bounds = qwen_bbox_to_pixels(bbox_2d, width, height)
                margin_x = int(width * 0.03)
                margin_y = int(height * 0.03)
                left, top, right, bottom = target_bounds
                crop_bounds = (
                    max(0, left - margin_x),
                    max(0, top - margin_y),
                    min(width, right + margin_x),
                    min(height, bottom + margin_y),
                )
            else:
                crop_bounds = _fallback_bounds(width, height, q_index, total_questions)

            crop = image.crop(crop_bounds)
            if target_bounds is not None:
                left, top, right, bottom = target_bounds
                crop_left, crop_top, _, _ = crop_bounds
                overlay = Image.new("RGBA", crop.size, (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                draw.rectangle(
                    (
                        left - crop_left,
                        top - crop_top,
                        right - crop_left - 1,
                        bottom - crop_top - 1,
                    ),
                    fill=(255, 0, 0, 45),
                    outline="red",
                    width=3,
                )
                crop = Image.alpha_composite(crop, overlay)

            destination = Path(output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            crop.save(destination, format="PNG")
            return str(destination)
    except (OSError, TypeError, ValueError, ZeroDivisionError):
        return ""
