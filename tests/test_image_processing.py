"""Tests for normalized page crops and their visible annotations."""

import pytest
from PIL import Image

from correct_ai.image_processing import crop_and_annotate_question


def test_crop_and_annotate_question(tmp_path):
    """The PNG crop retains its requested pixels and translucent red overlay."""
    source = tmp_path / "page.png"
    image = Image.new("RGB", (200, 100), "white")
    image.paste("blue", (50, 25, 150, 75))
    image.save(source)
    destination = tmp_path / "snippets" / "question.png"

    assert crop_and_annotate_question(str(source), [250, 250, 750, 750], str(destination)) == str(
        destination
    )

    with Image.open(destination) as result:
        assert result.format == "PNG"
        assert result.size == (100, 50)
        assert result.getpixel((0, 0)) == (220, 38, 38, 255)
        assert result.getpixel((50, 25)) == (44, 8, 212, 255)
    with Image.open(source) as original:
        assert original.getpixel((50, 25)) == (0, 0, 255)


@pytest.mark.parametrize(
    "bounds",
    [[-1, 0, 100, 100], [0, 0, 1001, 100], [500, 0, 500, 100], [0, 100, 100, 50], [0, 0, 100]],
)
def test_crop_rejects_invalid_bounds(tmp_path, bounds):
    """Invalid normalized coordinates cannot silently produce a wrong crop."""
    with pytest.raises(ValueError):
        crop_and_annotate_question(str(tmp_path / "missing.png"), bounds, str(tmp_path / "out.png"))


def test_crop_rejects_subpixel_bounds(tmp_path):
    """Distinct normalized bounds can still describe an empty pixel crop."""
    source = tmp_path / "tiny.png"
    Image.new("RGB", (10, 10), "white").save(source)
    with pytest.raises(ValueError, match="at least one pixel"):
        crop_and_annotate_question(str(source), [0, 0, 1, 1], str(tmp_path / "out.png"))


def test_crop_adds_vertical_context_around_thin_box(tmp_path):
    """Thin boxes retain nearby handwriting while highlighting only the target band."""
    source = tmp_path / "page.png"
    Image.new("RGB", (1000, 1000), "white").save(source)
    destination = tmp_path / "context.png"

    crop_and_annotate_question(
        str(source), [60, 50, 75, 980], str(destination), label="Réponse incomplète"
    )

    with Image.open(destination) as result:
        assert result.size == (930, 160)
        assert result.getpixel((10, 10)) == (255, 255, 255, 255)
        assert result.getpixel((10, 70)) == (248, 212, 212, 255)


def test_crop_draws_optional_label(tmp_path):
    """The optional label is visible in the generated crop."""
    source = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(source)
    plain = tmp_path / "plain.png"
    labeled = tmp_path / "labeled.png"
    crop_and_annotate_question(str(source), [0, 0, 1000, 1000], str(plain))
    crop_and_annotate_question(str(source), [0, 0, 1000, 1000], str(labeled), label="Q1")

    with Image.open(plain) as plain_image, Image.open(labeled) as labeled_image:
        assert plain_image.tobytes() != labeled_image.tobytes()
