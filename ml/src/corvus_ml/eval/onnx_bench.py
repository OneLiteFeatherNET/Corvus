"""ONNX Runtime CPU benchmark, parity check and int8 dynamic quantisation.

Imports only numpy / onnxruntime so the isolated benchmark process starts fast and its peak RSS
reflects the one model under test.
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort


def make_session(path: Path | str, threads: int) -> ort.InferenceSession:
    """CPU session with ``threads`` intra-op threads, one inter-op thread, sequential mode."""
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])


def run_session(sess: ort.InferenceSession, x: np.ndarray) -> list[np.ndarray]:
    return sess.run(None, {sess.get_inputs()[0].name: x})


def _peak_rss_mb() -> float:
    """Peak resident set size of this process in MiB.

    ``ru_maxrss`` survives ``exec`` and would carry the parent's peak into the benchmark child,
    so on Linux the per-address-space high-water mark ``VmHWM`` is preferred.
    """
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # KiB on Linux


@dataclass
class BenchResult:
    threads: int
    n_windows_timed: int
    latency_p50_ms: float
    latency_p99_ms: float
    latency_mean_ms: float
    batch_size: int
    batch_throughput_wps: float  # windows per second at the batch size
    peak_rss_mb: float  # peak RSS of the benchmark process
    model_rss_delta_mb: float  # peak RSS growth caused by loading and running the model
    file_size_kb: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def benchmark(
    path: Path | str,
    x: np.ndarray,
    threads: int = 1,
    warmup: int = 20,
    iters: int = 200,
    batch: int = 64,
    batch_iters: int = 10,
) -> BenchResult:
    """Time single-window and batch inference of the ONNX model at ``path``.

    ``x`` holds example inputs (at least one row; rows are cycled). The model is loaded inside
    this call; peak RSS is process-wide, so use :func:`benchmark_isolated` for a per-model value.
    """
    base = _peak_rss_mb()
    sess = make_session(path, threads)
    xs = np.ascontiguousarray(x, dtype=np.float32)
    n = xs.shape[0]
    for i in range(warmup):
        run_session(sess, xs[i % n : i % n + 1])
    lat = np.empty(iters)
    for i in range(iters):
        row = xs[i % n : i % n + 1]
        t0 = time.perf_counter_ns()
        run_session(sess, row)
        lat[i] = (time.perf_counter_ns() - t0) / 1e6
    reps = np.arange(batch) % n
    big = xs[reps]
    run_session(sess, big)
    t0 = time.perf_counter_ns()
    for _ in range(batch_iters):
        run_session(sess, big)
    elapsed = (time.perf_counter_ns() - t0) / 1e9
    peak = _peak_rss_mb()
    return BenchResult(
        threads=threads,
        n_windows_timed=iters,
        latency_p50_ms=float(np.percentile(lat, 50)),
        latency_p99_ms=float(np.percentile(lat, 99)),
        latency_mean_ms=float(lat.mean()),
        batch_size=batch,
        batch_throughput_wps=batch * batch_iters / elapsed if elapsed > 0 else float("inf"),
        peak_rss_mb=peak,
        model_rss_delta_mb=max(0.0, peak - base),
        file_size_kb=Path(path).stat().st_size / 1024.0,
    )


def benchmark_isolated(path: Path | str, x: np.ndarray, threads: int = 1, **kwargs) -> BenchResult:
    """Run :func:`benchmark` in a fresh interpreter so peak RSS belongs to this model only.

    The child imports only numpy and onnxruntime (``python -I -m corvus_ml.eval.onnx_bench``),
    not torch or sklearn, so the baseline RSS is that of a plain inference process.
    """
    opts = {"warmup": 20, "iters": 200, "batch": 64, "batch_iters": 10} | kwargs
    with tempfile.TemporaryDirectory() as tmp:
        npy = Path(tmp) / "x.npy"
        np.save(npy, np.ascontiguousarray(x, dtype=np.float32))
        cmd = [
            sys.executable,
            "-I",
            "-m",
            "corvus_ml.eval.onnx_bench",
            str(path),
            str(npy),
            str(threads),
            json.dumps(opts),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"benchmark process failed: {proc.stderr[-500:]}")
    return BenchResult(**json.loads(proc.stdout.strip().splitlines()[-1]))


def _main(argv: list[str]) -> int:
    path, npy, threads, opts = argv
    res = benchmark(path, np.load(npy), int(threads), **json.loads(opts))
    print(json.dumps(res.to_dict()))
    return 0


def quantize_int8(src: Path, dst: Path) -> Path:
    """Dynamic int8 weight quantisation (onnxruntime.quantization)."""
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(str(src), str(dst), weight_type=QuantType.QInt8)
    return dst


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
