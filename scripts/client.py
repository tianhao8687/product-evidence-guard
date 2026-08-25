from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import uuid


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import model_download  # noqa: E402
from protocol import (  # noqa: E402
    AUTHKEY,
    EXIT_COMMUNICATION_ERROR,
    EXIT_DOWNLOAD_PENDING,
    EXIT_GENERAL_ERROR,
    EXIT_SUCCESS,
    PIPE_ADDRESS,
    RUNTIME_DIR,
    SERVER_LOG_PATH,
    SERVER_SCRIPT,
    STARTUP_LOCK_GRACE_SECONDS,
    CommunicationError,
    ProtocolError,
    atomic_write_json,
    build_request,
    configure_utf8_streams,
    connect_pipe,
    error_response,
    is_server_record_current,
    read_json_object,
    read_pid_record,
    receive_message,
    remove_stale_runtime_identity,
    send_message,
    success_response,
    terminate_exact_server,
    utc_now,
    validate_response,
)


DEFAULT_START_TIMEOUT = 20.0
DEFAULT_STATUS_TIMEOUT = 2.0
# The server enforces a 300-second deadline for each model/file stage. A folder
# can legitimately contain several files, so the short client keeps a bounded
# one-hour request window rather than imposing a second 330-second task limit.
DEFAULT_REQUEST_TIMEOUT = 60.0 * 60.0
DEFAULT_DOWNLOAD_WAIT_TIMEOUT = 8.0 * 60.0
DEFAULT_DOWNLOAD_POLL_INTERVAL = 1.0
PENDING_ANALYSIS_NAME = "pending-analysis.json"
MODEL_DOWNLOAD_SCRIPT = SCRIPT_DIR / "model_download.py"
MODEL_DOWNLOAD_LOG_NAME = "model-download.log"
_SAFE_RUNTIME_LOG_EVENTS = {
    "model_download_worker_spawned",
    "server_process_spawned",
}


