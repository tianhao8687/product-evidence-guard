from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import protocol
import server


EVIDENCE = (
    REPO_ROOT
    / "docs"
    / "evidence"
    / "named-pipe-security-boundaries-20260805.json"
)
WRONG_AUTH_CHILD = r"""
from multiprocessing.connection import Client
import sys

try:
    connection = Client(
        address=sys.argv[1],
        family="AF_PIPE",
        authkey=b"deliberately-wrong-authkey",
    )
except Exception as exc:
    print(type(exc).__name__)
    raise SystemExit(0)
else:
    connection.close()
    print("unexpected-auth-success")
    raise SystemExit(9)
"""
OVERSIZED_CHILD = r"""
from multiprocessing.connection import Client
import hashlib
import sys

authkey = hashlib.sha256(
    b"local-product-evidence-guard:named-pipe:v1"
).digest()
connection = Client(address=sys.argv[1], family="AF_PIPE", authkey=authkey)
try:
    try:
        connection.send_bytes(b"x" * int(sys.argv[2]))
    except (BrokenPipeError, OSError) as exc:
        print("oversized-frame-rejected:" + type(exc).__name__)
    else:
        print("oversized-frame-sent")
finally:
    connection.close()
"""


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _status(address: str) -> dict[str, object]:
    connection = protocol.connect_pipe(address, protocol.AUTHKEY)
    try:
        request = protocol.build_request("status")
        protocol.send_message(connection, request)
        poll = getattr(connection, "poll", None)
        if callable(poll) and not poll(5.0):
            raise TimeoutError("isolated status response timed out")
        response = protocol.receive_message(connection)
        return protocol.validate_response(response, str(request["request_id"]))
    finally:
        connection.close()


def _wait_for_status(address: str, process: subprocess.Popen[bytes]) -> dict[str, object]:
    deadline = time.monotonic() + 10.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _status(address)
        except Exception as exc:  # test readiness retry, error is reported on failure
            last_error = exc
            if process.poll() is not None:
                break
            time.sleep(0.05)
    raise AssertionError(
        f"isolated Named Pipe server did not become ready: "
        f"exit={process.poll()} error={type(last_error).__name__ if last_error else 'none'}"
    )


@contextmanager
def _isolated_server():
    with tempfile.TemporaryDirectory() as temporary:
        runtime_dir = Path(temporary) / "runtime"
        address = (
            r"\\.\pipe\local-product-evidence-guard-security-test-"
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
                "60",
            ],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
        try:
            first = _wait_for_status(address, process)
            if first["status"] != "running":
                raise AssertionError(f"unexpected initial status: {first['status']}")
            yield address, runtime_dir, process
        finally:
            if process.poll() is None:
                try:
                    connection = protocol.connect_pipe(address, protocol.AUTHKEY)
                    request = protocol.build_request("shutdown")
                    protocol.send_message(connection, request)
                    if connection.poll(3.0):
                        protocol.receive_message(connection)
                    connection.close()
                    process.wait(timeout=5.0)
                except Exception:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5.0)


class NamedPipeSecurityEvidenceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.serialized = EVIDENCE.read_text(encoding="utf-8")
        self.record = json.loads(self.serialized)

    def test_evidence_schema_limits_and_claim_boundary(self) -> None:
        self.assertEqual(self.record["schema_version"], 1)
        self.assertEqual(
            self.record["evidence_type"], "named_pipe_security_boundaries"
        )
        self.assertEqual(
            self.record["implementation_limits"],
            {
                "maximum_message_bytes": protocol.MAX_MESSAGE_BYTES,
                "maximum_pending_connections": server.MAX_PENDING_CONNECTIONS,
                "maximum_request_workers": server.MAX_REQUEST_WORKERS,
            },
        )
        environment = self.record["environment"]
        self.assertFalse(environment["model_loaded"])
        self.assertFalse(environment["network_used"])
        self.assertEqual(environment["product_files_read"], 0)
        self.assertEqual(
            self.record["results"]["bounded_concurrent_status_burst"][
                "connection_count"
            ],
            24,
        )
        forgery = self.record["results"][
            "same_user_live_identity_record_forgery"
        ]
        self.assertTrue(forgery["process_alive_after_identity_check"])
        self.assertTrue(forgery["process_terminated_during_test_cleanup"])
        coverage = " ".join(self.record["coverage"].values()).lower()
        for phrase in (
            "partial boundary evidence",
            "not a penetration test",
            "no xml or zip bomb",
            "not a security boundary",
        ):
            self.assertIn(phrase, coverage)

    def test_evidence_has_no_host_path_credentials_or_product_content(self) -> None:
        self.assertIsNone(re.search(r"(?i)[a-z]:[\\/]", self.serialized))
        self.assertIsNone(re.search(r"(?i)/(?:home|users)/", self.serialized))
        for marker in (
            "api_key",
            "access_token",
            "bearer ",
            "authorization",
            "private key",
            "session_id",
            "request_id",
        ):
            self.assertNotIn(marker, self.serialized.lower())


