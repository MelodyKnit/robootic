"""Read-only NVIDIA and Torch CUDA preflight for the Python 3.7 pose runtime."""

import importlib
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional, Tuple


CUDA_117_MINIMUM_WINDOWS_DRIVER = (516, 1)
"""NVIDIA's minimum Windows driver family for CUDA 11.7 runtime compatibility."""


@dataclass(frozen=True)
class GpuPreflightResult:
    """Structured compatibility evidence safe to print in CLI or expose to developer tooling."""

    gpu_name: Optional[str]
    driver_version: Optional[str]
    compute_capability: Optional[str]
    cuda_driver_compatible: bool
    torch_installed: bool
    torch_version: Optional[str]
    torch_cuda_version: Optional[str]
    torch_cuda_available: bool
    torch_device_name: Optional[str]
    ready_for_pose_inference: bool
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        """Return JSON-serializable fields without exposing a Torch module object."""

        return asdict(self)


def inspect_cuda_gpu(
    command_runner: Callable[..., Any] = subprocess.run,
    torch_module: Optional[Any] = None,
) -> GpuPreflightResult:
    """Inspect CUDA readiness without allocating a model, downloading data, or opening hardware.

    A compatible NVIDIA driver permits dependency installation even before Torch exists. A
    fully ready result additionally requires the pinned CUDA Torch build and an available
    CUDA device, ensuring normal pose inference cannot silently select CPU.
    """

    gpu_name, driver_version, compute_capability = _read_nvidia_smi(command_runner)
    driver_compatible = _driver_supports_cuda_117(driver_version)
    if torch_module is None:
        try:
            torch_module = importlib.import_module("torch")
        except ImportError:
            torch_module = None
    if torch_module is None:
        return GpuPreflightResult(
            gpu_name,
            driver_version,
            compute_capability,
            driver_compatible,
            False,
            None,
            None,
            False,
            None,
            False,
            "CUDA driver is compatible, but the pinned CUDA Torch runtime is not installed."
            if driver_compatible
            else "No NVIDIA driver compatible with CUDA 11.7 was detected.",
        )
    torch_version = str(getattr(torch_module, "__version__", "")) or None
    torch_cuda_version = getattr(getattr(torch_module, "version", None), "cuda", None)
    cuda_available = bool(getattr(torch_module, "cuda").is_available())
    device_name = None
    if cuda_available:
        try:
            device_name = str(torch_module.cuda.get_device_name(0))
        except Exception:
            device_name = None
    torch_compatible = torch_cuda_version == "11.7" and cuda_available
    ready = driver_compatible and torch_compatible
    if ready:
        reason = "CUDA 11.7 Torch runtime and NVIDIA GPU are ready for pose inference."
    elif not driver_compatible:
        reason = "No NVIDIA driver compatible with CUDA 11.7 was detected."
    elif torch_cuda_version != "11.7":
        reason = "Torch must be installed with the pinned CUDA 11.7 build."
    else:
        reason = "Torch cannot access CUDA; pose inference will remain disabled."
    return GpuPreflightResult(
        gpu_name,
        driver_version,
        compute_capability,
        driver_compatible,
        True,
        torch_version,
        torch_cuda_version,
        cuda_available,
        device_name,
        ready,
        reason,
    )


def _read_nvidia_smi(command_runner: Callable[..., Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Read only one GPU row from NVIDIA's management tool without raising on absent drivers."""

    try:
        result = command_runner(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,compute_cap",
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None, None
    if getattr(result, "returncode", 1) != 0:
        return None, None, None
    first_line = str(getattr(result, "stdout", "")).splitlines()
    if not first_line:
        return None, None, None
    values = [value.strip() for value in first_line[0].split(",")]
    if len(values) != 3:
        return None, None, None
    return values[0] or None, values[1] or None, values[2] or None


def _driver_supports_cuda_117(driver_version: Optional[str]) -> bool:
    """Compare a Windows NVIDIA driver version conservatively against CUDA 11.7's minimum."""

    if not driver_version:
        return False
    try:
        components = tuple(int(value) for value in driver_version.split("."))
    except ValueError:
        return False
    if len(components) < 2:
        return False
    return components[:2] >= CUDA_117_MINIMUM_WINDOWS_DRIVER
