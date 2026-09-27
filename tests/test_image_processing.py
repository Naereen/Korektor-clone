"""Tests for Qwen coordinate conversion and resilient page crops."""

from PIL import Image

from correct_ai.image_processing import (
    crop_and_annotate_question,
    crop_and_annotate_question_locations,
    crop_for_bbox_refinement,
    is_valid_question_box,
    qwen_crop_bbox_to_page,
    qwen_bbox_to_pixels,
)


def test_qwen_bbox_to_pixels_uses_xy_order():
    """Qwen bounds use xmin, ymin, xmax, ymax on a 0-1000 scale."""
    assert qwen_bbox_to_pixels([100, 250, 900, 450], 200, 100) == (20, 25, 180, 45)


def test_refinement_crop_and_coordinates_map_back_to_page():
    """Local refinement coordinates are mapped through the crop origin."""
    image = Image.new("RGB", (1000, 1000), "white")
    crop, bounds = crop_for_bbox_refinement(image, [100, 100, 500, 500])

    assert bounds == (20, 20, 580, 580)
    assert crop.size == (560, 560)
    assert qwen_crop_bbox_to_page([100, 200, 900, 800], bounds, image.size) == [
        76,
        132,
        524,
        468,
    ]


def test_crop_and_annotate_question_adds_margin_and_overlay(tmp_path):
    """A valid Qwen box gets page-relative margins and a red overlay."""
    source = tmp_path / "page.png"
    image = Image.new("RGB", (200, 100), "white")
    image.paste("blue", (20, 25, 180, 45))
    image.save(source)
    destination = tmp_path / "snippets" / "question.png"

    result_path = crop_and_annotate_question(
        str(source), [100, 250, 900, 450], str(destination), 1, 4
    )

    assert result_path == str(destination)
    with Image.open(destination) as result:
        assert result.format == "PNG"
        assert result.size == (176, 26)
        assert result.getpixel((0, 0))[0] > result.getpixel((0, 0))[2]
        assert 0 < result.getpixel((10, 10))[0] < result.getpixel((10, 10))[2]
        assert result.getpixel((88, 13))[2] > result.getpixel((88, 13))[1]


def test_crop_and_annotate_question_accepts_custom_padding(tmp_path):
    """Custom page-relative padding controls the crop bounds."""
    source = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(source)
    destination = tmp_path / "custom-padding.png"

    result_path = crop_and_annotate_question(
        str(source),
        [100, 250, 900, 450],
        str(destination),
        1,
        4,
        padding_x_pct=0.1,
        padding_y_pct=0.05,
    )

    assert result_path == str(destination)
    with Image.open(destination) as result:
        assert result.size == (200, 30)


def test_multi_page_locations_are_stacked_in_page_order(tmp_path):
    """Page-scoped answer segments produce one vertically ordered composite."""
    first_page = tmp_path / "page-1.png"
    second_page = tmp_path / "page-2.png"
    Image.new("RGB", (100, 100), "blue").save(first_page)
    Image.new("RGB", (100, 100), "green").save(second_page)
    destination = tmp_path / "composite.png"

    result_path = crop_and_annotate_question_locations(
        {1: str(first_page), 2: str(second_page)},
        [
            {"page": 2, "bbox_2d": [100, 100, 500, 500]},
            {"page": 1, "bbox_2d": [100, 100, 500, 500]},
        ],
        str(destination),
        1,
        2,
    )

    assert result_path == str(destination)
    with Image.open(destination) as result:
        assert result.size == (48, 95)
        assert result.getpixel((24, 24))[2] > result.getpixel((24, 24))[1]
        assert result.getpixel((24, 72))[1] > result.getpixel((24, 72))[2]
        assert result.getpixel((24, 47))[:3] == (128, 128, 128)


def test_invalid_box_uses_horizontal_fallback(tmp_path):
    """An invalid model box produces a safe proportional slice without red ink."""
    source = tmp_path / "page.png"
    image = Image.new("RGB", (100, 400), "white")
    image.paste("blue", (0, 100, 100, 200))
    image.save(source)
    destination = tmp_path / "fallback.png"

    result_path = crop_and_annotate_question(str(source), [55, 80, 62, 980], str(destination), 2, 4)

    assert result_path == str(destination)
    with Image.open(destination) as result:
        assert result.size == (100, 100)
        assert result.getpixel((50, 50))[:3] == (0, 0, 255)
        assert result.getpixel((0, 0))[:3] == (0, 0, 255)


def test_missing_box_uses_clamped_fallback_index(tmp_path):
    """Missing bounds and out-of-range indices cannot crash crop generation."""
    source = tmp_path / "page.png"
    Image.new("RGB", (90, 90), "white").save(source)
    destination = tmp_path / "fallback.png"

    assert crop_and_annotate_question(str(source), None, str(destination), 99, 3) == str(
        destination
    )
    with Image.open(destination) as result:
        assert result.size == (90, 30)


def test_invalid_source_returns_empty_path(tmp_path):
    """Unreadable source images do not propagate an exception to Flask workers."""
    result = crop_and_annotate_question(
        str(tmp_path / "missing.png"), None, str(tmp_path / "out.png"), 1, 1
    )
    assert result == ""


def test_question_box_validation_uses_qwen_dimensions():
    """Both Qwen width and height must exceed twenty normalized units."""
    assert is_valid_question_box([100, 250, 900, 450])
    assert not is_valid_question_box([100, 250, 120, 450])
    assert not is_valid_question_box([100, 250, 900, 270])
    assert not is_valid_question_box([100, 250, 1001, 450])