@unittest.skipUnless(os.name == "nt", "real AF_PIPE security boundaries are Windows-only")
class RealWindowsNamedPipeSecurityBoundaryTests(unittest.TestCase):
    def test_wrong_auth_from_independent_process_is_rejected_and_server_survives(self) -> None:
        with _isolated_server() as (address, _runtime, process):
            attack = subprocess.run(
                [sys.executable, "-B", "-c", WRONG_AUTH_CHILD, address],
                cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10.0,
                check=False,
                creationflags=_creation_flags(),
            )
            self.assertEqual(attack.returncode, 0, attack.stdout + attack.stderr)
            self.assertIn("AuthenticationError", attack.stdout)
            self.assertNotIn("unexpected-auth-success", attack.stdout)
            response = _wait_for_status(address, process)
            self.assertEqual(response["status"], "running")

    def test_one_oversized_frame_is_closed_and_server_survives(self) -> None:
        with _isolated_server() as (address, _runtime, process):
            size = protocol.MAX_MESSAGE_BYTES + 1
            attack = subprocess.run(
                [sys.executable, "-B", "-c", OVERSIZED_CHILD, address, str(size)],
                cwd=REPO_ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10.0,
                check=False,
                creationflags=_creation_flags(),
            )
            self.assertEqual(attack.returncode, 0, attack.stdout + attack.stderr)
            self.assertIn("oversized-frame-rejected:BrokenPipeError", attack.stdout)
            response = _wait_for_status(address, process)
            self.assertEqual(response["status"], "running")

    def test_bounded_24_connection_status_burst_completes(self) -> None:
        with _isolated_server() as (address, _runtime, _process):
            with ThreadPoolExecutor(max_workers=24) as executor:
                futures = [executor.submit(_status, address) for _ in range(24)]
                responses = [future.result(timeout=15.0) for future in futures]
            self.assertEqual(len(responses), 24)
            self.assertTrue(all(item["ok"] for item in responses))
            self.assertTrue(all(item["operation"] == "status" for item in responses))
            self.assertTrue(all(item["status"] == "running" for item in responses))

    def test_same_user_forged_live_identity_record_is_known_limitation(self) -> None:
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
        try:
            marker = protocol.process_start_marker(child.pid)
            self.assertIsNotNone(marker)
            forged = {
                "schema_version": 1,
                "app_id": protocol.APP_ID,
                "pid": child.pid,
                "process_start_marker": marker,
                "startup_id": "forged-safe-test",
                "server_script": str(protocol.SERVER_SCRIPT.resolve()),
                "executable": str(Path(sys.executable).resolve()),
                "created_at": protocol.utc_now(),
            }
            observed = protocol.is_server_record_current(forged)
            documented = self._documented_identity_forgery_result()
            self.assertEqual(observed, documented)
            self.assertIsNone(child.poll())

            wrong_marker = dict(forged, process_start_marker="wrong-marker")
            wrong_script = dict(forged, server_script=str(REPO_ROOT / "not-server.py"))
            self.assertFalse(protocol.is_server_record_current(wrong_marker))
            self.assertFalse(protocol.is_server_record_current(wrong_script))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5.0)

    @staticmethod
    def _documented_identity_forgery_result() -> bool:
        record = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        return bool(
            record["results"]["same_user_live_identity_record_forgery"][
                "forged_record_accepted_as_current"
            ]
        )


if __name__ == "__main__":
    unittest.main()
