from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from product_evidence_guard.parsers import (
    RenderedPdfPage,
    RenderedPdfVisualRegion,
    render_pdf_visual_region,
)


IMAGE = 1
FORM = 2
TEXT = 3


class _FakeVisual:
    def __init__(
        self,
        *,
        object_type: int,
        bounds: tuple[float, float, float, float],
        pixels: tuple[int, int] | None = None,
        children: list["_FakeVisual"] | None = None,
    ) -> None:
        self.object_type = object_type
        self.bounds = bounds
        self.pixels = pixels
        self.children = children or []
        self.closed = False

    def get_bounds(self) -> tuple[float, float, float, float]:
        return self.bounds

    def get_px_size(self) -> tuple[int, int]:
        if self.pixels is None:
            raise RuntimeError("not an image")
        return self.pixels

    def close(self) -> None:
        self.closed = True


class _FakeImage:
    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.closed = False

    def save(self, path: Path, format: str) -> None:
        if format != "PNG":
            raise AssertionError(format)
        path.write_bytes(b"fake png")

    def close(self) -> None:
        self.closed = True


class _FakeBitmap:
    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        self.closed = False

    def to_pil(self) -> _FakeImage:
        return _FakeImage(self.size)

    def close(self) -> None:
        self.closed = True


class _FakePage:
    def __init__(
        self,
        *,
        size: tuple[float, float],
        rotation: int,
        objects: list[_FakeVisual],
        render_calls: list[dict[str, object]],
        crop_box: tuple[float, float, float, float],
        fail_roi_render: bool = False,
    ) -> None:
        self.size = size
        self.rotation = rotation
        self.objects = objects
        self.render_calls = render_calls
        self.crop_box = crop_box
        self.fail_roi_render = fail_roi_render
        self.closed = False

    def get_size(self) -> tuple[float, float]:
        return self.size

    def get_rotation(self) -> int:
        return self.rotation

    def get_cropbox(self) -> tuple[float, float, float, float]:
        return self.crop_box

    def get_objects(
        self,
        *,
        filter: list[int],
        max_depth: int,
        form: _FakeVisual | None = None,
    ):
        source = form.children if form is not None else self.objects
        requested = set(filter)
        return (
            visual
            for visual in source
            if visual.object_type in requested
        )

    def render(
        self,
        *,
        scale: float,
        crop: tuple[float, float, float, float] = (0, 0, 0, 0),
    ) -> _FakeBitmap:
        if self.fail_roi_render and any(crop):
            raise RuntimeError("fake ROI render failure")
        width = round((self.size[0] - crop[0] - crop[2]) * scale)
        height = round((self.size[1] - crop[1] - crop[3]) * scale)
        self.render_calls.append({"scale": scale, "crop": crop})
        return _FakeBitmap((width, height))

    def close(self) -> None:
        self.closed = True


def _fake_pdfium(
    *,
    size: tuple[float, float] = (600.0, 800.0),
    rotation: int = 0,
    crop_box: tuple[float, float, float, float] | None = None,
    objects_factory,
    fail_roi_render: bool = False,
) -> tuple[object, list[dict[str, object]]]:
    render_calls: list[dict[str, object]] = []

    class FakeDocument:
        def __init__(self, path: str) -> None:
            self.page = _FakePage(
                size=size,
                rotation=rotation,
                objects=objects_factory(),
                render_calls=render_calls,
                crop_box=crop_box or (0.0, 0.0, size[0], size[1]),
                fail_roi_render=fail_roi_render,
            )

        def __len__(self) -> int:
            return 1

        def __getitem__(self, index: int) -> _FakePage:
            if index != 0:
                raise IndexError(index)
            return self.page

        def close(self) -> None:
            pass

    fake = types.SimpleNamespace(
        PdfDocument=FakeDocument,
        raw=types.SimpleNamespace(
            FPDF_PAGEOBJ_IMAGE=IMAGE,
            FPDF_PAGEOBJ_FORM=FORM,
            FPDF_PAGEOBJ_TEXT=TEXT,
        ),
    )
    return fake, render_calls


