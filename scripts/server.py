from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import json
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing
from pathlib import Path
import queue
import signal
import sys
import threading
import time
from typing import Any
import uuid


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from protocol import (  # noqa: E402
    AUTHKEY,
    DEFAULT_IDLE_TIMEOUT,
    EXIT_DOWNLOAD_PENDING,
    EXIT_GENERAL_ERROR,
    EXIT_SUCCESS,
    PIPE_ADDRESS,
    PROTOCOL_VERSION,
    RUNTIME_DIR,
    SERVER_LOG_PATH,
    AlreadyRunningError,
    AuthenticationError,
    ProtocolError,
    RuntimeIdentityGuard,
    atomic_write_json,
    configure_utf8_streams,
    create_pipe_listener,
    error_response,
    read_json_object,
    receive_message,
    send_message,
    success_response,
    utc_now,
    validate_request,
)
import model_download  # noqa: E402
from product_evidence_guard.confirmation import (  # noqa: E402
    ConfirmationError,
    ConfirmationRequestError,
    apply_decision,
    export_confirmed,
)
from product_evidence_guard.engine import (  # noqa: E402
    MAX_SINGLE_FILE_SECONDS,
    analyze_directory,
)
from product_evidence_guard.hybrid_image_reader import (  # noqa: E402
    HybridImageReader,
)
from product_evidence_guard.openvino_adapter import (  # noqa: E402
    OpenVinoFactExtractor,
    resolve_openvino_device,
)


LOGGER = logging.getLogger("product_evidence_guard.local_server")
MAX_PENDING_CONNECTIONS = 32
MAX_REQUEST_WORKERS = 16
_SAFE_LOG_EVENTS = {
    "connection_failed",
    "duplicate_server_start_refused",
    "operation_failed",
    "protocol_response_send_failed",
    "server_exited",
    "server_started",
    "server_stopped_unexpectedly",
}
_SAFE_LOG_OPERATIONS = {
    "analyze",
    "confirm",
    "export",
    "reject",
    "shutdown",
    "status",
}


def _safe_log_atom(value: object) -> str:
    text = str(value)
    if (
        0 < len(text) <= 128
        and text.isascii()
        and all(character.isalnum() or character in "._:-" for character in text)
    ):
        return text
    return "<redacted>"


def _log_event(level: int, event: str, **fields: object) -> None:
    """Write only bounded operational metadata to the persistent server log.

    Request payloads, exception messages, paths and model output are never log
    fields. Values outside the small ASCII atom contract are replaced instead
    of being escaped, so they cannot smuggle customer text or credentials into
    the log.
    """

    safe_event = event if event in _SAFE_LOG_EVENTS else "redacted_event"
    parts = [f"event={safe_event}"]
    for name, value in sorted(fields.items()):
        safe_name = _safe_log_atom(name)
        if (
            name == "operation"
            and isinstance(value, str)
            and value in _SAFE_LOG_OPERATIONS
        ):
            safe_value = str(value)
        elif name == "error_type":
            safe_value = _safe_log_atom(value)
        else:
            safe_value = "<redacted>"
        parts.append(f"{safe_name}={safe_value}")
    LOGGER.log(level, " ".join(parts))


def _select_worker_executable(
    current_executable: str,
    base_executable: str | None,
    *,
    platform: str,
) -> str | None:
    """Use the real Windows Python binary instead of the venv redirector.

    The Windows venv executable can remain as a small parent process while the
    actual interpreter becomes its child. ``multiprocessing.Process.terminate``
    would then stop only the redirector and orphan the multi-gigabyte OpenVINO
    worker. Spawning the base interpreter directly keeps the process handle
    attached to the process that owns the model.
    """

    if platform != "win32" or not base_executable:
        return None
    current = Path(current_executable).resolve()
    base = Path(base_executable).resolve()
    if current == base or not base.is_file():
        return None
    return str(base)


