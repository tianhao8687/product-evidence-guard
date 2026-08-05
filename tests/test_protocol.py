from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import client
import model_download
import protocol
import server


class ServerSideConnection:
    def __init__(self, request: dict[str, object]) -> None:
        self._request = json.dumps(request, ensure_ascii=False).encode("utf-8")
        self.received_count = 0
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def recv_bytes(self, _maximum: int) -> bytes:
        self.received_count += 1
        return self._request

    def send_bytes(self, raw: bytes) -> None:
        self.sent.append(json.loads(raw.decode("utf-8")))

    def close(self) -> None:
        self.closed = True


class AutoReplyConnection:
    """A fake transport that derives a valid response from the sent request."""

    def __init__(self) -> None:
        self.response: bytes | None = None
        self.closed = False
        self.sent_count = 0

    def send_bytes(self, raw: bytes) -> None:
        self.sent_count += 1
        request = json.loads(raw.decode("utf-8"))
        response = protocol.success_response(
            request,
            status="running",
            result={"echo_operation": request["operation"]},
        )
        self.response = json.dumps(response, ensure_ascii=False).encode("utf-8")

    def poll(self, _timeout: float) -> bool:
        return self.response is not None

    def recv_bytes(self, _maximum: int) -> bytes:
        assert self.response is not None
        return self.response

    def close(self) -> None:
        self.closed = True


class BlockingListener:
    """No real socket/pipe: close() unblocks accept() for idle-timeout tests."""

    def __init__(self) -> None:
        self.closed = threading.Event()

    def accept(self) -> object:
        self.closed.wait()
        raise OSError("listener closed")

    def close(self) -> None:
        self.closed.set()


@dataclass
class FakeDecision:
    action: str
    candidate_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "status": "confirmed" if self.action == "confirm" else "rejected",
            "candidate_id": self.candidate_id,
        }


class ProtocolContractTests(unittest.TestCase):
    def test_named_pipe_and_fixed_derived_authkey(self) -> None:
        self.assertEqual(
            protocol.PIPE_ADDRESS,
            r"\\.\pipe\local-product-evidence-guard",
        )
        self.assertEqual(len(protocol.AUTHKEY), 32)
        self.assertEqual(
            protocol.AUTHKEY,
            __import__("hashlib")
            .sha256(b"local-product-evidence-guard:named-pipe:v1")
            .digest(),
        )

    def test_requests_are_json_validated_and_unknown_operations_are_rejected(self) -> None:
        request = protocol.build_request(
            "analyze",
            {"input_dir": "含 空格\\商品资料"},
            request_id="req-1",
        )
        self.assertEqual(request["payload"]["input_dir"], "含 空格\\商品资料")
        with self.assertRaises(protocol.ProtocolError):
            protocol.build_request("delete_everything")
        invalid = dict(request)
        invalid["protocol_version"] = 999
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_request(invalid)

    def test_transport_uses_json_bytes_and_enforces_size_limit(self) -> None:
        connection = ServerSideConnection(
            protocol.build_request("status", request_id="status-1")
        )
        received = protocol.receive_message(connection)
        self.assertEqual(received["operation"], "status")
        with self.assertRaises(protocol.ProtocolError):
            protocol.send_message(
                connection,
                {"text": "x" * (protocol.MAX_MESSAGE_BYTES + 1)},
            )

    def test_response_must_match_request_id(self) -> None:
        request = protocol.build_request("status", request_id="expected")
        response = protocol.success_response(
            request,
            status="running",
            result={},
        )
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_response(response, "different")


