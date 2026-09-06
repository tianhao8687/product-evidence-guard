from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.openvino_adapter import OpenVinoVlmBackend


class OpenVinoVlmBackendImageTests(unittest.TestCase):
    def test_context_band_keeps_full_width_and_validates_bounds(self) -> None:
        try:
            import numpy as np
            from PIL import Image, ImageOps
        except ImportError as exc:
            self.skipTest(f"OpenVINO image dependencies unavailable: {exc}")
        backend = OpenVinoVlmBackend.__new__(OpenVinoVlmBackend)
        backend._np = np
        backend._ov = type("FakeOpenVino", (), {"Tensor": staticmethod(lambda value: value)})
        backend._Image = Image
        backend._ImageOps = ImageOps
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "context.png"
            image = Image.new("RGB", (1000, 1000), "red")
            image.paste("blue", (0, 300, 1000, 600))
            image.save(path)
            tensor = backend.load_image_region(path, (0, 300, 1000, 600))
            self.assertEqual(tensor.shape, (1, 300, 1000, 3))
            self.assertTrue(np.all(tensor[0, :, :, 2] == 255))
            with self.assertRaises(ValueError):
                backend.load_image_region(path, (0, 600, 1000, 300))
            Image.new("RGB", (2, 2)).save(path)
            self.assertEqual(backend.load_image_region(path, (0, 500, 1000, 650)).shape, (1, 1, 2, 3))

    def test_large_image_is_resized_before_tensor_conversion(self) -> None:
        try:
            import numpy as np
            from PIL import Image, ImageOps
        except ImportError as exc:
            self.skipTest(f"OpenVINO image dependencies unavailable: {exc}")

        class FakeOpenVino:
            @staticmethod
            def Tensor(value):
                return value

        backend = OpenVinoVlmBackend.__new__(OpenVinoVlmBackend)
        backend._np = np
        backend._ov = FakeOpenVino
        backend._Image = Image
        backend._ImageOps = ImageOps

        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "large.jpg"
            Image.new("RGB", (2000, 1000), color="white").save(image_path)
            tensor = backend.load_image(image_path)

        self.assertEqual(tensor.shape, (1, 512, 1024, 3))


if __name__ == "__main__":
    unittest.main()
