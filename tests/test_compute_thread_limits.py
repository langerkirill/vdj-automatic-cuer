"""OpenMP/BLAS thread caps so AutoCue cannot saturate the UI process."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from compute_thread_limits import (
    THREAD_LIMIT_ENV,
    apply_compute_thread_limits,
    env_with_compute_thread_limits,
)


class ComputeThreadLimitsTests(unittest.TestCase):
    def test_env_helper_sets_one_thread_and_unbuffered(self) -> None:
        env = env_with_compute_thread_limits({"PATH": "/usr/bin"})
        self.assertEqual(env["PATH"], "/usr/bin")
        for key, value in THREAD_LIMIT_ENV.items():
            self.assertEqual(env[key], value)
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_env_helper_does_not_override_explicit_caps(self) -> None:
        env = env_with_compute_thread_limits({"OMP_NUM_THREADS": "2"})
        self.assertEqual(env["OMP_NUM_THREADS"], "2")
        self.assertEqual(env["OPENBLAS_NUM_THREADS"], "1")

    def test_apply_uses_setdefault(self) -> None:
        with patch.dict(os.environ, {"OMP_NUM_THREADS": "8"}, clear=False):
            apply_compute_thread_limits()
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "8")
            self.assertEqual(os.environ["OPENBLAS_NUM_THREADS"], "1")


if __name__ == "__main__":
    unittest.main()
