"""Strict standard-library TCP client for the documented PGI gateway frame."""

import socket
import threading
from typing import Any, Callable, Optional


FRAME_LENGTH = 14
FRAME_HEADER = b"\xff\xfe\xfd\xfc"
FRAME_FOOTER = 0xFB
READ_OPERATION = 0x00
WRITE_OPERATION = 0x01

INITIALIZATION_REGISTER = 0x0802
TARGET_FORCE_REGISTER = 0x0502
TARGET_POSITION_REGISTER = 0x0602
GRIP_STATE_REGISTER = 0x0F01


class PgiTcpClientError(RuntimeError):
    """Report a normalized PGI transport or protocol failure."""


class PgiTcpTransportError(PgiTcpClientError):
    """Report a connection, send, receive, or timeout failure."""


class PgiTcpWriteOutcomeUnknownError(PgiTcpTransportError):
    """Report a write whose device-side result cannot be confirmed safely."""


class PgiTcpProtocolError(PgiTcpClientError):
    """Report a malformed or mismatched PGI gateway response."""


SocketFactory = Callable[[int, int], Any]


class PgiTcpClient:
    """Exchange validated 14-byte register frames with one PGI TCP gateway.

    The public operations intentionally expose only registers demonstrated by the
    project-local vendor sample. A re-entrant lock serializes connect, close, and
    request/response pairs so concurrent callers cannot consume each other's frames.
    """

    def __init__(
        self,
        host: str,
        port: int = 8888,
        device_id: int = 1,
        timeout_seconds: float = 1.0,
        retry_count: int = 2,
        socket_factory: Optional[SocketFactory] = None,
    ) -> None:
        """Store one endpoint without opening a network connection."""

        if not isinstance(host, str) or not host.strip():
            raise ValueError("PGI host must be a non-empty string.")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("PGI port must be an integer between 1 and 65535.")
        if type(device_id) is not int or not 0 <= device_id <= 255:
            raise ValueError("PGI device_id must be an integer between 0 and 255.")
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("PGI timeout_seconds must be greater than zero.")
        if type(retry_count) is not int or retry_count < 0:
            raise ValueError("PGI retry_count must be a non-negative integer.")

        self.host = host.strip()
        self.port = port
        self.device_id = device_id
        self.timeout_seconds = float(timeout_seconds)
        self.retry_count = retry_count
        self._socket_factory = socket_factory or socket.socket
        self._socket = None  # type: Optional[Any]
        self._lock = threading.RLock()

    @property
    def connected(self) -> bool:
        """Return whether this client currently owns an open socket instance."""

        with self._lock:
            return self._socket is not None

    def connect(self) -> None:
        """Open the configured TCP endpoint with bounded retries and no register writes."""

        with self._lock:
            self._close_locked()
            self._connect_with_retries_locked()

    def reconnect(self) -> None:
        """Replace the current socket with a new validated TCP connection."""

        self.connect()

    def close(self) -> None:
        """Close the owned socket and make repeated cleanup calls harmless."""

        with self._lock:
            self._close_locked()

    def read_register(self, register: int) -> int:
        """Read one demonstrated gateway register and return its unsigned value."""

        request = self.build_frame(self.device_id, register, READ_OPERATION, 0)
        response = self._exchange(request, register, READ_OPERATION)
        return response[9] | (response[10] << 8)

    def write_register(self, register: int, value: int) -> None:
        """Write one demonstrated gateway register after validating its unsigned value."""

        request = self.build_frame(self.device_id, register, WRITE_OPERATION, value)
        self._exchange(request, register, WRITE_OPERATION)

    def request_initialization(self) -> None:
        """Request the ordinary vendor-sample initialization, never full recalibration."""

        self.write_register(INITIALIZATION_REGISTER, 0x00)

    def set_target_force(self, force_percent: int) -> None:
        """Set target force within the PGI documented 20 through 100 percent range."""

        if type(force_percent) is not int or not 20 <= force_percent <= 100:
            raise ValueError("PGI target force must be an integer between 20 and 100 percent.")
        self.write_register(TARGET_FORCE_REGISTER, force_percent)

    def set_target_position(self, position: int) -> None:
        """Set target position within the normalized PGI 0 through 1000 range."""

        if type(position) is not int or not 0 <= position <= 1000:
            raise ValueError("PGI target position must be an integer between 0 and 1000.")
        self.write_register(TARGET_POSITION_REGISTER, position)

    def get_initialization_state(self) -> int:
        """Read the vendor initialization-state register without changing the device."""

        return self.read_register(INITIALIZATION_REGISTER)

    def get_target_position(self) -> int:
        """Read the configured target-position register demonstrated by the TCP sample."""

        return self.read_register(TARGET_POSITION_REGISTER)

    def get_grip_state(self) -> int:
        """Read the vendor grip-state register without changing the device."""

        return self.read_register(GRIP_STATE_REGISTER)

    @staticmethod
    def build_frame(device_id: int, register: int, operation: int, value: int) -> bytes:
        """Build one exact vendor gateway frame with explicit byte-order rules."""

        if type(device_id) is not int or not 0 <= device_id <= 255:
            raise ValueError("PGI device_id must be an integer between 0 and 255.")
        if type(register) is not int or not 0 <= register <= 0xFFFF:
            raise ValueError("PGI register must be an unsigned 16-bit integer.")
        if operation not in (READ_OPERATION, WRITE_OPERATION):
            raise ValueError("PGI operation must be read or write.")
        if type(value) is not int or not 0 <= value <= 0xFFFF:
            raise ValueError("PGI register value must be an unsigned 16-bit integer.")

        return bytes(
            (
                0xFF,
                0xFE,
                0xFD,
                0xFC,
                device_id,
                (register >> 8) & 0xFF,
                register & 0xFF,
                operation,
                0x00,
                value & 0xFF,
                (value >> 8) & 0xFF,
                0x00,
                0x00,
                FRAME_FOOTER,
            )
        )

    def _exchange(self, request: bytes, register: int, operation: int) -> bytes:
        """Exchange one frame while never replaying a possibly delivered write."""

        with self._lock:
            if self._socket is None:
                raise PgiTcpTransportError("PGI TCP client is not connected.")

            last_error = None  # type: Optional[BaseException]
            for attempt in range(self.retry_count + 1):
                try:
                    if self._socket is None:
                        self._connect_once_locked()
                except OSError as error:
                    last_error = error
                    self._close_locked()
                    if attempt < self.retry_count:
                        continue
                    break

                try:
                    self._socket.sendall(request)
                except OSError as error:
                    self._close_locked()
                    if operation == WRITE_OPERATION:
                        raise PgiTcpWriteOutcomeUnknownError(
                            "PGI write outcome is unknown and was not retried. "
                            "Reconnect and inspect the gripper before issuing another command."
                        ) from error
                    last_error = error
                    if attempt < self.retry_count:
                        continue
                    break

                try:
                    response = self._receive_exact_locked(FRAME_LENGTH)
                    self._validate_response(response, register, operation)
                    return response
                except (OSError, PgiTcpClientError) as error:
                    self._close_locked()
                    if operation == WRITE_OPERATION:
                        raise PgiTcpWriteOutcomeUnknownError(
                            "PGI write outcome is unknown and was not retried. "
                            "Reconnect and inspect the gripper before issuing another command."
                        ) from error
                    last_error = error
                    if attempt < self.retry_count:
                        continue
                    break

            error_type = (
                PgiTcpProtocolError
                if isinstance(last_error, PgiTcpProtocolError)
                else PgiTcpTransportError
            )
            raise error_type(
                "PGI TCP exchange failed after {0} attempt(s): {1}".format(
                    self.retry_count + 1, last_error
                )
            ) from last_error

    def _connect_with_retries_locked(self) -> None:
        """Open a socket using the configured retry budget."""

        last_error = None  # type: Optional[BaseException]
        for attempt in range(self.retry_count + 1):
            try:
                self._connect_once_locked()
                return
            except OSError as error:
                last_error = error
                self._close_locked()
                if attempt < self.retry_count:
                    continue
        raise PgiTcpTransportError(
            "Unable to connect to the PGI TCP endpoint after {0} attempt(s): {1}".format(
                self.retry_count + 1, last_error
            )
        ) from last_error

    def _connect_once_locked(self) -> None:
        """Create and connect one socket, retaining it only after success."""

        candidate = self._socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        try:
            candidate.settimeout(self.timeout_seconds)
            candidate.connect((self.host, self.port))
        except Exception:
            try:
                candidate.close()
            finally:
                raise
        self._socket = candidate

    def _receive_exact_locked(self, length: int) -> bytes:
        """Receive exactly one frame or fail on timeout, EOF, or truncation."""

        chunks = []
        remaining = length
        while remaining:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise PgiTcpTransportError(
                    "PGI TCP connection closed before a complete response was received."
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _validate_response(self, response: bytes, register: int, operation: int) -> None:
        """Reject responses that do not match the request correlation fields."""

        if len(response) != FRAME_LENGTH:
            raise PgiTcpProtocolError("PGI TCP response must contain exactly 14 bytes.")
        if response[:4] != FRAME_HEADER:
            raise PgiTcpProtocolError("PGI TCP response has an invalid frame header.")
        if response[13] != FRAME_FOOTER:
            raise PgiTcpProtocolError("PGI TCP response has an invalid frame footer.")
        if response[4] != self.device_id:
            raise PgiTcpProtocolError("PGI TCP response device ID does not match the request.")
        response_register = (response[5] << 8) | response[6]
        if response_register != register:
            raise PgiTcpProtocolError("PGI TCP response register does not match the request.")
        if response[7] != operation:
            raise PgiTcpProtocolError("PGI TCP response operation does not match the request.")

    def _close_locked(self) -> None:
        """Close and discard the current socket while already holding the client lock."""

        owned_socket = self._socket
        self._socket = None
        if owned_socket is not None:
            try:
                owned_socket.close()
            except OSError:
                pass