def configure_logging(runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    desired_path = (runtime_dir / SERVER_LOG_PATH.name).resolve()
    for current in list(LOGGER.handlers):
        if Path(getattr(current, "baseFilename", "")).resolve() == desired_path:
            return
        LOGGER.removeHandler(current)
        current.close()
    handler = RotatingFileHandler(
        desired_path,
        maxBytes=1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    LOGGER.addHandler(handler)


class ServerState:
    def __init__(
        self,
        *,
        runtime_dir: Path,
        startup_id: str,
        idle_timeout: float,
        pid: int,
        process_start_marker: str,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.startup_id = startup_id
        self.idle_timeout = idle_timeout
        self.pid = pid
        self.process_start_marker = process_start_marker
        self.status = "starting"
        self.last_error: str | None = None
        self.started_at = utc_now()
        self.updated_at = self.started_at
        self.last_activity = time.monotonic()
        self.shutdown_reason: str | None = None
        self.transition_history: list[str] = ["starting"]
        self._lock = threading.RLock()
        self._write()

    @property
    def state_path(self) -> Path:
        return self.runtime_dir / "server-state.json"

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "protocol_version": PROTOCOL_VERSION,
            "status": self.status,
            "pid": self.pid,
            "process_start_marker": self.process_start_marker,
            "startup_id": self.startup_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "idle_timeout_seconds": self.idle_timeout,
            "last_error": self.last_error,
            "shutdown_reason": self.shutdown_reason,
        }

    def _write(self) -> None:
        atomic_write_json(self.state_path, self._snapshot_unlocked())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()

    def touch(self) -> None:
        with self._lock:
            self.last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        with self._lock:
            return max(0.0, time.monotonic() - self.last_activity)

    def transition(
        self,
        status: str,
        *,
        error: str | None = None,
        shutdown_reason: str | None = None,
    ) -> None:
        with self._lock:
            self.status = status
            self.last_error = error
            if shutdown_reason is not None:
                self.shutdown_reason = shutdown_reason
            self.updated_at = utc_now()
            self.transition_history.append(status)
            self._write()


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"缺少必填参数：{key}")
    return value.strip()


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError(f"参数 {key} 必须是字符串。")
    stripped = value.strip()
    return stripped or None


class ResidentModelCache:
    """Keep the selected local model instances alive for this server process."""

    def __init__(
        self,
        *,
        llm_factory: Callable[[str, str], Any] = OpenVinoFactExtractor,
        vlm_factory: Callable[..., Any] = HybridImageReader.from_openvino,
        device_resolver: Callable[[str], Any] = resolve_openvino_device,
    ) -> None:
        self._llm_factory = llm_factory
        self._vlm_factory = vlm_factory
        self._device_resolver = device_resolver
        self._key: tuple[str | None, str | None, str] | None = None
        self._llm: Any | None = None
        self._vlm: Any | None = None
        self._selection: Any | None = None

    def prepare(
        self,
        *,
        openvino_model: str | None,
        openvino_vlm_model: str | None,
        requested_device: str,
    ) -> dict[str, Any]:
        # A deterministic-only analysis must remain usable on a clean machine
        # where OpenVINO is not installed.  Bypass device discovery entirely
        # and leave any resident model cache intact for a later model request.
        if not openvino_model and not openvino_vlm_model:
            return {
                "llm": None,
                "vlm": None,
                "selection": None,
                "load_seconds": 0.0,
                "reused": False,
            }
        selection = self._device_resolver(requested_device)
        key = (
            str(Path(openvino_model).expanduser().resolve())
            if openvino_model
            else None,
            str(Path(openvino_vlm_model).expanduser().resolve())
            if openvino_vlm_model
            else None,
            selection.actual,
        )
        if self._key == key:
            # The loaded pipelines are reusable when AUTO and an explicit
            # request resolve to the same actual device, but the audit metadata
            # must describe this request rather than the request that first
            # populated the cache.
            self._selection = selection
            return {
                "llm": self._llm,
                "vlm": self._vlm,
                "selection": selection,
                "load_seconds": 0.0,
                "reused": True,
            }

        started = time.perf_counter()
        llm = (
            self._llm_factory(key[0], selection.actual)
            if key[0]
            else None
        )
        vlm = (
            self._vlm_factory(
                key[1],
                device=selection.actual,
                model_id="OpenVINO/Qwen3-VL-8B-Instruct-int4-ov",
            )
            if key[1]
            else None
        )
        load_seconds = round(time.perf_counter() - started, 4)
        self._key = key
        self._llm = llm
        self._vlm = vlm
        self._selection = selection
        return {
            "llm": llm,
            "vlm": vlm,
            "selection": selection,
            "load_seconds": load_seconds,
            "reused": False,
        }


def _resident_analysis_worker_main(connection: Any) -> None:
    """Own OpenVINO pipelines in a child process so the pipe stays responsive.

    Some native generation calls can retain the Python GIL for long periods.
    Keeping inference in this persistent worker lets the lightweight parent
    server continue accepting ``status`` requests while the model is loading
    or generating. The worker itself remains alive and reuses its model cache.
    """

    class ParentDisconnected(Exception):
        """Stop this exact worker quietly when its parent pipe disappears."""

    def send_to_parent(message: Mapping[str, Any]) -> None:
        try:
            connection.send(dict(message))
        except (BrokenPipeError, EOFError, OSError) as exc:
            raise ParentDisconnected from exc

    cache = ResidentModelCache()
    while True:
        try:
            message = connection.recv()
        except (EOFError, OSError):
            break
        if not isinstance(message, dict):
            continue
        if message.get("operation") == "shutdown":
            break
        if message.get("operation") != "analyze":
            continue
        job_id = str(message.get("job_id", ""))
        payload = message.get("payload")
        if not isinstance(payload, dict):
            try:
                send_to_parent(
                    {
                        "type": "error",
                        "job_id": job_id,
                        "message": "模型工作进程收到无效 payload。",
                    }
                )
            except ParentDisconnected:
                break
            continue
        try:
            prepared = cache.prepare(
                openvino_model=payload.get("openvino_model"),
                openvino_vlm_model=payload.get("openvino_vlm_model"),
                requested_device=str(payload.get("device") or "AUTO"),
            )
            send_to_parent(
                {
                    "type": "model_ready",
                    "job_id": job_id,
                    "load_seconds": prepared["load_seconds"],
                    "reused": prepared["reused"],
                }
            )
            summary = analyze_directory(
                payload["input_dir"],
                payload["output_dir"],
                openvino_model=payload.get("openvino_model"),
                openvino_vlm_model=payload.get("openvino_vlm_model"),
                device=str(payload.get("device") or "AUTO"),
                preloaded_llm_extractor=prepared["llm"],
                preloaded_image_reader=prepared["vlm"],
                preloaded_model_load_seconds=prepared["load_seconds"],
                preloaded_model_reused=prepared["reused"],
                device_selection=prepared["selection"],
                progress_callback=lambda stage, details: send_to_parent(
                    {
                        "type": "progress",
                        "job_id": job_id,
                        "stage": stage,
                        "details": dict(details),
                    }
                ),
            )
            send_to_parent(
                {
                    "type": "result",
                    "job_id": job_id,
                    "summary": summary,
                }
            )
        except ParentDisconnected:
            break
        except Exception as exc:
            try:
                send_to_parent(
                    {
                        "type": "error",
                        "job_id": job_id,
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
            except ParentDisconnected:
                break
    try:
        connection.close()
    except OSError:
        pass


class ResidentAnalysisWorker:
    """Parent-side controller for the exact persistent inference child."""

    def __init__(self) -> None:
        self._process: Any | None = None
        self._connection: Any | None = None
        self._lifecycle_lock = threading.Lock()

    @staticmethod
    def _dispose(
        process: Any | None,
        connection: Any | None,
        *,
        force: bool = False,
    ) -> None:
        if connection is not None:
            if not force and process is not None and process.is_alive():
                try:
                    connection.send({"operation": "shutdown"})
                except (EOFError, OSError):
                    pass
            try:
                connection.close()
            except OSError:
                pass
        if process is not None and process.is_alive():
            if force:
                process.terminate()
                process.join(timeout=5.0)
                return
            process.join(timeout=10.0)
            if process.is_alive():
                # This is the exact child created by this controller.
                process.terminate()
                process.join(timeout=5.0)

    def _ensure_started(self) -> tuple[Any, Any]:
        with self._lifecycle_lock:
            if self._process is not None and self._process.is_alive():
                assert self._connection is not None
                return self._process, self._connection
            old_process = self._process
            old_connection = self._connection
            self._process = None
            self._connection = None
        self._dispose(old_process, old_connection)

        worker_executable = _select_worker_executable(
            sys.executable,
            getattr(sys, "_base_executable", None),
            platform=sys.platform,
        )
        if worker_executable is not None:
            multiprocessing.set_executable(worker_executable)
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_resident_analysis_worker_main,
            args=(child,),
            name="peg-openvino-worker",
            daemon=True,
        )
        process.start()
        child.close()
        with self._lifecycle_lock:
            self._connection = parent
            self._process = process
        return process, parent

    def _discard_if_current(
        self,
        process: Any,
        connection: Any,
        *,
        force: bool = False,
    ) -> None:
        with self._lifecycle_lock:
            if self._process is process:
                self._process = None
            if self._connection is connection:
                self._connection = None
        self._dispose(process, connection, force=force)

    def analyze(
        self,
        payload: Mapping[str, Any],
        *,
        on_model_ready: Callable[[], None],
        timeout_seconds: float = MAX_SINGLE_FILE_SECONDS,
    ) -> Mapping[str, Any]:
        if timeout_seconds <= 0:
            raise ValueError("analysis timeout_seconds 必须大于 0。")
        process, connection = self._ensure_started()
        job_id = uuid.uuid4().hex
        deadline = time.monotonic() + timeout_seconds
        try:
            connection.send(
                {
                    "operation": "analyze",
                    "job_id": job_id,
                    "payload": dict(payload),
                }
            )
        except (EOFError, OSError) as exc:
            raise RuntimeError("无法向本地模型工作进程发送任务。") from exc

        def raise_timeout() -> None:
            self._discard_if_current(process, connection, force=True)
            raise TimeoutError(
                f"本地分析阶段超过 {timeout_seconds:g} 秒安全期限；"
                "已终止该任务的精确模型工作进程，"
                "未强制结束其他 Python 进程。"
            )

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise_timeout()
            if connection.poll(min(0.25, remaining)):
                try:
                    response = connection.recv()
                except (EOFError, OSError) as exc:
                    raise RuntimeError("本地模型工作进程连接意外关闭。") from exc
                if time.monotonic() >= deadline:
                    raise_timeout()
                if not isinstance(response, dict) or response.get("job_id") != job_id:
                    continue
                response_type = response.get("type")
                if response_type == "model_ready":
                    on_model_ready()
                    deadline = time.monotonic() + timeout_seconds
                    continue
                if response_type == "progress":
                    # The child emits this only at trusted engine boundaries.
                    # Resetting here enforces 300 seconds per file/stage instead
                    # of incorrectly limiting the whole multi-file folder.
                    deadline = time.monotonic() + timeout_seconds
                    continue
                if response_type == "result":
                    summary = response.get("summary")
                    if not isinstance(summary, Mapping):
                        raise RuntimeError("本地模型工作进程返回了无效摘要。")
                    return dict(summary)
                if response_type == "error":
                    raise RuntimeError(str(response.get("message") or "本地模型任务失败。"))
            if not process.is_alive():
                raise RuntimeError(
                    f"本地模型工作进程异常退出，exit_code={process.exitcode}。"
                )

    def close(self) -> None:
        with self._lifecycle_lock:
            process = self._process
            connection = self._connection
            self._process = None
            self._connection = None
        self._dispose(process, connection)


class ServerApplication:
    """Pure request dispatcher; transports are injected by the server loop/tests."""

    def __init__(
        self,
        state: ServerState,
        stop_event: threading.Event,
        *,
        analyze: Callable[..., Mapping[str, Any]] = analyze_directory,
        decide: Callable[..., Any] = apply_decision,
        export: Callable[..., Mapping[str, Any]] = export_confirmed,
        model_cache: ResidentModelCache | None = None,
        analysis_worker: ResidentAnalysisWorker | None = None,
    ) -> None:
        self.state = state
        self.stop_event = stop_event
        self._analyze = analyze
        self._decide = decide
        self._export = export
        self._model_cache = model_cache or ResidentModelCache()
        self._supports_resident_models = analyze is analyze_directory
        self._analysis_worker = analysis_worker or ResidentAnalysisWorker()
        self._operation_lock = threading.Lock()

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        validated = validate_request(request)
        operation = validated["operation"]
        payload = validated["payload"]
        self.state.touch()
        if operation == "status":
            download_state = read_json_object(
                self.state.runtime_dir / model_download.DOWNLOAD_STATE_NAME
            ) or {}
            download_active = download_state.get("status") == "downloading"
            return success_response(
                validated,
                status="downloading" if download_active else self.state.status,
                result={
                    "server": self.state.snapshot(),
                    "download": download_state,
                    "available_operations": [
                        "status",
                        "analyze",
                        "confirm",
                        "reject",
                        "export",
                        "shutdown",
                    ],
                },
                exit_code=(
                    EXIT_DOWNLOAD_PENDING if download_active else EXIT_SUCCESS
                ),
            )
        if operation == "shutdown":
            # A shutdown waits for the current mutating operation so analysis
            # output cannot be cut off halfway through an atomic workflow.
            with self._operation_lock:
                self._analysis_worker.close()
                self.state.transition("shutdown", shutdown_reason="requested")
                self.stop_event.set()
                return success_response(
                    validated,
                    status="shutdown",
                    result={"message": "本地服务已收到安全关闭请求。"},
                )
        with self._operation_lock:
            try:
                if operation == "analyze":
                    return self._dispatch_analyze(validated, payload)
                if operation in {"confirm", "reject"}:
                    return self._dispatch_decision(validated, payload, operation)
                if operation == "export":
                    return self._dispatch_export(validated, payload)
                raise ProtocolError(f"不支持的操作：{operation}")
            except ConfirmationRequestError as exc:
                # A stale/missing candidate or mismatched session is a rejected
                # business request, not a server health failure. Keep the
                # resident service available for the user's corrected request.
                self.state.transition("running")
                return error_response(
                    validated,
                    status="running",
                    code="operation_failed",
                    message=f"ConfirmationError: {exc}",
                    exit_code=EXIT_GENERAL_ERROR,
                )
            except ProtocolError as exc:
                self.state.transition("error", error=str(exc))
                return error_response(
                    validated,
                    status="error",
                    code="invalid_request",
                    message=str(exc),
                    exit_code=EXIT_GENERAL_ERROR,
                )
            except Exception as exc:
                safe_error = f"{type(exc).__name__}: {exc}"
                self.state.transition("error", error=safe_error)
                _log_event(
                    logging.ERROR,
                    "operation_failed",
                    operation=operation,
                    error_type=type(exc).__name__,
                )
                return error_response(
                    validated,
                    status="error",
                    code="operation_failed",
                    message=safe_error,
                    exit_code=EXIT_GENERAL_ERROR,
                )

    def _dispatch_analyze(
        self,
        request: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        input_dir = _required_text(payload, "input_dir")
        output_dir = _optional_text(payload, "output_dir")
        if output_dir is None:
            output_dir = str(Path(input_dir).expanduser() / ".peg-output")
        openvino_model = _optional_text(payload, "openvino_model")
        openvino_vlm_model = _optional_text(payload, "openvino_vlm_model")
        device = _optional_text(payload, "device") or "AUTO"

        for model_path in (openvino_model, openvino_vlm_model):
            if model_path and not Path(model_path).expanduser().is_dir():
                pending = read_json_object(
                    self.state.runtime_dir / model_download.DOWNLOAD_STATE_NAME
                ) or read_json_object(
                    self.state.runtime_dir / "pending-request.json"
                )
                if pending and pending.get("status") == "downloading":
                    self.state.transition("downloading")
                    return error_response(
                        request,
                        status="downloading",
                        code="model_downloading",
                        message=(
                            "模型仍在下载，请稍后运行 "
                            "scripts\\run.ps1 --continue。"
                        ),
                        exit_code=EXIT_DOWNLOAD_PENDING,
                    )
                raise ProtocolError(f"本地模型目录不存在：{model_path}")

        analyze_options: dict[str, Any] = {
            "openvino_model": openvino_model,
            "openvino_vlm_model": openvino_vlm_model,
            "device": device,
        }
        if openvino_model or openvino_vlm_model:
            self.state.transition("loading")
        if self._supports_resident_models:
            result = self._analysis_worker.analyze(
                {
                    "input_dir": input_dir,
                    "output_dir": output_dir,
                    **analyze_options,
                },
                on_model_ready=lambda: self.state.transition("running"),
            )
        else:
            self.state.transition("running")
            result = self._analyze(
                input_dir,
                output_dir,
                **analyze_options,
            )
        self.state.transition("running")
        return success_response(
            request,
            status="running",
            result={"summary": dict(result), "output_dir": output_dir},
            exit_code=EXIT_SUCCESS,
        )

    def _dispatch_decision(
        self,
        request: Mapping[str, Any],
        payload: Mapping[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        self.state.transition("running")
        result = self._decide(
            _required_text(payload, "output_dir"),
            session_id=_required_text(payload, "session_id"),
            candidate_id=_required_text(payload, "candidate_id"),
            reason=_required_text(payload, "reason"),
            action=operation,
        )
        serialized = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        self.state.transition("running")
        return success_response(
            request,
            status="running",
            result={"decision": serialized},
        )

    def _dispatch_export(
        self,
        request: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        self.state.transition("running")
        result = self._export(
            _required_text(payload, "output_dir"),
            session_id=_required_text(payload, "session_id"),
        )
        self.state.transition("running")
        return success_response(
            request,
            status="running",
            result={"export": dict(result)},
        )


def handle_connection(connection: Any, application: ServerApplication) -> None:
    """Read exactly one request, send exactly one response, then close."""

    request: dict[str, Any] | None = None
    try:
        request = receive_message(connection)
        response = application.dispatch(request)
    except (ProtocolError, EOFError) as exc:
        response = error_response(
            request,
            status="error",
            code="protocol_error",
            message=str(exc),
            exit_code=EXIT_GENERAL_ERROR,
        )
    except Exception as exc:
        _log_event(
            logging.ERROR,
            "connection_failed",
            error_type=type(exc).__name__,
        )
        response = error_response(
            request,
            status="error",
            code="connection_error",
            message=f"{type(exc).__name__}: {exc}",
            exit_code=EXIT_GENERAL_ERROR,
        )
    try:
        send_message(connection, response)
    except (OSError, ProtocolError):
        _log_event(logging.WARNING, "protocol_response_send_failed")
    finally:
        try:
            connection.close()
        except OSError:
            pass


def _accept_connections(
    listener: Any,
    connection_queue: queue.Queue[Any],
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            connection = listener.accept()
        except (OSError, EOFError, AuthenticationError):
            # A client can abandon a Windows pipe while the authentication
            # handshake is pending. That connection must not permanently kill
            # the accept loop; a closed listener is paired with stop_event and
            # exits on the next condition check.
            if stop_event.is_set():
                return
            time.sleep(0.05)
            continue
        while True:
            try:
                connection_queue.put(connection, timeout=0.25)
                break
            except queue.Full:
                if stop_event.is_set():
                    try:
                        close = getattr(connection, "close", None)
                        if callable(close):
                            close()
                    except OSError:
                        pass
                    return
                continue


def serve_forever(
    *,
    address: str = PIPE_ADDRESS,
    runtime_dir: Path = RUNTIME_DIR,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    startup_id: str | None = None,
    listener_factory: Callable[[str, bytes], Any] = create_pipe_listener,
) -> int:
    startup_id = startup_id or uuid.uuid4().hex
    configure_logging(runtime_dir)
    guard = RuntimeIdentityGuard(
        runtime_dir=runtime_dir,
        startup_id=startup_id,
        server_script=Path(__file__).resolve(),
    )
    try:
        record = guard.acquire()
    except AlreadyRunningError:
        _log_event(logging.WARNING, "duplicate_server_start_refused")
        return EXIT_GENERAL_ERROR

    stop_event = threading.Event()
    state = ServerState(
        runtime_dir=runtime_dir,
        startup_id=startup_id,
        idle_timeout=idle_timeout,
        pid=int(record["pid"]),
        process_start_marker=str(record["process_start_marker"]),
    )
    application = ServerApplication(state, stop_event)
    listener: Any | None = None
    previous_handlers: dict[int, Any] = {}
    workers: list[threading.Thread] = []

    def request_stop(signum: int, _frame: Any) -> None:
        state.transition("shutdown", shutdown_reason=f"signal:{signum}")
        stop_event.set()
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[signum] = signal.signal(signum, request_stop)
        except (OSError, ValueError):
            pass

    try:
        listener = listener_factory(address, AUTHKEY)
        state.transition("running")
        _log_event(logging.INFO, "server_started")
        connections: queue.Queue[Any] = queue.Queue(
            maxsize=MAX_PENDING_CONNECTIONS
        )
        accept_thread = threading.Thread(
            target=_accept_connections,
            args=(listener, connections, stop_event),
            name="peg-pipe-accept",
            daemon=True,
        )
        accept_thread.start()

        def serve_connection(connection: Any) -> None:
            try:
                handle_connection(connection, application)
            finally:
                state.touch()

        while not stop_event.is_set():
            workers = [worker for worker in workers if worker.is_alive()]
            if workers:
                remaining = idle_timeout
            else:
                remaining = idle_timeout - state.idle_seconds()
            if remaining <= 0:
                state.transition("shutdown", shutdown_reason="idle_timeout")
                stop_event.set()
                break
            if len(workers) >= MAX_REQUEST_WORKERS:
                time.sleep(min(0.05, remaining))
                continue
            try:
                connection = connections.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            state.touch()
            worker = threading.Thread(
                target=serve_connection,
                args=(connection,),
                name="peg-pipe-request",
                daemon=True,
            )
            workers.append(worker)
            worker.start()
        return EXIT_SUCCESS
    except Exception as exc:
        state.transition("error", error=f"{type(exc).__name__}: {exc}")
        _log_event(
            logging.ERROR,
            "server_stopped_unexpectedly",
            error_type=type(exc).__name__,
        )
        return EXIT_GENERAL_ERROR
    finally:
        stop_event.set()
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        current_thread = threading.current_thread()
        for worker in workers:
            if worker is not current_thread and worker.is_alive():
                worker.join(timeout=5.0)
        application._analysis_worker.close()
        if state.status not in {"shutdown", "error"}:
            state.transition("shutdown", shutdown_reason="server_exit")
        guard.release()
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass
        _log_event(logging.INFO, "server_exited")
        for handler in list(LOGGER.handlers):
            LOGGER.removeHandler(handler)
            handler.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Product Evidence Guard local server")
    parser.add_argument("--startup-id", default=None)
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT)
    parser.add_argument("--runtime-dir", default=str(RUNTIME_DIR))
    parser.add_argument("--pipe-address", default=PIPE_ADDRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_streams()
    args = build_parser().parse_args(argv)
    if args.idle_timeout <= 0:
        print(
            json.dumps(
                {"ok": False, "error": "idle-timeout 必须大于 0。"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return EXIT_GENERAL_ERROR
    return serve_forever(
        address=args.pipe_address,
        runtime_dir=Path(args.runtime_dir).expanduser().resolve(),
        idle_timeout=args.idle_timeout,
        startup_id=args.startup_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
