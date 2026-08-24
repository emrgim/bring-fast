"""Live cart/status must not hold the MCP request until Chrome finishes."""

import time

import pytest

from bring_fast import checkout


def test_in_thread_raises_before_a_slow_worker_finishes():
    def slow():
        time.sleep(8)
        return "done"

    started = time.monotonic()
    with pytest.raises(checkout.LiveCartTimeout, match="exceeded 0s"):
        checkout._in_thread(slow, timeout=0.4)
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
