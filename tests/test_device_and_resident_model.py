from __future__ import annotations

from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from product_evidence_guard.openvino_adapter import (
    OpenVinoDeviceSelection,
    _strict_json_array,
    resolve_openvino_device,
)


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import client  # noqa: E402
from server import (  # noqa: E402
    ResidentAnalysisWorker,
    ResidentModelCache,
    _accept_connections,
)


class FakeCore:
    def __init__(self, devices: dict[str, str]) -> None:
        self.available_devices = list(devices)
        self._devices = devices

    def get_property(self, device: str, _name: str) -> str:
        return self._devices[device]


class DeviceSelectionTests(unittest.TestCase):
    def test_optional_llm_array_parser_requires_complete_top_level_array(self) -> None:
        self.assertEqual(
            _strict_json_array('[{"field":"net_weight"}]'),
            [{"field": "net_weight"}],
        )
        self.assertEqual(
            _strict_json_array('```json\n[{"field":"net_weight"}]\n```'),
            [{"field": "net_weight"}],
        )
        self.assertEqual(
            _strict_json_array('explanation [{"field":"net_weight"}]'),
            [],
        )
        self.assertEqual(
            _strict_json_array('{"items":[{"field":"net_weight"}]}'),
            [],
        )

    def test_auto_prefers_only_an_intel_gpu(self) -> None:
        intel = FakeCore({"CPU": "Example CPU", "GPU": "Intel(R) Arc"})
        nvidia = FakeCore({"CPU": "Example CPU", "GPU": "NVIDIA RTX"})

        self.assertEqual(resolve_openvino_device("AUTO", core=intel).actual, "GPU")
        selected = resolve_openvino_device("AUTO", core=nvidia)
        self.assertEqual(selected.actual, "CPU")
        self.assertEqual(selected.policy, "auto_cpu_fallback")

    def test_explicit_device_must_exist_and_npu_is_not_advertised(self) -> None:
        core = FakeCore({"CPU": "Example CPU", "GPU": "NVIDIA RTX"})

        self.assertEqual(resolve_openvino_device("GPU", core=core).actual, "GPU")
        with self.assertRaisesRegex(RuntimeError, "不可用"):
            resolve_openvino_device("GPU.9", core=core)
        with self.assertRaisesRegex(RuntimeError, "拒绝使用 NPU"):
            resolve_openvino_device("NPU", core=core)