class ServerApplicationTests(unittest.TestCase):
    def make_application(
        self,
        runtime_dir: Path,
        *,
        analyze=None,
        decide=None,
        export=None,
    ) -> tuple[server.ServerApplication, server.ServerState, threading.Event]:
        state = server.ServerState(
            runtime_dir=runtime_dir,
            startup_id="test-start",
            idle_timeout=300,
            pid=os.getpid(),
            process_start_marker=protocol.process_start_marker(os.getpid())
            or "test-marker",
        )
        stop_event = threading.Event()
        kwargs = {}
        if analyze is not None:
            kwargs["analyze"] = analyze
        if decide is not None:
            kwargs["decide"] = decide
        if export is not None:
            kwargs["export"] = export
        return server.ServerApplication(state, stop_event, **kwargs), state, stop_event

    def test_one_connection_handles_exactly_one_request_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, _, _ = self.make_application(Path(temporary))
            connection = ServerSideConnection(
                protocol.build_request("status", request_id="one")
            )
            server.handle_connection(connection, app)
            self.assertEqual(connection.received_count, 1)
            self.assertEqual(len(connection.sent), 1)
            self.assertTrue(connection.closed)
            self.assertEqual(connection.sent[0]["status"], "starting")

    def test_status_and_shutdown_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app, state, stop_event = self.make_application(Path(temporary))
            status = app.dispatch(protocol.build_request("status"))
            self.assertTrue(status["ok"])
            self.assertEqual(status["status"], "starting")
            shutdown = app.dispatch(protocol.build_request("shutdown"))
            self.assertEqual(shutdown["status"], "shutdown")
            self.assertTrue(stop_event.is_set())
            self.assertEqual(state.status, "shutdown")

    def test_analyze_calls_existing_engine_and_transitions_loading_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model"
            model.mkdir()
            captured: dict[str, object] = {}

            def fake_analyze(input_dir, output_dir, **kwargs):
                captured.update(
                    {
                        "input_dir": input_dir,
                        "output_dir": output_dir,
                        **kwargs,
                    }
                )
                return {"session_id": "s1", "candidate_count": 2}

            app, state, _ = self.make_application(root, analyze=fake_analyze)
            response = app.dispatch(
                protocol.build_request(
                    "analyze",
                    {
                        "input_dir": str(root / "商品 资料"),
                        "output_dir": str(root / "输出 目录"),
                        "openvino_vlm_model": str(model),
                        "device": "GPU",
                    },
                )
            )
            self.assertTrue(response["ok"])
            self.assertEqual(response["result"]["summary"]["candidate_count"], 2)
            self.assertEqual(captured["device"], "GPU")
            self.assertEqual(captured["openvino_vlm_model"], str(model))
            self.assertIn("loading", state.transition_history)
            self.assertEqual(state.status, "running")

    def test_missing_model_with_pending_download_returns_exit_code_three(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protocol.atomic_write_json(
                root / "pending-request.json",
                {"status": "downloading"},
            )
            app, state, _ = self.make_application(
                root,
                analyze=lambda *_args, **_kwargs: self.fail(
                    "engine must not run while the model is incomplete"
                ),
            )
            response = app.dispatch(
                protocol.build_request(
                    "analyze",
                    {
                        "input_dir": str(root / "input"),
                        "openvino_vlm_model": str(root / "missing-model"),
                    },
                )
            )
            self.assertFalse(response["ok"])
            self.assertEqual(response["status"], "downloading")
            self.assertEqual(response["exit_code"], protocol.EXIT_DOWNLOAD_PENDING)
            self.assertEqual(state.status, "downloading")

    def test_confirm_reject_and_export_call_confirmation_api(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_decide(output_dir, **kwargs):
            calls.append((output_dir, kwargs))
            return FakeDecision(kwargs["action"], kwargs["candidate_id"])

        def fake_export(output_dir, **kwargs):
            calls.append((output_dir, kwargs))
            return {"confirmed_count": 1}

        with tempfile.TemporaryDirectory() as temporary:
            app, _, _ = self.make_application(
                Path(temporary),
                decide=fake_decide,
                export=fake_export,
            )
            for operation in ("confirm", "reject"):
                response = app.dispatch(
                    protocol.build_request(
                        operation,
                        {
                            "output_dir": "out",
                            "session_id": "session",
                            "candidate_id": "candidate",
                            "reason": "人工核验",
                        },
                    )
                )
                self.assertTrue(response["ok"])
            exported = app.dispatch(
                protocol.build_request(
                    "export",
                    {"output_dir": "out", "session_id": "session"},
                )
            )
            self.assertEqual(exported["result"]["export"]["confirmed_count"], 1)
            self.assertEqual([item[1].get("action") for item in calls[:2]], [
                "confirm",
                "reject",
            ])

            def stale_decide(*_args, **_kwargs):
                raise server.ConfirmationRequestError("候选已经失效")

            stale_app, stale_state, _ = self.make_application(
                Path(temporary) / "stale-request",
                decide=stale_decide,
            )
            stale_response = stale_app.dispatch(
                protocol.build_request(
                    "confirm",
                    {
                        "output_dir": "out",
                        "session_id": "session",
                        "candidate_id": "stale-candidate",
                        "reason": "不应重新确认",
                    },
                )
            )
            self.assertFalse(stale_response["ok"])
            self.assertEqual(stale_response["status"], "running")
            self.assertEqual(stale_response["error"]["code"], "operation_failed")
            self.assertEqual(stale_state.status, "running")
            status = stale_app.dispatch(protocol.build_request("status"))
            self.assertEqual(status["status"], "running")

            def integrity_failure(*_args, **_kwargs):
                raise server.ConfirmationError("审计目标不安全")

            integrity_app, integrity_state, _ = self.make_application(
                Path(temporary) / "integrity-failure",
                decide=integrity_failure,
            )
            integrity_response = integrity_app.dispatch(
                protocol.build_request(
                    "confirm",
                    {
                        "output_dir": "out",
                        "session_id": "session",
                        "candidate_id": "candidate",
                        "reason": "人工确认",
                    },
                )
            )
            self.assertFalse(integrity_response["ok"])
            self.assertEqual(integrity_response["status"], "error")
            self.assertEqual(
                integrity_response["error"]["code"],
                "operation_failed",
            )
            self.assertEqual(integrity_state.status, "error")

    def test_operation_failure_sets_error_state_without_crashing_server(self) -> None:
        def failed_engine(*_args, **_kwargs):
            raise RuntimeError("mock failure")

        with tempfile.TemporaryDirectory() as temporary:
            app, state, _ = self.make_application(
                Path(temporary),
                analyze=failed_engine,
            )
            response = app.dispatch(
                protocol.build_request(
                    "analyze",
                    {
                        "input_dir": "input",
                        "output_dir": "output",
                    },
                )
            )
            self.assertFalse(response["ok"])
            self.assertEqual(response["exit_code"], protocol.EXIT_GENERAL_ERROR)
            self.assertEqual(state.status, "error")
            status = app.dispatch(protocol.build_request("status"))
            self.assertEqual(status["status"], "error")

    def test_server_log_does_not_record_exception_product_text(self) -> None:
        secret_text = "客户未公开商品正文-净重777g"

        def failed_engine(*_args, **_kwargs):
            raise RuntimeError(secret_text)

        with tempfile.TemporaryDirectory() as temporary:
            app, _, _ = self.make_application(
                Path(temporary),
                analyze=failed_engine,
            )
            with self.assertLogs(server.LOGGER, level="ERROR") as captured:
                response = app.dispatch(
                    protocol.build_request(
                        "analyze",
                        {"input_dir": "input", "output_dir": "output"},
                    )
                )

        self.assertFalse(response["ok"])
        self.assertNotIn(secret_text, "\n".join(captured.output))
        self.assertIn("RuntimeError", "\n".join(captured.output))

    def test_status_remains_available_and_shutdown_waits_for_active_analysis(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_engine(*_args, **_kwargs):
            started.set()
            self.assertTrue(release.wait(2.0))
            return {"candidate_count": 0}

        with tempfile.TemporaryDirectory() as temporary:
            app, state, stop_event = self.make_application(
                Path(temporary),
                analyze=slow_engine,
            )
            analysis_thread = threading.Thread(
                target=lambda: app.dispatch(
                    protocol.build_request(
                        "analyze",
                        {"input_dir": "input", "output_dir": "output"},
                    )
                )
            )
            analysis_thread.start()
            self.assertTrue(started.wait(1.0))
            status = app.dispatch(protocol.build_request("status"))
            self.assertEqual(status["status"], "running")

            shutdown_thread = threading.Thread(
                target=lambda: app.dispatch(protocol.build_request("shutdown"))
            )
            shutdown_thread.start()
            self.assertFalse(stop_event.wait(0.05))
            release.set()
            analysis_thread.join(1.0)
            shutdown_thread.join(1.0)
            self.assertFalse(analysis_thread.is_alive())
            self.assertFalse(shutdown_thread.is_alive())
            self.assertTrue(stop_event.is_set())
            self.assertEqual(state.status, "shutdown")


class ClientAndLifecycleTests(unittest.TestCase):
    def test_client_exchange_uses_injected_transport(self) -> None:
        connections: list[AutoReplyConnection] = []

        def connector(_address: str, _authkey: bytes) -> AutoReplyConnection:
            connection = AutoReplyConnection()
            connections.append(connection)
            return connection

        request = protocol.build_request("status", request_id="client-status")
        response = client.exchange(request, connector=connector, timeout=0.1)
        self.assertEqual(response["result"]["echo_operation"], "status")
        self.assertEqual(connections[0].sent_count, 1)
        self.assertTrue(connections[0].closed)

    def test_disconnected_status_distinguishes_stopped_and_downloading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary)
            stopped, stopped_code = client.status_when_disconnected(
                runtime_dir=runtime_dir
            )
            self.assertEqual(stopped["status"], "stopped")
            self.assertEqual(stopped_code, protocol.EXIT_SUCCESS)
            protocol.atomic_write_json(
                runtime_dir / "server-state.json",
                {"status": "downloading"},
            )
            downloading, downloading_code = client.status_when_disconnected(
                runtime_dir=runtime_dir
            )
            self.assertEqual(downloading["status"], "downloading")
            self.assertEqual(
                downloading_code,
                protocol.EXIT_DOWNLOAD_PENDING,
            )

    def test_runtime_guard_rejects_duplicate_exact_server_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary)
            first = protocol.RuntimeIdentityGuard(
                runtime_dir=runtime_dir,
                startup_id="first",
            )
            first.acquire()
            try:
                duplicate = protocol.RuntimeIdentityGuard(
                    runtime_dir=runtime_dir,
                    startup_id="second",
                )
                with self.assertRaises(protocol.AlreadyRunningError):
                    duplicate.acquire()
                record = protocol.read_pid_record(runtime_dir)
                self.assertTrue(protocol.is_server_record_current(record))
                changed = dict(record or {})
                changed["process_start_marker"] = "wrong-start-marker"
                self.assertFalse(protocol.is_server_record_current(changed))
            finally:
                first.release()
            self.assertFalse((runtime_dir / "server.pid").exists())
            self.assertFalse((runtime_dir / "server.lock").exists())

    def test_fresh_incomplete_start_lock_is_not_deleted_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary)
            runtime_dir.mkdir(exist_ok=True)
            lock_path = runtime_dir / "server.lock"
            lock_path.write_text("{}", encoding="utf-8")
            self.assertFalse(
                protocol.remove_stale_runtime_identity(runtime_dir)
            )
            self.assertTrue(lock_path.exists())
            old = time.time() - protocol.STARTUP_LOCK_GRACE_SECONDS - 1
            os.utime(lock_path, (old, old))
            self.assertTrue(
                protocol.remove_stale_runtime_identity(runtime_dir)
            )
            self.assertFalse(lock_path.exists())

    def test_idle_timeout_uses_injected_listener_without_named_pipe(self) -> None:
        listener = BlockingListener()
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary)
            exit_code = server.serve_forever(
                address="mock-address",
                runtime_dir=runtime_dir,
                idle_timeout=0.05,
                startup_id="idle-test",
                listener_factory=lambda _address, _authkey: listener,
            )
            self.assertEqual(exit_code, protocol.EXIT_SUCCESS)
            state = protocol.read_json_object(runtime_dir / "server-state.json")
            self.assertEqual(state["status"], "shutdown")
            self.assertEqual(state["shutdown_reason"], "idle_timeout")
            self.assertFalse((runtime_dir / "server.pid").exists())

    def test_model_prepare_and_continue_are_mockable_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary) / "runtime"
            model_dir = Path(temporary) / "model"

            class Spec:
                target = model_dir
                required_files = ("config.json",)

            def downloader(_spec):
                model_dir.mkdir()
                (model_dir / "config.json").write_text("{}", encoding="utf-8")
                return {"status": "ready", "model_path": str(model_dir)}

            prepared = client.prepare_local_model(
                {
                    "input_dir": "资料",
                    "openvino_vlm_model": None,
                    "deterministic_only": False,
                },
                runtime_dir=runtime_dir,
                downloader=downloader,
                spec_loader=lambda: Spec(),
            )
            self.assertEqual(prepared["openvino_vlm_model"], str(model_dir))

    def test_continue_download_resumes_saved_analysis_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_dir = root / "runtime"
            model_dir = root / "model"

            class Spec:
                target = model_dir
                required_files = ("config.json",)

            client._save_pending_analysis(
                {"input_dir": "资料目录", "openvino_vlm_model": None},
                runtime_dir=runtime_dir,
            )
            pending, response, exit_code = client.continue_download(
                runtime_dir=runtime_dir,
                downloader=lambda _spec: {
                    "status": "ready",
                    "model_path": str(model_dir),
                },
                spec_loader=lambda: Spec(),
            )
            self.assertEqual(exit_code, protocol.EXIT_SUCCESS)
            self.assertTrue(response["result"]["pending_analysis_resumed"])
            self.assertEqual(pending["openvino_vlm_model"], str(model_dir))
            self.assertTrue(
                (runtime_dir / client.PENDING_ANALYSIS_NAME).exists()
            )

    def test_pending_analysis_is_cleared_only_after_successful_resume(self) -> None:
        for analysis_exit, should_exist in ((1, True), (0, False)):
            with self.subTest(analysis_exit=analysis_exit):
                with tempfile.TemporaryDirectory() as temporary:
                    runtime_dir = Path(temporary) / "runtime"
                    client._save_pending_analysis(
                        {"input_dir": "匿名 资料", "openvino_vlm_model": None},
                        runtime_dir=runtime_dir,
                    )
                    pending = {
                        "input_dir": "匿名 资料",
                        "openvino_vlm_model": "model",
                    }
                    with mock.patch.object(
                        client,
                        "continue_download",
                        return_value=(pending, {"ok": True}, 0),
                    ), mock.patch.object(
                        client,
                        "execute_command",
                        return_value=({"ok": analysis_exit == 0}, analysis_exit),
                    ):
                        exit_code = client.main(
                            ["--continue"],
                            runtime_dir=runtime_dir,
                        )
                    self.assertEqual(exit_code, analysis_exit)
                    self.assertEqual(
                        (runtime_dir / client.PENDING_ANALYSIS_NAME).exists(),
                        should_exist,
                    )

    def test_different_pending_analysis_cannot_be_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary) / "runtime"
            client._save_pending_analysis(
                {"input_dir": "first"}, runtime_dir=runtime_dir
            )
            with self.assertRaises(client.CliUsageError):
                client._save_pending_analysis(
                    {"input_dir": "second"}, runtime_dir=runtime_dir
                )
            pending = client._load_pending_analysis(runtime_dir)
            self.assertEqual(pending["input_dir"], "first")

    def test_download_pending_is_saved_for_continue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            class Spec:
                target = root / "missing-model"
                required_files = ("config.json",)

            with self.assertRaises(client.DownloadPending):
                client.prepare_local_model(
                    {
                        "input_dir": "含 空格\\资料",
                        "openvino_vlm_model": None,
                        "deterministic_only": False,
                    },
                    runtime_dir=root / "runtime",
                    downloader=lambda _spec: {"status": "downloading"},
                    spec_loader=lambda: Spec(),
                )
            pending = protocol.read_json_object(
                root / "runtime" / client.PENDING_ANALYSIS_NAME
            )
            self.assertEqual(
                pending["payload"]["input_dir"],
                "含 空格\\资料",
            )

    def test_download_coordinator_returns_pending_at_host_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_dir = root / "runtime"
            spec = model_download.DownloadSpec(
                model_id="owner/model",
                target=root / "model",
                required_files=("config.json",),
                revision="abc",
                runtime_dir=runtime_dir,
            )
            starts: list[Path] = []

            def starter(_spec, *, runtime_dir):
                starts.append(runtime_dir)
                return 1234

            result = client.coordinate_model_download(
                spec,
                runtime_dir=runtime_dir,
                wait_timeout=0,
                starter=starter,
            )
            self.assertEqual(result["status"], "downloading")
            self.assertEqual(result["wait_timeout_seconds"], 0)
            self.assertEqual(result["worker_pid"], 1234)
            self.assertEqual(starts, [runtime_dir])

    def test_download_coordinator_observes_worker_atomic_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_dir = root / "runtime"
            target = root / "model"
            spec = model_download.DownloadSpec(
                model_id="owner/model",
                target=target,
                required_files=("config.json",),
                revision="abc",
                runtime_dir=runtime_dir,
            )

            def starter(_spec, *, runtime_dir):
                self.assertEqual(runtime_dir, spec.runtime_dir)
                target.mkdir()
                (target / "config.json").write_text("{}", encoding="utf-8")
                return 5678

            result = client.coordinate_model_download(
                spec,
                runtime_dir=runtime_dir,
                wait_timeout=1,
                starter=starter,
            )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["worker_pid"], 5678)

    def test_download_coordinator_surfaces_permanent_worker_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_dir = root / "runtime"
            spec = model_download.DownloadSpec(
                model_id="owner/model",
                target=root / "model",
                required_files=("config.json",),
                revision="abc",
                runtime_dir=runtime_dir,
            )

            def starter(_spec, *, runtime_dir):
                protocol.atomic_write_json(
                    runtime_dir / "download-state.json",
                    {
                        "schema_version": 1,
                        "status": "error",
                        "last_error": "RevisionNotFoundError: bad revision",
                    },
                )
                return 9012

            result = client.coordinate_model_download(
                spec,
                runtime_dir=runtime_dir,
                wait_timeout=1,
                starter=starter,
            )
            self.assertEqual(result["status"], "error")
            self.assertFalse(result["retryable"])
            self.assertIn("RevisionNotFoundError", result["error"])

    def test_powershell_entry_is_utf8_and_forwards_all_arguments(self) -> None:
        # UTF-8 with BOM keeps the public entry parseable in Windows
        # PowerShell 5.1 on non-UTF-8 runner locales.
        content = (SCRIPTS_DIR / "run.ps1").read_text(encoding="utf-8-sig")
        self.assertTrue(content.startswith("$ErrorActionPreference = 'Stop'"))
        self.assertIn("[Console]::OutputEncoding", content)
        self.assertIn("$ClientScript @args", content)
        self.assertIn(".runtime\\source-root.txt", content)
        self.assertIn("-File $ExternalRunScript @args", content)
        self.assertIn("runtime_root_invalid", content)
        self.assertNotIn("Write-Host", content)

    @unittest.skipUnless(os.name == "nt", "real AF_PIPE is Windows-only")
    def test_real_windows_named_pipe_status_and_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_dir = Path(temporary) / "runtime"
            address = (
                r"\\.\pipe\local-product-evidence-guard-test-"
                + uuid.uuid4().hex
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPTS_DIR / "server.py"),
                    "--runtime-dir",
                    str(runtime_dir),
                    "--pipe-address",
                    address,
                    "--idle-timeout",
                    "30",
                ],
                cwd=str(REPO_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                deadline = time.monotonic() + 10
                connection = None
                while time.monotonic() < deadline:
                    try:
                        connection = protocol.connect_pipe(
                            address,
                            protocol.AUTHKEY,
                        )
                        break
                    except protocol.CommunicationError:
                        if process.poll() is not None:
                            break
                        time.sleep(0.1)
                if connection is None:
                    log_path = runtime_dir / "server.log"
                    log = (
                        log_path.read_text(encoding="utf-8", errors="replace")
                        if log_path.exists()
                        else ""
                    )
                    self.fail(
                        f"Named Pipe server did not start; "
                        f"exit={process.poll()} log={log}"
                    )
                status_request = protocol.build_request("status")
                protocol.send_message(connection, status_request)
                status_response = protocol.receive_message(connection)
                connection.close()
                self.assertEqual(status_response["status"], "running")

                shutdown_connection = protocol.connect_pipe(
                    address,
                    protocol.AUTHKEY,
                )
                shutdown_request = protocol.build_request("shutdown")
                protocol.send_message(shutdown_connection, shutdown_request)
                shutdown_response = protocol.receive_message(shutdown_connection)
                shutdown_connection.close()
                self.assertEqual(shutdown_response["status"], "shutdown")
                self.assertEqual(process.wait(timeout=10), protocol.EXIT_SUCCESS)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
