"""Crop and annotate scanned question regions using normalized coordinates."""

from pathlib import Path

from PIL import Image, ImageDraw


def crop_and_annotate_question(
    image_path: str, box_2d: list[int], output_path: str, label: str = ""
) -> str:
    """Save an annotated PNG crop of a question on a scanned page.

    ``box_2d`` is ``[ymin, xmin, ymax, xmax]`` on a 0-1000 scale.
    Invalid bounds or a region smaller than one image pixel raise ValueError.
    The source image is never modified; output directories are created as needed.

    Args:
        image_path: Path to the source page image.
        box_2d: Normalized bounds in top, left, bottom, right order.
        output_path: Destination for the generated PNG.
        label: Optional text drawn inside the upper left of the crop.

    Returns:
        The provided output path on success.
    """
    if (
        len(box_2d) != 4
        or any(not isinstance(value, int) or isinstance(value, bool) for value in box_2d)
        or not (0 <= box_2d[0] < box_2d[2] <= 1000)
        or not (0 <= box_2d[1] < box_2d[3] <= 1000)
    ):
        raise ValueError("box_2d must contain ordered integer bounds between 0 and 1000")

    with Image.open(image_path) as source:
        width, height = source.size
        top, left, bottom, right = box_2d
        target_bounds = (
            round(left * width / 1000),
            round(top * height / 1000),
            round(right * width / 1000),
            round(bottom * height / 1000),
        )
        if target_bounds[0] >= target_bounds[2] or target_bounds[1] >= target_bounds[3]:
            raise ValueError("box_2d must cover at least one pixel in each dimension")

        min_context_height = round(height * 160 / 1000)
        crop_top, crop_bottom = target_bounds[1], target_bounds[3]
        if crop_bottom - crop_top < min_context_height:
            center = (crop_top + crop_bottom) / 2
            crop_top = max(0, round(center - min_context_height / 2))
            crop_bottom = min(height, crop_top + min_context_height)
            crop_top = max(0, crop_bottom - min_context_height)

        bounds = (target_bounds[0], crop_top, target_bounds[2], crop_bottom)
        crop = source.crop(bounds).convert("RGBA")

    overlay = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    target_left = target_bounds[0] - bounds[0]
    target_top = target_bounds[1] - bounds[1]
    target_right = target_bounds[2] - bounds[0]
    target_bottom = target_bounds[3] - bounds[1]
    draw.rectangle(
        (target_left, target_top, target_right - 1, target_bottom - 1),
        fill=(220, 38, 38, 51),
        outline=(220, 38, 38, 255),
        width=3,
    )
    result = Image.alpha_composite(crop, overlay)
    if label:
        label_draw = ImageDraw.Draw(result)
        label_bounds = label_draw.textbbox((0, 0), label)
        label_height = label_bounds[3] - label_bounds[1]
        label_top = target_top - label_height - 4
        if label_top < 0:
            label_top = min(target_bottom + 4, crop.height - label_height)
        label_position = (target_left + 4, label_top)
        label_draw.text(label_position, label, fill=(220, 38, 38, 255))

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(destination, format="PNG")
    return output_path
