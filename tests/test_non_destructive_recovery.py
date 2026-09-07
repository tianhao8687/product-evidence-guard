from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import client
import protocol


class NonDestructiveRecoveryTests(unittest.TestCase):
    def test_unresponsive_live_server_is_not_killed_replaced_or_unregistered(self):
        with tempfile.TemporaryDirectory() as temporary:
            starter = Mock()
            record = {"pid": 12345, "process_start_marker": "test-identity"}
            with patch.object(client, "_probe", side_effect=protocol.CommunicationError("busy")), \
                 patch.object(client, "read_pid_record", return_value=record), \
                 patch.object(client, "is_server_record_current", return_value=True), \
                 patch.object(client, "remove_stale_runtime_identity") as cleanup, \
                 patch.object(protocol, "terminate_exact_server") as terminate:
                with self.assertRaisesRegex(protocol.CommunicationError, "未自动终止或重启"):
                    client.ensure_server(runtime_dir=Path(temporary), starter=starter, start_timeout=0)
                terminate.assert_not_called()
                cleanup.assert_not_called()
                starter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