def _write_safe_runtime_log_event(
    path: Path,
    event: str,
    *,
    replace: bool = False,
) -> None:
    """Persist one fixed event without child output, arguments or host paths."""

    if event not in _SAFE_RUNTIME_LOG_EVENTS:
        raise ValueError("Unsupported runtime log event.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        stat = path.lstat()
        if path.is_symlink() or not path.is_file() or stat.st_nlink != 1:
            raise RuntimeError("Refusing unsafe runtime log target.")
    mode = "w" if replace else "a"
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        handle.write(f"{utc_now()} event={event}\n")


class CliUsageError(ValueError):
    pass


class DownloadPending(RuntimeError):
    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__("模型仍在下载。")
        self.result = dict(result)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _print_json(data: Mapping[str, Any]) -> None:
    print(
        json.dumps(
            dict(data),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _local_request(operation: str) -> dict[str, Any]:
    return {
        "request_id": f"client-{uuid.uuid4().hex}",
        "operation": operation,
    }


def _local_error(
    operation: str,
    *,
    code: str,
    message: str,
    exit_code: int,
    status: str = "error",
) -> dict[str, Any]:
    return error_response(
        _local_request(operation),
        status=status,
        code=code,
        message=message,
        exit_code=exit_code,
    )


def _local_success(
    operation: str,
    *,
    status: str,
    result: Mapping[str, Any],
    exit_code: int = EXIT_SUCCESS,
) -> dict[str, Any]:
    return success_response(
        _local_request(operation),
        status=status,
        result=result,
        exit_code=exit_code,
    )


def exchange(
    request: Mapping[str, Any],
    *,
    connector: Callable[[str, bytes], Any] = connect_pipe,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> dict[str, Any]:
    connection: Any | None = None
    try:
        connection = connector(PIPE_ADDRESS, AUTHKEY)
        send_message(connection, request)
        poll = getattr(connection, "poll", None)
        if callable(poll) and not poll(timeout):
            raise CommunicationError("等待本地服务响应超时。")
        response = receive_message(connection)
        return validate_response(response, str(request["request_id"]))
    except CommunicationError:
        raise
    except (OSError, EOFError, ProtocolError) as exc:
        raise CommunicationError(f"本地服务通信失败：{exc}") from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def _probe(
    *,
    connector: Callable[[str, bytes], Any],
    timeout: float = DEFAULT_STATUS_TIMEOUT,
) -> dict[str, Any]:
    return exchange(
        build_request("status"),
        connector=connector,
        timeout=timeout,
    )


def start_server_process(
    startup_id: str,
    *,
    runtime_dir: Path = RUNTIME_DIR,
) -> int:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-B",
        str(SERVER_SCRIPT),
        "--startup-id",
        startup_id,
        "--runtime-dir",
        str(runtime_dir),
    ]
    creationflags = 0
    popen_options: dict[str, Any] = {
        "cwd": str(REPO_ROOT),
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        popen_options["creationflags"] = creationflags
    else:
        popen_options["start_new_session"] = True
    log_path = runtime_dir / SERVER_LOG_PATH.name
    # Older versions captured child stderr and may have retained host paths.
    # Reset the bounded rotating family when starting the privacy-safe server.
    for backup_index in (1, 2):
        log_path.with_name(f"{log_path.name}.{backup_index}").unlink(
            missing_ok=True
        )
    _write_safe_runtime_log_event(
        log_path,
        "server_process_spawned",
        replace=True,
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **popen_options,
    )
    return int(process.pid)


def ensure_server(
    *,
    connector: Callable[[str, bytes], Any] = connect_pipe,
    starter: Callable[[str], int] | None = None,
    runtime_dir: Path = RUNTIME_DIR,
    start_timeout: float = DEFAULT_START_TIMEOUT,
) -> dict[str, Any]:
    try:
        return _probe(connector=connector)
    except CommunicationError:
        pass

    existing = read_pid_record(runtime_dir)
    if is_server_record_current(existing):
        deadline = time.monotonic() + min(5.0, max(0.0, start_timeout))
        while time.monotonic() < deadline:
            try:
                return _probe(connector=connector)
            except CommunicationError:
                time.sleep(0.1)
        disk_state = read_json_object(runtime_dir / "server-state.json") or {}
        if disk_state.get("status") in {"downloading", "loading"}:
            raise CommunicationError("本地服务正忙，尚不能接受该请求。")
        if not terminate_exact_server(existing):
            raise CommunicationError("旧服务无响应，且无法通过精确身份安全恢复。")
        remove_stale_runtime_identity(runtime_dir)
    else:
        lock_path = runtime_dir / "server.lock"
        if lock_path.exists():
            deadline = time.monotonic() + max(0.1, start_timeout)
            while time.monotonic() < deadline:
                try:
                    return _probe(connector=connector)
                except CommunicationError:
                    pass
                current = read_pid_record(runtime_dir)
                if is_server_record_current(current):
                    time.sleep(0.1)
                    continue
                try:
                    lock_age = time.time() - lock_path.stat().st_mtime
                except FileNotFoundError:
                    break
                except OSError as exc:
                    raise CommunicationError(
                        f"无法检查服务启动锁：{exc}"
                    ) from exc
                if lock_age >= STARTUP_LOCK_GRACE_SECONDS:
                    break
                time.sleep(0.1)
            current = read_pid_record(runtime_dir)
            if is_server_record_current(current):
                raise CommunicationError("本地服务进程已启动，但 Named Pipe 未就绪。")
        remove_stale_runtime_identity(runtime_dir)
        if lock_path.exists():
            raise CommunicationError("无法安全清理仍在使用的服务启动锁。")

    startup_id = uuid.uuid4().hex
    launch = starter or (
        lambda value: start_server_process(value, runtime_dir=runtime_dir)
    )
    launch(startup_id)
    deadline = time.monotonic() + max(0.1, start_timeout)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _probe(connector=connector)
        except CommunicationError as exc:
            last_error = exc
            time.sleep(0.1)
    raise CommunicationError(
        f"本地服务启动超时：{last_error or 'Named Pipe 未就绪'}"
    )


def status_when_disconnected(
    *,
    runtime_dir: Path = RUNTIME_DIR,
) -> tuple[dict[str, Any], int]:
    download_state = read_json_object(
        runtime_dir / model_download.DOWNLOAD_STATE_NAME
    ) or {}
    state = read_json_object(runtime_dir / "server-state.json") or {}
    if download_state.get("status") == "downloading":
        response = _local_success(
            "status",
            status="downloading",
            result={
                "server": state,
                "download": download_state,
                "message": "模型仍在下载，请运行 scripts\\run.ps1 --continue。",
            },
            exit_code=EXIT_DOWNLOAD_PENDING,
        )
        return response, EXIT_DOWNLOAD_PENDING
    status = state.get("status")
    if status == "downloading":
        response = _local_success(
            "status",
            status="downloading",
            result={
                "server": state,
                "message": "模型仍在下载，请运行 scripts\\run.ps1 --continue。",
            },
            exit_code=EXIT_DOWNLOAD_PENDING,
        )
        return response, EXIT_DOWNLOAD_PENDING
    record = read_pid_record(runtime_dir)
    if is_server_record_current(record):
        current_status = (
            str(status)
            if status in {"starting", "downloading", "loading", "running", "error"}
            else "error"
        )
        if current_status != "error":
            response = _local_success(
                "status",
                status=current_status,
                result={
                    "server": state,
                    "transport": "verified_state_file_fallback",
                    "pipe_reachable": False,
                    "message": (
                        "服务进程身份有效；Named Pipe 正忙，返回原子状态快照。"
                    ),
                },
            )
            return response, EXIT_SUCCESS
        response = _local_error(
            "status",
            status="error",
            code="server_unreachable",
            message="服务进程存在，但 Named Pipe 无响应且状态为 error。",
            exit_code=EXIT_COMMUNICATION_ERROR,
        )
        response["result"] = {
            "server": state,
            "transport": "verified_state_file_fallback",
            "pipe_reachable": False,
        }
        return response, EXIT_COMMUNICATION_ERROR
    response = _local_success(
        "status",
        status="stopped",
        result={"server": state, "message": "本地服务未运行。"},
    )
    return response, EXIT_SUCCESS


def _pending_analysis_path(runtime_dir: Path) -> Path:
    return runtime_dir / PENDING_ANALYSIS_NAME


def _save_pending_analysis(
    payload: Mapping[str, Any],
    *,
    runtime_dir: Path,
) -> None:
    existing = _load_pending_analysis(runtime_dir)
    candidate = dict(payload)
    if existing is not None:
        if existing == candidate:
            return
        raise CliUsageError(
            "已有另一个待恢复分析请求；请先运行 scripts\\run.ps1 --continue。"
        )
    atomic_write_json(
        _pending_analysis_path(runtime_dir),
        {
            "schema_version": 1,
            "operation": "analyze",
            "payload": candidate,
        },
    )


def _load_pending_analysis(runtime_dir: Path) -> dict[str, Any] | None:
    data = read_json_object(_pending_analysis_path(runtime_dir))
    if (
        not data
        or data.get("schema_version") != 1
        or data.get("operation") != "analyze"
        or not isinstance(data.get("payload"), dict)
    ):
        return None
    return dict(data["payload"])


def _clear_pending_analysis(runtime_dir: Path) -> None:
    try:
        _pending_analysis_path(runtime_dir).unlink()
    except FileNotFoundError:
        pass


def _download_wait_timeout() -> float:
    raw = os.environ.get("PRODUCT_EVIDENCE_DOWNLOAD_WAIT_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_DOWNLOAD_WAIT_TIMEOUT
    try:
        value = float(raw)
    except ValueError as exc:
        raise CliUsageError(
            "PRODUCT_EVIDENCE_DOWNLOAD_WAIT_TIMEOUT 必须是秒数。"
        ) from exc
    return min(DEFAULT_DOWNLOAD_WAIT_TIMEOUT, max(0.0, value))


def _download_worker_runtime_dir(runtime_dir: Path) -> Path:
    return runtime_dir / model_download.DOWNLOAD_WORKER_DIR_NAME


def _download_worker_is_running(runtime_dir: Path) -> bool:
    worker_dir = _download_worker_runtime_dir(runtime_dir)
    record = read_pid_record(worker_dir)
    return is_server_record_current(
        record,
        expected_script=MODEL_DOWNLOAD_SCRIPT,
    )


def start_model_download_process(
    spec: model_download.DownloadSpec,
    *,
    runtime_dir: Path,
) -> int:
    """Start one hidden helper that may outlive the host's client call."""

    runtime_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-B",
        str(MODEL_DOWNLOAD_SCRIPT),
        "--model-id",
        spec.model_id,
        "--target",
        str(spec.target),
        "--revision",
        spec.revision,
        "--runtime-dir",
        str(runtime_dir),
    ]
    popen_options: dict[str, Any] = {
        "cwd": str(REPO_ROOT),
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_options["creationflags"] = getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0,
        )
    else:
        popen_options["start_new_session"] = True
    log_path = runtime_dir / MODEL_DOWNLOAD_LOG_NAME
    # Third-party download progress and exception text may contain local paths
    # or credentials. The structured state JSON remains the diagnostic source;
    # the persistent log records only this fixed lifecycle event.
    _write_safe_runtime_log_event(
        log_path,
        "model_download_worker_spawned",
        replace=True,
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **popen_options,
    )
    return int(process.pid)


def coordinate_model_download(
    spec: model_download.DownloadSpec,
    *,
    runtime_dir: Path,
    wait_timeout: float | None = None,
    poll_interval: float = DEFAULT_DOWNLOAD_POLL_INTERVAL,
    starter: Callable[..., int] = start_model_download_process,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait within the host budget while a detached helper resumes `.partial`."""

    errors = model_download.verify_model_directory(
        spec.target,
        spec.required_files,
    )
    if not errors:
        return {
            "status": "ready",
            "downloaded": False,
            "model_id": spec.model_id,
            "model_path": str(spec.target),
            "revision": spec.revision,
        }
    if spec.target.exists():
        return {
            "status": "error",
            "downloaded": False,
            "model_id": spec.model_id,
            "model_path": str(spec.target),
            "revision": spec.revision,
            "retryable": False,
            "error": (
                "正式模型目录存在但完整性检查失败："
                + "; ".join(errors[:5])
            ),
        }

    worker_pid: int | None = None
    if not _download_worker_is_running(runtime_dir):
        atomic_write_json(
            runtime_dir / model_download.DOWNLOAD_STATE_NAME,
            {
                "schema_version": 1,
                "status": "downloading",
                "active": True,
                "retryable": True,
                "model_id": spec.model_id,
                "target": str(spec.target),
                "partial": str(
                    spec.target.with_name(spec.target.name + ".partial")
                ),
                "revision": spec.revision,
                "updated_at": utc_now(),
                "last_error": None,
            },
        )
        try:
            worker_pid = int(starter(spec, runtime_dir=runtime_dir))
        except Exception as exc:
            atomic_write_json(
                runtime_dir / model_download.DOWNLOAD_STATE_NAME,
                {
                    "schema_version": 1,
                    "status": "error",
                    "active": False,
                    "retryable": False,
                    "model_id": spec.model_id,
                    "target": str(spec.target),
                    "revision": spec.revision,
                    "updated_at": utc_now(),
                    "last_error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise

    timeout = _download_wait_timeout() if wait_timeout is None else wait_timeout
    timeout = min(DEFAULT_DOWNLOAD_WAIT_TIMEOUT, max(0.0, float(timeout)))
    started = monotonic()
    deadline = started + timeout
    while True:
        errors = model_download.verify_model_directory(
            spec.target,
            spec.required_files,
        )
        if not errors:
            return {
                "status": "ready",
                "downloaded": True,
                "model_id": spec.model_id,
                "model_path": str(spec.target),
                "revision": spec.revision,
                "worker_pid": worker_pid,
            }
        state = read_json_object(
            runtime_dir / model_download.DOWNLOAD_STATE_NAME
        ) or {}
        worker_running = _download_worker_is_running(runtime_dir)
        if state.get("status") == "error" and not worker_running:
            return {
                "status": "error",
                "downloaded": False,
                "model_id": spec.model_id,
                "model_path": str(spec.target),
                "revision": spec.revision,
                "retryable": False,
                "error": str(state.get("last_error") or "模型下载失败。"),
                "worker_pid": worker_pid,
            }
        now = monotonic()
        if (
            now - started >= max(1.0, poll_interval)
            and not worker_running
            and state.get("status") == "downloading"
            and state.get("last_error")
        ):
            break
        if now >= deadline:
            break
        sleeper(min(max(0.01, poll_interval), max(0.01, deadline - now)))
    return {
        "status": "downloading",
        "downloaded": False,
        "model_id": spec.model_id,
        "model_path": str(spec.target),
        "partial_path": str(spec.target.with_name(spec.target.name + ".partial")),
        "revision": spec.revision,
        "retryable": True,
        "worker_pid": worker_pid,
        "wait_timeout_seconds": timeout,
        "continue_command": "scripts\\run.ps1 --continue",
    }


def prepare_local_model(
    payload: dict[str, Any],
    *,
    runtime_dir: Path = RUNTIME_DIR,
    downloader: Callable[..., Mapping[str, Any]] = model_download.download_model,
    spec_loader: Callable[..., Any] = model_download.load_download_spec,
) -> dict[str, Any]:
    """Use an explicit model, or ensure the configured local VLM is complete."""

    if payload.pop("deterministic_only", False):
        return payload
    if payload.get("openvino_vlm_model"):
        return payload
    spec = spec_loader()
    errors = model_download.verify_model_directory(
        spec.target,
        spec.required_files,
    )
    if errors:
        _save_pending_analysis(payload, runtime_dir=runtime_dir)
        if downloader is model_download.download_model:
            result = coordinate_model_download(
                spec,
                runtime_dir=runtime_dir,
            )
        else:
            result = dict(downloader(spec))
        if result.get("status") == "error":
            raise RuntimeError(str(result.get("error") or "模型下载失败。"))
        if result.get("status") != "ready":
            raise DownloadPending(result)
    payload["openvino_vlm_model"] = str(spec.target)
    return payload


def continue_download(
    *,
    runtime_dir: Path = RUNTIME_DIR,
    downloader: Callable[..., Mapping[str, Any]] = model_download.download_model,
    spec_loader: Callable[..., Any] = model_download.load_download_spec,
) -> tuple[dict[str, Any] | None, dict[str, Any], int]:
    try:
        spec = spec_loader()
        if downloader is model_download.download_model:
            result = coordinate_model_download(
                spec,
                runtime_dir=runtime_dir,
            )
        else:
            result = dict(downloader(spec))
    except Exception as exc:
        response = _local_error(
            "continue",
            code="download_failed",
            message=f"{type(exc).__name__}: {exc}",
            exit_code=EXIT_GENERAL_ERROR,
        )
        return None, response, EXIT_GENERAL_ERROR
    if result.get("status") == "error":
        response = _local_error(
            "continue",
            code="download_failed",
            message=str(result.get("error") or "模型下载失败。"),
            exit_code=EXIT_GENERAL_ERROR,
        )
        response["result"] = result
        return None, response, EXIT_GENERAL_ERROR
    if result.get("status") != "ready":
        response = _local_error(
            "continue",
            status="downloading",
            code="model_downloading",
            message="模型仍在下载，请稍后再次运行 scripts\\run.ps1 --continue。",
            exit_code=EXIT_DOWNLOAD_PENDING,
        )
        response["result"] = result
        return None, response, EXIT_DOWNLOAD_PENDING
    pending = _load_pending_analysis(runtime_dir)
    if pending is not None:
        pending["openvino_vlm_model"] = str(spec.target)
    response = _local_success(
        "continue",
        status="running",
        result={"download": result, "pending_analysis_resumed": pending is not None},
    )
    return pending, response, EXIT_SUCCESS


def _command_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "analyze":
        return {
            "input_dir": str(Path(args.input_dir).expanduser().resolve()),
            "output_dir": (
                str(Path(args.output_dir).expanduser().resolve())
                if args.output_dir
                else None
            ),
            "openvino_model": (
                str(Path(args.openvino_model).expanduser().resolve())
                if args.openvino_model
                else None
            ),
            "openvino_vlm_model": (
                str(Path(args.openvino_vlm_model).expanduser().resolve())
                if args.openvino_vlm_model
                else None
            ),
            "device": args.device,
            "deterministic_only": args.deterministic_only,
        }
    if args.command in {"confirm", "reject"}:
        return {
            "output_dir": str(Path(args.output_dir).expanduser().resolve()),
            "session_id": args.session_id,
            "candidate_id": args.candidate_id,
            "reason": args.reason,
        }
    if args.command == "export":
        return {
            "output_dir": str(Path(args.output_dir).expanduser().resolve()),
            "session_id": args.session_id,
        }
    return {}


def execute_command(
    command: str,
    payload: Mapping[str, Any],
    *,
    connector: Callable[[str, bytes], Any] = connect_pipe,
    starter: Callable[[str], int] | None = None,
    runtime_dir: Path = RUNTIME_DIR,
) -> tuple[dict[str, Any], int]:
    if command == "status":
        try:
            response = _probe(connector=connector)
            return response, int(response["exit_code"])
        except CommunicationError:
            return status_when_disconnected(runtime_dir=runtime_dir)
    if command == "shutdown":
        deadline = time.monotonic() + 5.0
        while True:
            try:
                response = exchange(
                    build_request("shutdown"),
                    connector=connector,
                    timeout=DEFAULT_STATUS_TIMEOUT,
                )
                return response, int(response["exit_code"])
            except CommunicationError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
        record = read_pid_record(runtime_dir)
        if is_server_record_current(record):
            response = _local_error(
                "shutdown",
                status="running",
                code="server_busy",
                message=(
                    "服务进程仍在运行且 Named Pipe 正忙；"
                    "为避免中断当前写操作，本次未强制终止，请稍后重试。"
                ),
                exit_code=EXIT_COMMUNICATION_ERROR,
            )
            return response, EXIT_COMMUNICATION_ERROR
        response = _local_success(
            "shutdown",
            status="stopped",
            result={"message": "本地服务已经停止。"},
        )
        return response, EXIT_SUCCESS

    try:
        ensure_server(
            connector=connector,
            starter=starter,
            runtime_dir=runtime_dir,
        )
        request = build_request(command, payload)
        response = exchange(request, connector=connector)
        return response, int(response["exit_code"])
    except CommunicationError as exc:
        response = _local_error(
            command,
            code="communication_error",
            message=str(exc),
            exit_code=EXIT_COMMUNICATION_ERROR,
        )
        return response, EXIT_COMMUNICATION_ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="scripts\\run.ps1",
        description="本地商品事实核验唯一客户端入口",
    )
    parser.add_argument(
        "--continue",
        dest="continue_download",
        action="store_true",
        help="继续未完成的模型下载和分析请求",
    )
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("status", help="查看本地服务状态")
    commands.add_parser("shutdown", help="安全关闭本地服务")

    analyze = commands.add_parser("analyze", help="分析一个商品资料目录")
    analyze.add_argument("input_dir")
    analyze.add_argument("--output", "--output-dir", dest="output_dir", default=None)
    analyze.add_argument("--openvino-model", default=None)
    analyze.add_argument(
        "--model",
        "--openvino-vlm-model",
        dest="openvino_vlm_model",
        default=None,
    )
    analyze.add_argument(
        "--device",
        default="AUTO",
        help="AUTO 优先选择可用 Intel GPU，否则使用 CPU。",
    )
    analyze.add_argument(
        "--deterministic-only",
        action="store_true",
        help="仅用于不需要图片模型的离线/CI 冒烟测试",
    )

    for operation in ("confirm", "reject"):
        decision = commands.add_parser(operation)
        decision.add_argument("--output-dir", required=True)
        decision.add_argument("--session-id", required=True)
        decision.add_argument("--candidate-id", required=True)
        decision.add_argument("--reason", required=True)

    export = commands.add_parser("export")
    export.add_argument("--output-dir", required=True)
    export.add_argument("--session-id", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    connector: Callable[[str, bytes], Any] = connect_pipe,
    starter: Callable[[str], int] | None = None,
    runtime_dir: Path = RUNTIME_DIR,
) -> int:
    configure_utf8_streams()
    operation = "client"
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
        if args.continue_download:
            if args.command is not None:
                raise CliUsageError("--continue 不能与其他操作同时使用。")
            pending, response, exit_code = continue_download(
                runtime_dir=runtime_dir
            )
            if pending is not None and exit_code == EXIT_SUCCESS:
                response, exit_code = execute_command(
                    "analyze",
                    pending,
                    connector=connector,
                    starter=starter,
                    runtime_dir=runtime_dir,
                )
                if exit_code == EXIT_SUCCESS:
                    _clear_pending_analysis(runtime_dir)
            _print_json(response)
            return exit_code
        if args.command is None:
            raise CliUsageError("必须指定操作或使用 --continue。")
        operation = args.command
        payload = _command_payload(args)
        if operation == "analyze":
            if not Path(str(payload["input_dir"])).is_dir():
                raise CliUsageError(
                    f"商品资料目录不存在：{payload['input_dir']}"
                )
            try:
                payload = prepare_local_model(payload, runtime_dir=runtime_dir)
            except DownloadPending as exc:
                response = _local_error(
                    "analyze",
                    status="downloading",
                    code="model_downloading",
                    message=(
                        "模型仍在下载，请稍后运行 "
                        "scripts\\run.ps1 --continue。"
                    ),
                    exit_code=EXIT_DOWNLOAD_PENDING,
                )
                response["result"] = exc.result
                _print_json(response)
                return EXIT_DOWNLOAD_PENDING
        response, exit_code = execute_command(
            operation,
            payload,
            connector=connector,
            starter=starter,
            runtime_dir=runtime_dir,
        )
        if operation == "analyze" and exit_code == EXIT_SUCCESS:
            _clear_pending_analysis(runtime_dir)
        _print_json(response)
        return exit_code
    except CliUsageError as exc:
        response = _local_error(
            operation,
            code="invalid_arguments",
            message=str(exc),
            exit_code=EXIT_GENERAL_ERROR,
        )
        _print_json(response)
        return EXIT_GENERAL_ERROR
    except Exception as exc:
        response = _local_error(
            operation,
            code="client_error",
            message=f"{type(exc).__name__}: {exc}",
            exit_code=EXIT_GENERAL_ERROR,
        )
        _print_json(response)
        return EXIT_GENERAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
