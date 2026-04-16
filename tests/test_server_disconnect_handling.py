from __future__ import annotations

import ssl

from server import _is_client_disconnect_error


def test_is_client_disconnect_error_true_for_common_disconnect_exceptions():
    assert _is_client_disconnect_error(BrokenPipeError())
    assert _is_client_disconnect_error(ConnectionResetError())
    assert _is_client_disconnect_error(OSError(32, "broken pipe"))
    assert _is_client_disconnect_error(OSError(104, "connection reset by peer"))
    assert _is_client_disconnect_error(ssl.SSLError("EOF occurred in violation of protocol"))


def test_is_client_disconnect_error_false_for_unrelated_exceptions():
    assert not _is_client_disconnect_error(ValueError("bad value"))
    assert not _is_client_disconnect_error(RuntimeError("unexpected"))

