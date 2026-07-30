from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any, Callable, Mapping
import uuid


APP_ID = "local-product-evidence-guard"
PROTOCOL_VERSION = 1
PIPE_ADDRESS = r"\\.\pipe\local-product-evidence-guard"
AUTHKEY = hashlib.sha256(
    f"{APP_ID}:named-pipe:v{PROTOCOL_VERSION}".encode("utf-8")
).digest()

VALID_OPERATIONS = frozenset(
    {"status", "analyze", "confirm", "reject", "export", "shutdown"}
)
VALID_STATES = frozenset(
    {"starting", "downloading", "loading", "running", "error", "shutdown"}
)

EXIT_SUCCESS = 0
EXIT_GENERAL_ERROR = 1
EXIT_COMMUNICATION_ERROR = 2
EXIT_DOWNLOAD_PENDING = 3

MAX_MESSAGE_BYTES = 1024 * 1024
DEFAULT_IDLE_TIMEOUT = 300.0
STARTUP_LOCK_GRACE_SECONDS = 15.0

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / ".runtime"
PID_PATH = RUNTIME_DIR / "server.pid"
LOCK_PATH = RUNTIME_DIR / "server.lock"
STATE_PATH = RUNTIME_DIR / "server-state.json"
SERVER_LOG_PATH = RUNTIME_DIR / "server.log"
SERVER_SCRIPT = Path(__file__).resolve().with_name("server.py")


class ProtocolError(ValueError):
    """A message does not satisfy the local IPC contract."""


class CommunicationError(RuntimeError):
    """The authenticated local server could not be reached or replied incorrectly."""


class AlreadyRunningError(RuntimeError):
    """An exact, live server identity already owns the runtime lock."""


def configure_utf8_streams() -> None:
    """Keep CLI JSON stable when launched from Windows PowerShell or Qoder."""

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _ensure_message_size(data: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            dict(data),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("消息必须是可序列化的 JSON 对象。") from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolError("消息超过 1 MiB 协议上限。")
    return encoded


def send_message(connection: Any, data: Mapping[str, Any]) -> None:
    """Send JSON bytes instead of pickle over multiprocessing.connection."""

    connection.send_bytes(_ensure_message_size(data))


def receive_message(connection: Any) -> dict[str, Any]:
    try:
        raw = connection.recv_bytes(MAX_MESSAGE_BYTES)
    except OSError as exc:
        raise CommunicationError(f"本地连接读取失败：{exc}") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("收到的消息不是有效 UTF-8 JSON。") from exc
    if not isinstance(decoded, dict):
        raise ProtocolError("协议消息必须是 JSON 对象。")
    return decoded


def build_request(
    operation: str,
    payload: Mapping[str, Any] | None = None,
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id or uuid.uuid4().hex,
        "operation": operation,
        "payload": dict(payload or {}),
    }
    return validate_request(request)


def validate_request(message: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(message, Mapping):
        raise ProtocolError("请求必须是 JSON 对象。")
    if message.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("协议版本不受支持。")
    request_id = message.get("request_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ProtocolError("request_id 无效。")
    operation = message.get("operation")
    if operation not in VALID_OPERATIONS:
        raise ProtocolError(f"不支持的操作：{operation}")
    payload = message.get("payload")
    if not isinstance(payload, Mapping):
        raise ProtocolError("payload 必须是 JSON 对象。")
    normalized = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "operation": str(operation),
        "payload": dict(payload),
    }
    _ensure_message_size(normalized)
    return normalized


def success_response(
    request: Mapping[str, Any],
    *,
    status: str,
    result: Mapping[str, Any] | None = None,
    exit_code: int = EXIT_SUCCESS,
) -> dict[str, Any]:
    if status not in VALID_STATES and status != "stopped":
        raise ProtocolError(f"响应状态无效：{status}")
    response = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": str(request.get("request_id", "")),
        "operation": str(request.get("operation", "")),
        "ok": True,
        "status": status,
        "exit_code": int(exit_code),
        "result": dict(result or {}),
        "error": None,
    }
    _ensure_message_size(response)
    return response


def error_response(
    request: Mapping[str, Any] | None,
    *,
    status: str,
    code: str,
    message: str,
    exit_code: int = EXIT_GENERAL_ERROR,
) -> dict[str, Any]:
    if status not in VALID_STATES and status != "stopped":
        status = "error"
    response = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": str((request or {}).get("request_id", "")),
        "operation": str((request or {}).get("operation", "")),
        "ok": False,
        "status": status,
        "exit_code": int(exit_code),
        "result": {},
        "error": {"code": str(code), "message": str(message)},
    }
    _ensure_message_size(response)
    return response


