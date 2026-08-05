from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import client
import model_download
import server


SENSITIVE_TEXT = (
    "客户未公开商品正文-净重777g "
    "token=private-test-token "
    "C:" + r"\Users\PrivateCustomer\商品资料\说明书.pdf"
)


def _close_server_handlers() -> None:
    for handler in list(server.LOGGER.handlers):
        handler.flush()
        server.LOGGER.removeHandler(handler)
        handler.close()


class InstallLogPrivacyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (SCRIPTS_DIR / "install-env.ps1").read_text(
            encoding="utf-8"
        )

    def test_installer_does_not_log_paths_arguments_or_native_output(self) -> None:
        self.assertNotIn("Repository=$repoRoot", self.script)
        self.assertNotIn("$($Arguments -join ' ')", self.script)
        self.assertNotIn('Write-InstallLog "CHECK $line"', self.script)
        self.assertNotIn("primary artifact from $url", self.script)
        self.assertNotIn("exact target $venvDir", self.script)
        self.assertIn("argument_count=$($Arguments.Count)", self.script)
        self.assertIn("exit_code=$commandExitCode", self.script)

    def test_installer_resets_legacy_log_and_redacts_sensitive_shapes(self) -> None:
        self.assertIn(
            'Write-InstallLog "Install started. Python=$pythonVersion" -Reset',
            self.script,
        )
        self.assertIn("token|password|secret|authorization|api[_-]?key", self.script)
        self.assertIn("<project-root>", self.script)
        self.assertIn("<local-path>", self.script)
        self.assertIn("<redacted>", self.script)


class ServerLogPrivacyTests(unittest.TestCase):
    def tearDown(self) -> None:
        _close_server_handlers()

    def test_structured_server_event_rejects_product_text_credentials_and_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peg-server-log-") as temporary:
            runtime = Path(temporary)
            try:
                server.configure_logging(runtime)
                server._log_event(
                    server.logging.ERROR,
                    "operation_failed",
                    operation=SENSITIVE_TEXT,
                    error_type=SENSITIVE_TEXT,
                    raw_text="NETWEIGHT8OZ",
                    authkey="ascii-test-secret",
                )
                server._log_event(
                    server.logging.INFO,
                    "operation_completed",
                    operation="analyze",
                    error_type="RuntimeError",
                )
                for handler in server.LOGGER.handlers:
                    handler.flush()
                content = (runtime / "server.log").read_text(
                    encoding="utf-8"
                )
            finally:
                _close_server_handlers()

        self.assertNotIn(SENSITIVE_TEXT, content)
        self.assertNotIn("净重777g", content)
        self.assertNotIn("private-test-token", content)
        self.assertNotIn("PrivateCustomer", content)
        self.assertNotIn("说明书.pdf", content)
        self.assertNotIn("NETWEIGHT8OZ", content)
        self.assertNotIn("ascii-test-secret", content)
        self.assertIn("event=operation_failed", content)
        self.assertIn("operation=<redacted>", content)
        self.assertIn("raw_text=<redacted>", content)
        self.assertIn("error_type=RuntimeError", content)


class ClientChildLogPrivacyTests(unittest.TestCase):
    def test_runtime_log_writer_rejects_hardlink_without_mutating_external_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peg-client-log-link-") as temporary:
            base = Path(temporary)
            runtime = base / "runtime"
            runtime.mkdir()
            external = base / "external.log"
            external.write_text(SENSITIVE_TEXT, encoding="utf-8")
            linked = runtime / "server.log"
            try:
                linked.hardlink_to(external)
            except OSError as error:
                self.skipTest(f"hard links unavailable: {error}")

            with self.assertRaisesRegex(RuntimeError, "unsafe runtime log target"):
                client._write_safe_runtime_log_event(
                    linked,
                    "server_process_spawned",
                    replace=True,
                )

            self.assertEqual(external.read_text(encoding="utf-8"), SENSITIVE_TEXT)

    def test_server_child_output_is_discarded_and_legacy_log_family_is_reset(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peg-client-server-log-") as temporary:
            runtime = Path(temporary)
            log_path = runtime / "server.log"
            log_path.write_text(SENSITIVE_TEXT, encoding="utf-8")
            for index in (1, 2):
                log_path.with_name(f"server.log.{index}").write_text(
                    SENSITIVE_TEXT,
                    encoding="utf-8",
                )
            fake_process = types.SimpleNamespace(pid=12345)

            with patch.object(
                client.subprocess,
                "Popen",
                return_value=fake_process,
            ) as popen:
                pid = client.start_server_process(
                    "privacy-test-startup",
                    runtime_dir=runtime,
                )

            self.assertEqual(pid, 12345)
            self.assertIs(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("event=server_process_spawned", content)
            self.assertNotIn("PrivateCustomer", content)
            self.assertNotIn("private-test-token", content)
            self.assertFalse(log_path.with_name("server.log.1").exists())
            self.assertFalse(log_path.with_name("server.log.2").exists())

    def test_download_child_output_is_discarded_and_legacy_log_is_reset(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peg-client-download-log-") as temporary:
            base = Path(temporary)
            runtime = base / "runtime"
            runtime.mkdir()
            log_path = runtime / client.MODEL_DOWNLOAD_LOG_NAME
            log_path.write_text(SENSITIVE_TEXT, encoding="utf-8")
            spec = model_download.DownloadSpec(
                model_id="public/model-id",
                target=base / "PrivateCustomer" / "model",
                required_files=("config.json",),
                runtime_dir=runtime,
            )
            fake_process = types.SimpleNamespace(pid=54321)

            with patch.object(
                client.subprocess,
                "Popen",
                return_value=fake_process,
            ) as popen:
                pid = client.start_model_download_process(
                    spec,
                    runtime_dir=runtime,
                )

            self.assertEqual(pid, 54321)
            self.assertIs(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("event=model_download_worker_spawned", content)
            self.assertNotIn("PrivateCustomer", content)
            self.assertNotIn("private-test-token", content)
            self.assertNotIn("public/model-id", content)


if __name__ == "__main__":
    unittest.main()
