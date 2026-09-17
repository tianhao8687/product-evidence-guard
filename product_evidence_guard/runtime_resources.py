"""Small, best-effort resource guard for optional local model loading.

Available Windows commit matters as well as physical RAM. This is a safety
estimate, not a promise that native inference cannot run out of memory.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

GIB = 1024 ** 3


def available_memory() -> dict[str, int]:
    if os.name != "nt":
        return {}  # Do not substitute total memory for available memory.

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in (
                "total_physical", "available_physical", "total_commit", "available_commit",
                "total_virtual", "available_virtual", "extended_virtual")]

    state = MemoryStatus()
    state.length = ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        return {}
    return {"physical": state.available_physical, "commit": state.available_commit}


def check_model_memory(model_path: str | Path) -> dict:
    weights = sum(p.stat().st_size for p in Path(model_path).glob("*.bin"))
    # Include compilation/inference headroom and leave room for the user's apps.
    required = max(4 * GIB, int(weights * 1.5) + 3 * GIB)
    available = available_memory()
    if any(available.get(key, required) < required for key in ("physical", "commit")):
        raise MemoryError(
            "本地 AI 暂缓加载：可用内存/提交空间不足 "
            f"(可用 {min(available.values()) / GIB:.1f} GB，预计需 {required / GIB:.1f} GB)。"
            "请在空闲时释放内存后重试；不会关闭其他软件。")
    return {"available_bytes": available, "estimated_required_bytes": required}


def cpu_pipeline_options(device: str) -> dict:
    return {"INFERENCE_NUM_THREADS": min(4, os.cpu_count() or 1), "NUM_STREAMS": 1} if device.split(".")[0].upper() == "CPU" else {}


def lower_worker_priority() -> None:
    """Only change the priority of the calling, dedicated inference process."""
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)
