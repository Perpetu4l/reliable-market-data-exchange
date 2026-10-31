"""Small Python implementation of the packed order-entry protocol.

Used by the GUI manual-testing tab. Opens a real TCP socket to the C++
exchange; never calls the order book or matching engine in-process.
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass
from typing import Final


HEADER: Final = struct.Struct("<QIHQI")
NEW_ORDER: Final = struct.Struct("<QIHQIQQQBBB8s5s")
CLIENT_HELLO: Final = struct.Struct("<QIHQIQ24s6s")
EXECUTION_REPORT: Final = struct.Struct("<QIHQIQQQQQQB7s")

NEW_ORDER_TYPE = 1
ORDER_ACK_TYPE = 4
ORDER_REJECT_TYPE = 5
EXECUTION_REPORT_TYPE = 6
CLIENT_HELLO_TYPE = 7
SERVER_WELCOME_TYPE = 8

BUY = 1
SELL = 2
LIMIT = 2
DAY = 1


@dataclass(frozen=True)
class ServerMessage:
    message_type: int
    sequence_number: int
    timestamp_ns: int
    payload: bytes


def _fixed_text(text: str, size: int) -> bytes:
    return text.encode("ascii", errors="ignore")[:size].ljust(size, b"\0")


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ConnectionError("The exchange closed the connection")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def receive_message(connection: socket.socket) -> ServerMessage:
    header = _recv_exact(connection, HEADER.size)
    sequence_number, message_length, message_type, timestamp_ns, _instrument_id = HEADER.unpack(header)
    if message_length < HEADER.size or message_length > 256:
        raise ValueError(f"Invalid order-entry frame length: {message_length}")
    return ServerMessage(
        message_type=message_type,
        sequence_number=sequence_number,
        timestamp_ns=timestamp_ns,
        payload=header + _recv_exact(connection, message_length - HEADER.size),
    )


def submit_limit_order(
    host: str,
    port: int,
    client_id: int,
    client_order_id: int,
    symbol: str,
    side: int,
    price_cents: int,
    quantity: int,
    timeout_seconds: float = 3.0,
) -> tuple[ServerMessage, float, list[dict[str, int]]]:
    """Handshake and submit one order through the real C++ TCP server.

    Also attempts a brief non-blocking read after ORDER_ACK to capture any
    immediate EXECUTION_REPORT frames generated when the order crosses.
    """
    with socket.create_connection((host, port), timeout=timeout_seconds) as connection:
        connection.settimeout(timeout_seconds)
        hello = CLIENT_HELLO.pack(
            1, CLIENT_HELLO.size, CLIENT_HELLO_TYPE, time.time_ns(), 0,
            client_id, _fixed_text(f"gui-{client_id}", 24), b"\0" * 6,
        )
        connection.sendall(hello)
        welcome = receive_message(connection)
        if welcome.message_type != SERVER_WELCOME_TYPE:
            raise ConnectionError("The exchange did not accept the client handshake")

        request = NEW_ORDER.pack(
            2, NEW_ORDER.size, NEW_ORDER_TYPE, time.time_ns(), 1,
            client_order_id, price_cents, quantity, side, LIMIT, DAY,
            _fixed_text(symbol.upper(), 8), b"\0" * 5,
        )
        started = time.perf_counter_ns()
        connection.sendall(request)
        primary_response: ServerMessage | None = None
        rtt_ms = 0.0
        executions: list[dict[str, int]] = []
        while primary_response is None:
            response = receive_message(connection)
            if response.message_type in (ORDER_ACK_TYPE, ORDER_REJECT_TYPE):
                primary_response = response
                rtt_ms = (time.perf_counter_ns() - started) / 1_000_000.0
            elif response.message_type == EXECUTION_REPORT_TYPE and len(response.payload) == EXECUTION_REPORT.size:
                unpacked = EXECUTION_REPORT.unpack(response.payload)
                executions.append({
                    "execution_price_cents": unpacked[8],
                    "execution_quantity": unpacked[9],
                    "leaves_quantity": unpacked[10],
                })

        # Briefly check if an execution report immediately followed ORDER_ACK
        if primary_response.message_type == ORDER_ACK_TYPE:
            connection.settimeout(0.05)
            try:
                while True:
                    extra = receive_message(connection)
                    if extra.message_type == EXECUTION_REPORT_TYPE and len(extra.payload) == EXECUTION_REPORT.size:
                        unpacked = EXECUTION_REPORT.unpack(extra.payload)
                        executions.append({
                            "execution_price_cents": unpacked[8],
                            "execution_quantity": unpacked[9],
                            "leaves_quantity": unpacked[10],
                        })
                    else:
                        break
            except (socket.timeout, TimeoutError, ConnectionError, OSError):
                pass
        return primary_response, rtt_ms, executions