def validate_response(message: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    if not isinstance(message, Mapping):
        raise ProtocolError("响应必须是 JSON 对象。")
    if message.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("响应协议版本不受支持。")
    if message.get("request_id") != request_id:
        raise ProtocolError("响应 request_id 与请求不匹配。")
    if not isinstance(message.get("ok"), bool):
        raise ProtocolError("响应缺少布尔值 ok。")
    if message.get("status") not in VALID_STATES | {"stopped"}:
        raise ProtocolError("响应状态无效。")
    exit_code = message.get("exit_code")
    if exit_code not in {
        EXIT_SUCCESS,
        EXIT_GENERAL_ERROR,
        EXIT_COMMUNICATION_ERROR,
        EXIT_DOWNLOAD_PENDING,
    }:
        raise ProtocolError("响应退出码无效。")
    if not isinstance(message.get("result"), Mapping):
        raise ProtocolError("响应 result 必须是 JSON 对象。")
    error = message.get("error")
    if error is not None and not isinstance(error, Mapping):
        raise ProtocolError("响应 error 格式无效。")
    normalized = dict(message)
    _ensure_message_size(normalized)
    return normalized


def connect_pipe(
    address: str = PIPE_ADDRESS,
    authkey: bytes = AUTHKEY,
) -> Any:
    if os.name != "nt":
        raise CommunicationError("Windows Named Pipe 仅可在 Windows 上建立。")
    from multiprocessing.connection import Client

    try:
        return Client(address=address, family="AF_PIPE", authkey=authkey)
    except (OSError, EOFError, AuthenticationError) as exc:
        raise CommunicationError(f"无法连接本地服务：{exc}") from exc


def create_pipe_listener(
    address: str = PIPE_ADDRESS,
    authkey: bytes = AUTHKEY,
) -> Any:
    if os.name != "nt":
        raise CommunicationError("Windows Named Pipe 仅可在 Windows 上监听。")
    from multiprocessing.connection import Listener

    try:
        return Listener(address=address, family="AF_PIPE", backlog=8, authkey=authkey)
    except (OSError, EOFError) as exc:
        raise CommunicationError(f"无法创建本地 Named Pipe：{exc}") from exc


def _windows_process_start_marker(pid: int) -> str | None:
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    get_process_times.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not get_process_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return f"win-filetime:{value}"
    finally:
        close_handle(handle)


def _proc_process_start_marker(pid: int) -> str | None:
    try:
        text = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    except (FileNotFoundError, OSError, UnicodeError):
        return None
    closing = text.rfind(")")
    if closing < 0:
        return None
    fields_after_name = text[closing + 2 :].split()
    if len(fields_after_name) <= 19:
        return None
    return f"proc-start-ticks:{fields_after_name[19]}"


def process_start_marker(pid: int) -> str | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        return _windows_process_start_marker(pid)
    if Path("/proc").is_dir():
        return _proc_process_start_marker(pid)
    return None


def make_process_record(
    *,
    startup_id: str,
    server_script: Path = SERVER_SCRIPT,
    executable: Path | None = None,
) -> dict[str, Any]:
    pid = os.getpid()
    marker = process_start_marker(pid)
    if marker is None:
        raise RuntimeError("无法读取当前服务进程的启动身份。")
    return {
        "schema_version": 1,
        "app_id": APP_ID,
        "pid": pid,
        "process_start_marker": marker,
        "startup_id": startup_id,
        "server_script": str(server_script.resolve()),
        "executable": str((executable or Path(sys.executable)).resolve()),
        "created_at": utc_now(),
    }


def is_server_record_current(
    record: Mapping[str, Any] | None,
    *,
    expected_script: Path = SERVER_SCRIPT,
) -> bool:
    if not isinstance(record, Mapping):
        return False
    if record.get("schema_version") != 1 or record.get("app_id") != APP_ID:
        return False
    try:
        pid = int(record.get("pid", 0))
    except (TypeError, ValueError):
        return False
    recorded_script = record.get("server_script")
    if not isinstance(recorded_script, str):
        return False
    try:
        if Path(recorded_script).resolve() != expected_script.resolve():
            return False
    except OSError:
        return False
    marker = process_start_marker(pid)
    return marker is not None and marker == record.get("process_start_marker")


def read_pid_record(runtime_dir: Path = RUNTIME_DIR) -> dict[str, Any] | None:
    return read_json_object(runtime_dir / PID_PATH.name)


def remove_stale_runtime_identity(runtime_dir: Path = RUNTIME_DIR) -> bool:
    """Remove only stale identity files; never terminate an unverified PID."""

    pid_path = runtime_dir / PID_PATH.name
    lock_path = runtime_dir / LOCK_PATH.name
    record = read_json_object(pid_path)
    if is_server_record_current(record):
        return False
    lock_record = read_json_object(lock_path)
    if is_server_record_current(lock_record):
        return False
    try:
        lock_age = time.time() - lock_path.stat().st_mtime
    except FileNotFoundError:
        lock_age = STARTUP_LOCK_GRACE_SECONDS
    except OSError:
        return False
    if lock_age < STARTUP_LOCK_GRACE_SECONDS:
        # Another client may have spawned the server between lock creation and
        # the atomic PID record write. A fresh, incomplete lock is not stale.
        return False
    removed = False
    for path in (pid_path, lock_path):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            pass
    return removed


def terminate_exact_server(
    record: Mapping[str, Any],
    *,
    timeout: float = 5.0,
) -> bool:
    """Terminate only the PID whose immutable start marker still matches."""

    if not is_server_record_current(record):
        return False
    pid = int(record["pid"])
    if pid == os.getpid():
        return False
    if os.name == "nt":
        from ctypes import wintypes

        process_terminate = 0x0001
        synchronize = 0x00100000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        terminate_process = kernel32.TerminateProcess
        terminate_process.argtypes = [wintypes.HANDLE, wintypes.UINT]
        terminate_process.restype = wintypes.BOOL
        wait_for_single_object = kernel32.WaitForSingleObject
        close_handle = kernel32.CloseHandle

        handle = open_process(process_terminate | synchronize, False, pid)
        if not handle:
            return False
        try:
            if not is_server_record_current(record):
                return False
            if not terminate_process(handle, EXIT_COMMUNICATION_ERROR):
                return False
            wait_for_single_object(handle, max(0, int(timeout * 1000)))
            return True
        finally:
            close_handle(handle)
    try:
        if not is_server_record_current(record):
            return False
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return False
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not is_server_record_current(record):
            return True
        time.sleep(0.05)
    return not is_server_record_current(record)


@dataclass(slots=True)
class RuntimeIdentityGuard:
    runtime_dir: Path
    startup_id: str
    server_script: Path = SERVER_SCRIPT
    _record: dict[str, Any] | None = None
    _acquired: bool = False

    @property
    def pid_path(self) -> Path:
        return self.runtime_dir / PID_PATH.name

    @property
    def lock_path(self) -> Path:
        return self.runtime_dir / LOCK_PATH.name

    @property
    def record(self) -> dict[str, Any]:
        if self._record is None:
            raise RuntimeError("服务启动身份尚未建立。")
        return dict(self._record)

    def acquire(self) -> dict[str, Any]:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        record = make_process_record(
            startup_id=self.startup_id,
            server_script=self.server_script,
        )
        for attempt in range(2):
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError as exc:
                existing = read_json_object(self.pid_path)
                lock_record = read_json_object(self.lock_path)
                if is_server_record_current(existing, expected_script=self.server_script):
                    raise AlreadyRunningError("本地服务已在运行。") from exc
                if is_server_record_current(lock_record, expected_script=self.server_script):
                    raise AlreadyRunningError("本地服务正在启动。") from exc
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age < STARTUP_LOCK_GRACE_SECONDS:
                    raise AlreadyRunningError("本地服务启动锁正在使用。") from exc
                if attempt == 0:
                    remove_stale_runtime_identity(self.runtime_dir)
                    continue
                raise AlreadyRunningError("无法安全恢复旧服务启动锁。") from exc
            else:
                try:
                    encoded = (
                        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                    ).encode("utf-8")
                    os.write(descriptor, encoded)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                atomic_write_json(self.pid_path, record)
                self._record = record
                self._acquired = True
                return dict(record)
        raise AlreadyRunningError("无法获取服务启动锁。")

    def release(self) -> None:
        if not self._acquired or self._record is None:
            return
        for path in (self.pid_path, self.lock_path):
            current = read_json_object(path)
            if (
                isinstance(current, dict)
                and current.get("startup_id") == self.startup_id
                and current.get("process_start_marker")
                == self._record.get("process_start_marker")
            ):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        self._acquired = False

    def __enter__(self) -> "RuntimeIdentityGuard":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


# AuthenticationError lives in multiprocessing.context on supported Python versions.
try:
    from multiprocessing import AuthenticationError
except ImportError:  # pragma: no cover - compatibility fallback
    AuthenticationError = OSError  # type: ignore[misc,assignment]