class PdfVisualRoiTests(unittest.TestCase):
    def test_crops_form_with_large_image_and_snaps_nearby_text(self) -> None:
        def objects() -> list[_FakeVisual]:
            large_image = _FakeVisual(
                object_type=IMAGE,
                bounds=(0.0, 0.0, 200.0, 140.0),
                pixels=(1000, 700),
            )
            form = _FakeVisual(
                object_type=FORM,
                bounds=(300.0, 200.0, 500.0, 450.0),
                children=[large_image],
            )
            caption = _FakeVisual(
                object_type=TEXT,
                bounds=(320.0, 160.0, 480.0, 190.0),
            )
            return [form, caption]

        fake_pdfium, render_calls = _fake_pdfium(
            objects_factory=objects,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
                rendered = render_pdf_visual_region(
                    Path("mixed.pdf"),
                    1,
                    output,
                )

            self.assertIsInstance(rendered, RenderedPdfPage)
            self.assertIsInstance(rendered, RenderedPdfVisualRegion)
            self.assertEqual(
                rendered.strategy,
                "pdfium_top_level_raster_union_v1",
            )
            self.assertIsNone(rendered.fallback_reason)
            self.assertEqual(
                rendered.crop_box_pdf,
                (264.0, 124.0, 536.0, 486.0),
            )
            self.assertEqual(rendered.page_width_pdf, 600.0)
            self.assertEqual(rendered.page_height_pdf, 800.0)
            self.assertAlmostEqual(rendered.crop_ratio, 0.205133, places=6)
            self.assertEqual(rendered.pixel_width, 544)
            self.assertEqual(rendered.pixel_height, 724)
            self.assertEqual(rendered.image_path.name, "page-0001-roi.png")
            self.assertEqual(rendered.image_path.parent, output)
            self.assertTrue(rendered.image_path.is_file())

        self.assertEqual(
            render_calls,
            [
                {
                    "scale": 2.0,
                    "crop": (264.0, 124.0, 64.0, 314.0),
                }
            ],
        )

    def test_crops_large_top_level_image(self) -> None:
        def objects() -> list[_FakeVisual]:
            return [
                _FakeVisual(
                    object_type=IMAGE,
                    bounds=(120.0, 240.0, 360.0, 520.0),
                    pixels=(1200, 900),
                )
            ]

        fake_pdfium, render_calls = _fake_pdfium(
            objects_factory=objects,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
                rendered = render_pdf_visual_region(
                    Path("image.pdf"),
                    1,
                    Path(temporary),
                )

        self.assertEqual(
            rendered.crop_box_pdf,
            (84.0, 204.0, 396.0, 556.0),
        )
        self.assertEqual(len(render_calls), 1)
        self.assertTrue(any(render_calls[0]["crop"]))

    def test_absorbs_neighboring_visual_instead_of_slicing_it(self) -> None:
        def objects() -> list[_FakeVisual]:
            large_image = _FakeVisual(
                object_type=IMAGE,
                bounds=(0.0, 0.0, 200.0, 140.0),
                pixels=(1000, 700),
            )
            left_chart = _FakeVisual(
                object_type=FORM,
                bounds=(100.0, 260.0, 295.0, 430.0),
            )
            image_form = _FakeVisual(
                object_type=FORM,
                bounds=(320.0, 260.0, 535.0, 430.0),
                children=[large_image],
            )
            return [left_chart, image_form]

        fake_pdfium, render_calls = _fake_pdfium(
            objects_factory=objects,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
                rendered = render_pdf_visual_region(
                    Path("neighbor.pdf"),
                    1,
                    Path(temporary),
                )

        self.assertEqual(
            rendered.crop_box_pdf,
            (64.0, 224.0, 571.0, 466.0),
        )
        self.assertEqual(rendered.strategy, "pdfium_top_level_raster_union_v1")
        self.assertEqual(len(render_calls), 1)

    def test_no_reliable_raster_region_falls_back_to_full_page(self) -> None:
        fake_pdfium, render_calls = _fake_pdfium(
            objects_factory=list,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
                rendered = render_pdf_visual_region(
                    Path("vector.pdf"),
                    1,
                    output,
                )

            self.assertEqual(rendered.strategy, "full_page")
            self.assertEqual(
                rendered.fallback_reason,
                "no_reliable_raster_region",
            )
            self.assertEqual(
                rendered.crop_box_pdf,
                (0.0, 0.0, 600.0, 800.0),
            )
            self.assertEqual(rendered.page_width_pdf, 600.0)
            self.assertEqual(rendered.page_height_pdf, 800.0)
            self.assertEqual(rendered.crop_ratio, 1.0)
            self.assertEqual(rendered.image_path.name, "page-0001.png")
            self.assertEqual(rendered.image_path.parent, output)
            self.assertTrue(rendered.image_path.is_file())

        self.assertEqual(
            render_calls,
            [{"scale": 2.0, "crop": (0, 0, 0, 0)}],
        )

    def test_rotated_page_falls_back_to_full_page(self) -> None:
        fake_pdfium, render_calls = _fake_pdfium(
            rotation=90,
            objects_factory=list,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
                rendered = render_pdf_visual_region(
                    Path("rotated.pdf"),
                    1,
                    Path(temporary),
                )

        self.assertEqual(rendered.strategy, "full_page")
        self.assertEqual(
            rendered.fallback_reason,
            "page_rotation_not_supported",
        )
        self.assertEqual(len(render_calls), 1)
        self.assertFalse(any(render_calls[0]["crop"]))

    def test_nonzero_cropbox_origin_falls_back_to_full_page(self) -> None:
        def objects() -> list[_FakeVisual]:
            return [
                _FakeVisual(
                    object_type=IMAGE,
                    bounds=(326.5, 260.3, 536.6, 423.7),
                    pixels=(1400, 1000),
                )
            ]

        fake_pdfium, render_calls = _fake_pdfium(
            size=(512.0, 692.0),
            crop_box=(50.0, 50.0, 562.0, 742.0),
            objects_factory=objects,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
                rendered = render_pdf_visual_region(
                    Path("offset-cropbox.pdf"),
                    1,
                    Path(temporary),
                )

        self.assertEqual(rendered.strategy, "full_page")
        self.assertEqual(
            rendered.fallback_reason,
            "nonzero_or_offset_page_cropbox_not_supported",
        )
        self.assertEqual(len(render_calls), 1)
        self.assertFalse(any(render_calls[0]["crop"]))

    def test_crop_covering_eighty_percent_falls_back(self) -> None:
        def objects() -> list[_FakeVisual]:
            large_image = _FakeVisual(
                object_type=IMAGE,
                bounds=(0.0, 0.0, 560.0, 760.0),
                pixels=(2000, 2000),
            )
            return [large_image]

        fake_pdfium, render_calls = _fake_pdfium(
            objects_factory=objects,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
                rendered = render_pdf_visual_region(
                    Path("nearly-full.pdf"),
                    1,
                    Path(temporary),
                )

        self.assertEqual(rendered.strategy, "full_page")
        self.assertEqual(
            rendered.fallback_reason,
            "crop_area_ratio_limit",
        )
        self.assertEqual(len(render_calls), 1)
        self.assertFalse(any(render_calls[0]["crop"]))

    def test_roi_render_failure_fails_open_and_removes_partial_path(self) -> None:
        def objects() -> list[_FakeVisual]:
            return [
                _FakeVisual(
                    object_type=IMAGE,
                    bounds=(120.0, 240.0, 360.0, 520.0),
                    pixels=(1200, 900),
                )
            ]

        fake_pdfium, render_calls = _fake_pdfium(
            objects_factory=objects,
            fail_roi_render=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
                rendered = render_pdf_visual_region(
                    Path("render-failure.pdf"),
                    1,
                    output,
                )

            self.assertEqual(rendered.strategy, "full_page")
            self.assertEqual(
                rendered.fallback_reason,
                "roi_render_failed:RuntimeError",
            )
            self.assertEqual(rendered.image_path.name, "page-0001.png")
            self.assertFalse((output / "page-0001-roi.png").exists())

        self.assertEqual(
            render_calls,
            [{"scale": 2.0, "crop": (0, 0, 0, 0)}],
        )


if __name__ == "__main__":
    unittest.main()
