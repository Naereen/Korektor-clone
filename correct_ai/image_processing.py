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
        bounds = (
            round(left * width / 1000),
            round(top * height / 1000),
            round(right * width / 1000),
            round(bottom * height / 1000),
        )
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise ValueError("box_2d must cover at least one pixel in each dimension")
        crop = source.crop(bounds).convert("RGBA")

    overlay = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle(
        (0, 0, crop.width - 1, crop.height - 1),
        fill=(220, 38, 38, 51),
        outline=(220, 38, 38, 255),
        width=3,
    )
    result = Image.alpha_composite(crop, overlay)
    if label:
        ImageDraw.Draw(result).text((4, 4), label, fill=(220, 38, 38, 255))

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(destination, format="PNG")
    return output_path
