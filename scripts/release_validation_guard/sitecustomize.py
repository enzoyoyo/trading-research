"""Fail-closed network guard for candidate-local release validation children."""
from __future__ import annotations

import socket

_MESSAGE = "network disabled by trading-research release validation"


def _blocked(*_args, **_kwargs):
    raise OSError(_MESSAGE)


socket.create_connection = _blocked
socket.getaddrinfo = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
