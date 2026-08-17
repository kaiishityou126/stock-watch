"""errors 分型异常单测:确保子类仍是 RuntimeError(不破坏现有 except)。"""
from __future__ import annotations

import unittest

from common.errors import AuthError, RateLimitError


class TestErrors(unittest.TestCase):
    def test_auth_is_runtime_error(self):
        self.assertTrue(issubclass(AuthError, RuntimeError))

    def test_ratelimit_is_runtime_error(self):
        self.assertTrue(issubclass(RateLimitError, RuntimeError))

    def test_distinct_types(self):
        self.assertNotEqual(AuthError, RateLimitError)


if __name__ == "__main__":
    unittest.main()
