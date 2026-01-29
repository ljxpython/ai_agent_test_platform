# pyright: reportMissingImports=false

import importlib
import os
import sys

import pytest


# Allow importing backend code when tests are invoked directly.
_BACKEND_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend", "src"))  # noqa: E402
if _BACKEND_SRC not in sys.path:
    sys.path.insert(0, _BACKEND_SRC)  # noqa: E402


def _router_module():
    # Import after sys.path adjustment so `omo_platform` resolves when pytest is
    # invoked without PYTHONPATH.
    return importlib.import_module("omo_platform.langgraph_proxy.router")


_ROUTER = _router_module()
_extract_thread_id_from_content_location = _ROUTER._extract_thread_id_from_content_location
_extract_thread_id_from_request_path = _ROUTER._extract_thread_id_from_request_path


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/threads/t_123/runs/r_456", "t_123"),
        ("/api/lg/threads/t_123/runs/r_456", "t_123"),
        # Extra leading segments should be tolerated.
        ("/foo/bar/threads/t_123/runs/r_456", "t_123"),
        # Full URL should be tolerated.
        ("https://example.test/api/lg/threads/t_123/runs/r_456?x=1#frag", "t_123"),
        ("", None),
        ("/threads//runs/r_456", None),
        ("/threads/t_123/runs/", None),
        ("/runs/r_456", None),
    ],
)
def test_extract_thread_id_from_content_location(value: str, expected: str | None) -> None:
    assert _extract_thread_id_from_content_location(value) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("threads/t_123/runs/stream", "t_123"),
        ("/threads/t_123/runs/stream", "t_123"),
        ("/api/lg/threads/t_123/runs/stream", "t_123"),
        # Extra leading segments should be tolerated.
        ("/foo/bar/threads/t_123/runs/stream", "t_123"),
        # Full URL should be tolerated.
        ("https://example.test/api/lg/threads/t_123/runs/stream?x=1#frag", "t_123"),
        ("", None),
        ("threads//runs/stream", None),
        ("threads/t_123/runs/", None),
        ("runs/stream", None),
    ],
)
def test_extract_thread_id_from_request_path(path: str, expected: str | None) -> None:
    assert _extract_thread_id_from_request_path(path) == expected
