from __future__ import annotations

import asyncio
import gc
import logging
import os
import site
import subprocess
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Generator, Optional

logger = logging.getLogger("narezki.cuda")

_CUDA_DLL_DIRS_ADDED: set[str] = set()
_DLL_DIR_HANDLES: list[Any] = []


def setup_cuda_dlls() -> list[str]:
    """Register NVIDIA CUDA/cuDNN wheel DLL folders on Windows before loading CTranslate2/Torch."""
    if sys.platform != "win32":
        return []

    added: list[str] = []
    candidate_roots: list[Path] = []

    for sp in site.getsitepackages():
        candidate_roots.append(Path(sp))
    candidate_roots.append(Path(sys.prefix) / "Lib" / "site-packages")

    dll_dirs: list[Path] = []
    for root in candidate_roots:
        if not root.exists():
            continue
        nvidia_dir = root / "nvidia"
        if nvidia_dir.exists():
            for bin_dir in nvidia_dir.glob("*/bin"):
                if bin_dir.is_dir():
                    dll_dirs.append(bin_dir)
            for lib_dir in nvidia_dir.glob("*/lib"):
                if lib_dir.is_dir():
                    dll_dirs.append(lib_dir)
        torch_lib = root / "torch" / "lib"
        if torch_lib.is_dir():
            dll_dirs.append(torch_lib)
        ct2_dir = root / "ctranslate2"
        if ct2_dir.is_dir():
            dll_dirs.append(ct2_dir)

    current_path = os.environ.get("PATH", "")
    path_parts = [p for p in current_path.split(os.pathsep) if p]

    for d in dll_dirs:
        resolved = str(d.resolve())
        if resolved in _CUDA_DLL_DIRS_ADDED:
            continue
        try:
            if hasattr(os, "add_dll_directory"):
                handle = os.add_dll_directory(resolved)
                _DLL_DIR_HANDLES.append(handle)
            if resolved not in path_parts:
                path_parts.insert(0, resolved)
            _CUDA_DLL_DIRS_ADDED.add(resolved)
            added.append(resolved)
        except OSError as exc:
            logger.warning("Could not register DLL directory %s: %s", resolved, exc)

    os.environ["PATH"] = os.pathsep.join(path_parts)
    if added:
        logger.info("Registered %d CUDA/Torch DLL directories", len(added))
    return added


def _query_nvidia_smi() -> Optional[dict[str, Any]]:
    """Query live device-wide VRAM usage via nvidia-smi."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            **kwargs,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        first_line = proc.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first_line.split(",")]
        if len(parts) < 5:
            return None
        return {
            "device_name": parts[0],
            "vram_total_mb": int(float(parts[1])),
            "vram_used_mb": int(float(parts[2])),
            "vram_free_mb": int(float(parts[3])),
            "driver_version": parts[4],
        }
    except Exception:
        return None


def get_gpu_info() -> dict[str, Any]:
    """Return current CUDA availability and live VRAM telemetry in MB."""
    setup_cuda_dlls()
    info: dict[str, Any] = {
        "cuda_available": False,
        "device_name": "CPU (No CUDA GPU)",
        "vram_total_mb": 0,
        "vram_used_mb": 0,
        "vram_free_mb": 0,
        "torch_allocated_mb": 0,
        "torch_reserved_mb": 0,
        "driver_version": None,
    }

    smi = _query_nvidia_smi()
    if smi:
        info.update(smi)

    try:
        import torch

        if torch.cuda.is_available():
            info["cuda_available"] = True
            idx = torch.cuda.current_device()
            props = torch.cuda.get_device_properties(idx)
            free_bytes, total_bytes = torch.cuda.mem_get_info(idx)
            total_mb = int(total_bytes // (1024 * 1024))
            free_mb = int(free_bytes // (1024 * 1024))
            used_mb = max(0, total_mb - free_mb)

            if not smi:
                info["device_name"] = props.name
                info["vram_total_mb"] = total_mb
                info["vram_free_mb"] = free_mb
                info["vram_used_mb"] = used_mb
            elif not info.get("device_name"):
                info["device_name"] = props.name

            info["torch_allocated_mb"] = int(torch.cuda.memory_allocated(idx) // (1024 * 1024))
            info["torch_reserved_mb"] = int(torch.cuda.memory_reserved(idx) // (1024 * 1024))
    except Exception as exc:
        logger.debug("torch CUDA query failed: %s", exc)

    return info


def release_vram(stage: str = "cleanup") -> dict[str, Any]:
    """Force garbage collection and release PyTorch CUDA cached/IPC memory."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            try:
                torch.cuda.synchronize()
            except Exception:
                pass
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception as exc:
        logger.debug("release_vram(%s) encountered error: %s", stage, exc)
    gc.collect()
    info = get_gpu_info()
    logger.info(
        "VRAM released after [%s]: %s/%s MB used",
        stage,
        info["vram_used_mb"],
        info["vram_total_mb"],
    )
    return info


async def get_gpu_info_async() -> dict[str, Any]:
    """Non-blocking async wrapper for get_gpu_info()."""
    return await asyncio.to_thread(get_gpu_info)


async def release_vram_async(stage: str = "cleanup") -> dict[str, Any]:
    """Non-blocking async wrapper for release_vram()."""
    return await asyncio.to_thread(release_vram, stage)


@contextmanager
def sync_vram_guard(stage_name: str) -> Generator[None, None, None]:
    """Synchronous VRAM guard ensuring cleanup on exit."""
    setup_cuda_dlls()
    before = get_gpu_info()
    logger.info(
        "Entering GPU stage [%s] (VRAM used: %s/%s MB)",
        stage_name,
        before["vram_used_mb"],
        before["vram_total_mb"],
    )
    try:
        yield
    finally:
        release_vram(stage_name)


@asynccontextmanager
async def vram_guard(
    stage_name: str,
    on_gpu_status: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
):
    """Async context manager that cleans up GPU memory and optionally broadcasts telemetry."""
    setup_cuda_dlls()
    before = await get_gpu_info_async()
    logger.info(
        "Entering GPU stage [%s] (VRAM used: %s/%s MB)",
        stage_name,
        before["vram_used_mb"],
        before["vram_total_mb"],
    )
    if on_gpu_status is not None:
        await on_gpu_status(before)
    try:
        yield
    finally:
        after = await release_vram_async(stage_name)
        if on_gpu_status is not None:
            await on_gpu_status(after)
