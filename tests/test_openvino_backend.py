from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.openvino_adapter import OpenVinoVlmBackend


class OpenVinoVlmBackendImageTests(unittest.TestCase):
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
