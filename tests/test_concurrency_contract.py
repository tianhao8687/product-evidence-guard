from pathlib import Path
import tempfile
import threading
import unittest

from product_evidence_guard.confirmation import ConfirmationRequestError
from product_evidence_guard.task_jobs import TaskJobs


class ConcurrencyContractTests(unittest.TestCase):
    def test_eight_active_outputs_are_accepted_and_ninth_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = threading.Event()
            def execute(_payload, _progress):
                release.wait(5)
                return {"ok": True}
            jobs = TaskJobs(Path(temporary), execute)
            try:
                ids = [jobs.submit({"input_dir": str(Path(temporary) / f"input-{i}"),
                                    "output_dir": str(Path(temporary) / f"output-{i}")})["job_id"]
                       for i in range(8)]
                self.assertEqual(len(set(ids)), 8)
                with self.assertRaises(ConfirmationRequestError):
                    jobs.submit({"input_dir": str(Path(temporary) / "input-9"),
                                 "output_dir": str(Path(temporary) / "output-9")})
                # Repeating the same output returns its existing active job.
                duplicate = jobs.submit({"input_dir": str(Path(temporary) / "input-0"),
                                         "output_dir": str(Path(temporary) / "output-0")})
                self.assertEqual(duplicate["job_id"], ids[0])
            finally:
                release.set()
                for thread in jobs.threads:
                    thread.join(5)


if __name__ == "__main__":
    unittest.main()
