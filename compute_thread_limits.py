"""Cap BLAS/OpenMP threads before numpy/sklearn load.

AutoCue stem FFT and ML ingest otherwise spawn one OpenMP worker per core
inside Music Sorter 8787, hold the GIL, and freeze Set Overview.
"""

from __future__ import annotations

import os
from typing import Mapping, MutableMapping

THREAD_LIMIT_ENV: dict[str, str] = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}


def apply_compute_thread_limits() -> None:
    """Set one-thread caps on the current process unless already set."""
    for key, value in THREAD_LIMIT_ENV.items():
        os.environ.setdefault(key, value)


def env_with_compute_thread_limits(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy env and apply caps without overriding an explicit value."""
    env: MutableMapping[str, str] = dict(os.environ if base is None else base)
    for key, value in THREAD_LIMIT_ENV.items():
        env.setdefault(key, value)
    env["PYTHONUNBUFFERED"] = "1"
    return dict(env)
