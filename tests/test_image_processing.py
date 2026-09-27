"""Tests for Qwen coordinate conversion and resilient page crops."""

from PIL import Image

from correct_ai.image_processing import (
    crop_and_annotate_question,
    is_valid_question_box,
    qwen_bbox_to_pixels,
)


def test_qwen_bbox_to_pixels_uses_xy_order():
    """Qwen bounds use xmin, ymin, xmax, ymax on a 0-1000 scale."""
    assert qwen_bbox_to_pixels([100, 250, 900, 450], 200, 100) == (20, 25, 180, 45)


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
        assert result.size == (172, 26)
        assert result.getpixel((6, 3))[0] > result.getpixel((6, 3))[2]
        assert result.getpixel((86, 13))[2] > result.getpixel((86, 13))[1]


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