class ResidentModelCacheTests(unittest.TestCase):
    def test_progress_heartbeats_reset_the_per_stage_deadline(self) -> None:
        class Process:
            exitcode = None

            def __init__(self) -> None:
                self.alive = True

            def is_alive(self) -> bool:
                return self.alive

            def join(self, timeout: float) -> None:
                del timeout

            def terminate(self) -> None:
                self.alive = False

        class Connection:
            def __init__(self) -> None:
                self.job_id = ""
                self.index = 0

            def send(self, message: object) -> None:
                if isinstance(message, dict) and message.get("job_id"):
                    self.job_id = str(message["job_id"])

            def poll(self, timeout: float) -> bool:
                del timeout
                time.sleep(0.03)
                return True

            def recv(self) -> dict[str, object]:
                responses = (
                    {"type": "model_ready", "job_id": self.job_id},
                    {
                        "type": "progress",
                        "job_id": self.job_id,
                        "stage": "file_started",
                    },
                    {
                        "type": "result",
                        "job_id": self.job_id,
                        "summary": {"candidate_count": 1},
                    },
                )
                response = responses[self.index]
                self.index += 1
                return response

            def close(self) -> None:
                return None

        worker = ResidentAnalysisWorker()
        worker._process = Process()
        worker._connection = Connection()
        try:
            summary = worker.analyze(
                {"input_dir": "x", "output_dir": "y"},
                on_model_ready=lambda: None,
                timeout_seconds=0.05,
            )
        finally:
            worker.close()

        self.assertEqual(summary["candidate_count"], 1)

    def test_analysis_deadline_terminates_only_the_exact_worker(self) -> None:
        class Process:
            exitcode = None

            def __init__(self) -> None:
                self.alive = True
                self.terminated = False

            def is_alive(self) -> bool:
                return self.alive

            def join(self, timeout: float) -> None:
                del timeout

            def terminate(self) -> None:
                self.terminated = True
                self.alive = False

        class Connection:
            def __init__(self) -> None:
                self.closed = False
                self.messages: list[object] = []

            def send(self, message: object) -> None:
                self.messages.append(message)

            def poll(self, timeout: float) -> bool:
                del timeout
                return False

            def close(self) -> None:
                self.closed = True

        process = Process()
        connection = Connection()
        worker = ResidentAnalysisWorker()
        worker._process = process
        worker._connection = connection

        with self.assertRaisesRegex(TimeoutError, "0.01 秒"):
            worker.analyze(
                {"input_dir": "x", "output_dir": "y"},
                on_model_ready=lambda: None,
                timeout_seconds=0.01,
            )

        self.assertTrue(process.terminated)
        self.assertTrue(connection.closed)
        self.assertIsNone(worker._process)
        self.assertIsNone(worker._connection)

    def test_same_model_and_device_are_loaded_once(self) -> None:
        calls: list[tuple[str, str, str]] = []
        selection = OpenVinoDeviceSelection(
            requested="CPU",
            actual="CPU",
            available_devices=("CPU",),
            full_device_names={"CPU": "Example CPU"},
            policy="explicit_available_device",
        )

        def make_vlm(
            model_path: str,
            *,
            device: str,
            model_id: str,
        ) -> object:
            calls.append((model_path, device, model_id))
            return object()

        cache = ResidentModelCache(
            vlm_factory=make_vlm,
            device_resolver=lambda _requested: selection,
        )
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "model"
            model.mkdir()
            first = cache.prepare(
                openvino_model=None,
                openvino_vlm_model=str(model),
                requested_device="CPU",
            )
            second = cache.prepare(
                openvino_model=None,
                openvino_vlm_model=str(model),
                requested_device="CPU",
            )

        self.assertEqual(len(calls), 1)
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertIs(first["vlm"], second["vlm"])
        self.assertEqual(second["load_seconds"], 0.0)

    def test_reused_model_reports_current_request_selection(self) -> None:
        calls: list[tuple[str, str, str]] = []

        def resolve_device(requested: str) -> OpenVinoDeviceSelection:
            return OpenVinoDeviceSelection(
                requested=requested,
                actual="CPU",
                available_devices=("CPU",),
                full_device_names={"CPU": "Example CPU"},
                policy=(
                    "auto_cpu_fallback"
                    if requested == "AUTO"
                    else "explicit_available_device"
                ),
            )

        def make_vlm(
            model_path: str,
            *,
            device: str,
            model_id: str,
        ) -> object:
            calls.append((model_path, device, model_id))
            return object()

        cache = ResidentModelCache(
            vlm_factory=make_vlm,
            device_resolver=resolve_device,
        )
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "model"
            model.mkdir()
            first = cache.prepare(
                openvino_model=None,
                openvino_vlm_model=str(model),
                requested_device="CPU",
            )
            second = cache.prepare(
                openvino_model=None,
                openvino_vlm_model=str(model),
                requested_device="AUTO",
            )

        self.assertEqual(len(calls), 1)
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(second["selection"].requested, "AUTO")
        self.assertEqual(second["selection"].actual, "CPU")
        self.assertEqual(second["selection"].policy, "auto_cpu_fallback")

    def test_abandoned_pipe_does_not_kill_accept_loop(self) -> None:
        stop_event = threading.Event()
        accepted = object()

        class Listener:
            calls = 0

            def accept(self) -> object:
                self.calls += 1
                if self.calls == 1:
                    raise EOFError("abandoned authentication")
                stop_event.set()
                return accepted

        connections: queue.Queue[object] = queue.Queue()
        _accept_connections(Listener(), connections, stop_event)

        self.assertIs(connections.get_nowait(), accepted)

    def test_status_uses_verified_snapshot_when_pipe_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / "server-state.json").write_text(
                '{"status":"running","schema_version":1}',
                encoding="utf-8",
            )
            with (
                patch.object(client, "read_pid_record", return_value={"pid": 123}),
                patch.object(client, "is_server_record_current", return_value=True),
            ):
                response, exit_code = client.status_when_disconnected(
                    runtime_dir=runtime
                )

        self.assertEqual(exit_code, 0)
        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], "running")
        self.assertFalse(response["result"]["pipe_reachable"])
        self.assertEqual(
            response["result"]["transport"],
            "verified_state_file_fallback",
        )


if __name__ == "__main__":
    unittest.main()
