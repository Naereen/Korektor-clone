"""Crop and annotate scanned question regions using Qwen coordinates."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Optional, Tuple, cast

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


def crop_for_bbox_refinement(
    image: Image.Image, bbox_2d: list[int], margin_ratio: float = 0.2
) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """Crop around a candidate box and return its page-relative pixel bounds."""
    width, height = image.size
    left, top, right, bottom = qwen_bbox_to_pixels(bbox_2d, width, height)
    margin_x = max(16, int((right - left) * margin_ratio))
    margin_y = max(16, int((bottom - top) * margin_ratio))
    bounds = (
        max(0, left - margin_x),
        max(0, top - margin_y),
        min(width, right + margin_x),
        min(height, bottom + margin_y),
    )
    return image.crop(bounds), bounds


def qwen_crop_bbox_to_page(
    bbox_2d: list[int],
    crop_bounds: Tuple[int, int, int, int],
    page_size: Tuple[int, int],
) -> list[int]:
    """Map a normalized crop-relative Qwen box back to page coordinates."""
    crop_left, crop_top, crop_right, crop_bottom = crop_bounds
    page_width, page_height = page_size
    if page_width <= 0 or page_height <= 0:
        raise ValueError("Image dimensions must be positive")
    if not is_valid_question_box(bbox_2d):
        raise ValueError("Invalid Qwen bbox_2d")

    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top
    left, top, right, bottom = qwen_bbox_to_pixels(bbox_2d, crop_width, crop_height)
    page_box = [
        round((crop_left + left) * 1000 / page_width),
        round((crop_top + top) * 1000 / page_height),
        round((crop_left + right) * 1000 / page_width),
        round((crop_top + bottom) * 1000 / page_height),
    ]
    page_box = [min(1000, max(0, value)) for value in page_box]
    if not is_valid_question_box(page_box):
        raise ValueError("Refined Qwen bbox is too small in page coordinates")
    return page_box


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
    padding_x_pct: float = 0.04,
    padding_y_pct: float = 0.03,
) -> str:
    """Save a Qwen crop or a proportional fallback slice without raising.

    Valid boxes receive configurable page-relative margins and a translucent
    red annotation. Invalid or absent boxes use a horizontal page slice based
    on the one-based question index.
    """
    try:
        with Image.open(image_path) as source:
            image = source.convert("RGBA")
            width, height = image.size
            target_bounds: Optional[Tuple[int, int, int, int]] = None

            if is_valid_question_box(bbox_2d) and bbox_2d is not None:
                target_bounds = qwen_bbox_to_pixels(bbox_2d, width, height)
                margin_x = int(width * padding_x_pct)
                margin_y = int(height * padding_y_pct)
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
                        0,
                        0,
                        crop.width - 1,
                        crop.height - 1,
                    ),
                    outline="red",
                    width=3,
                )
                draw.rectangle(
                    (
                        left - crop_left,
                        top - crop_top,
                        right - crop_left - 1,
                        bottom - crop_top - 1,
                    ),
                    fill=(255, 0, 0, 35),
                )
                crop = Image.alpha_composite(crop, overlay)

            destination = Path(output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            crop.save(destination, format="PNG")
            return str(destination)
    except (OSError, TypeError, ValueError, ZeroDivisionError):
        return ""


def crop_and_annotate_question_locations(
    page_image_paths: Dict[int, str],
    locations: List[Dict[str, object]],
    output_path: str,
    q_index: int,
    total_questions: int,
    padding_x_pct: float = 0.04,
    padding_y_pct: float = 0.03,
) -> str:
    """Save per-page answer crops as one vertically stacked PNG composite."""
    try:
        crops: List[Image.Image] = []
        with TemporaryDirectory() as temp_dir:
            for index, location in enumerate(
                sorted(locations, key=_location_page_sort_key)
            ):
                page_number = location.get("page")
                if type(page_number) is not int or page_number not in page_image_paths:
                    continue
                bbox = location.get("bbox_2d")
                if bbox is not None and not is_valid_question_box(bbox):
                    continue
                crop_path = Path(temp_dir) / f"segment_{index}.png"
                result_path = crop_and_annotate_question(
                    page_image_paths[page_number],
                    cast(Optional[list[int]], bbox),
                    str(crop_path),
                    q_index,
                    total_questions,
                    padding_x_pct,
                    padding_y_pct,
                )
                if result_path:
                    with Image.open(result_path) as crop:
                        crops.append(crop.convert("RGBA"))

            if not crops:
                return ""

            separator_height = 3
            composite_width = max(crop.width for crop in crops)
            composite_height = sum(crop.height for crop in crops) + separator_height * (
                len(crops) - 1
            )
            composite = Image.new("RGBA", (composite_width, composite_height), "white")
            y_offset = 0
            for index, crop in enumerate(crops):
                x_offset = (composite_width - crop.width) // 2
                composite.alpha_composite(crop, (x_offset, y_offset))
                y_offset += crop.height
                if index < len(crops) - 1:
                    ImageDraw.Draw(composite).line(
                        (
                            0,
                            y_offset + separator_height // 2,
                            composite_width,
                            y_offset + separator_height // 2,
                        ),
                        fill=(128, 128, 128, 255),
                        width=1,
                    )
                    y_offset += separator_height

            destination = Path(output_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            composite.save(destination, format="PNG")
            return str(destination)
    except (OSError, TypeError, ValueError, ZeroDivisionError):
        return ""


def _location_page_sort_key(location: Dict[str, object]) -> int:
    """Return a numeric page sort key for a validated location."""
    page_number = location.get("page")
    return page_number if type(page_number) is int else 0
