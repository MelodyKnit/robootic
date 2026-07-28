"""Offline tests for the PGI TCP protocol and adapter boundary."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import socket
import threading
import time
import unittest

from gripper_ai_controller.adapters.pgi.adapter import (
    PgiTcpAdapterError,
    PgiTcpGripperAdapter,
    PgiTcpOperationOutcomeUnknownError,
)
from gripper_ai_controller.adapters.pgi.client import (
    FRAME_FOOTER,
    FRAME_HEADER,
    GRIP_STATE_REGISTER,
    INITIALIZATION_REGISTER,
    PgiTcpClient,
    PgiTcpProtocolError,
    PgiTcpTransportError,
    PgiTcpWriteOutcomeUnknownError,
    READ_OPERATION,
    TARGET_FORCE_REGISTER,
    TARGET_POSITION_REGISTER,
    WRITE_OPERATION,
)
from gripper_ai_controller.domain.models import GripperAction, GripperCommand


def response_for(request, value=None):
    """Create one gateway-style response that correlates with the request."""

    response = bytearray(request)
    if value is not None:
        response[9] = value & 0xFF
        response[10] = (value >> 8) & 0xFF
    return bytes(response)


class FakeSocket:
    """Deterministic socket double that never opens a network connection."""

    def __init__(
        self,
        responder=None,
        receive_chunk_size=14,
        send_error=None,
        receive_error=None,
        connect_error=None,
    ):
        self.responder = responder or (lambda request: response_for(request))
        self.receive_chunk_size = receive_chunk_size
        self.send_error = send_error
        self.receive_error = receive_error
        self.connect_error = connect_error
        self.timeout = None
        self.endpoint = None
        self.sent = []
        self.pending = b""
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, endpoint):
        if self.connect_error is not None:
            raise self.connect_error
        self.endpoint = endpoint

    def sendall(self, payload):
        if self.send_error is not None:
            error = self.send_error
            self.send_error = None
            raise error
        self.sent.append(bytes(payload))
        self.pending = self.responder(bytes(payload))

    def recv(self, length):
        if self.receive_error is not None:
            error = self.receive_error
            self.receive_error = None
            raise error
        if not self.pending:
            return b""
        chunk_length = min(length, self.receive_chunk_size, len(self.pending))
        chunk = self.pending[:chunk_length]
        self.pending = self.pending[chunk_length:]
        return chunk

    def close(self):
        self.closed = True


class FakeSocketFactory:
    """Return prepared fake sockets in connection-attempt order."""

    def __init__(self, *sockets):
        self.sockets = list(sockets)
        self.created = []

    def __call__(self, family, socket_type):
        self.assert_socket_kind(family, socket_type)
        if not self.sockets:
            raise AssertionError("No fake socket remains for this connection attempt.")
        candidate = self.sockets.pop(0)
        self.created.append(candidate)
        return candidate

    @staticmethod
    def assert_socket_kind(family, socket_type):
        if family != socket.AF_INET or socket_type != socket.SOCK_STREAM:
            raise AssertionError("PGI client requested an unexpected socket type.")


class InterleaveDetectingSocket(FakeSocket):
    """Record whether a second request starts before the first response is consumed."""

    def __init__(self):
        super().__init__(receive_chunk_size=2)
        self.interleaved = False
        self._exchange_active = False
        self._state_lock = threading.Lock()

    def sendall(self, payload):
        with self._state_lock:
            if self._exchange_active:
                self.interleaved = True
            self._exchange_active = True
        super().sendall(payload)

    def recv(self, length):
        time.sleep(0.001)
        chunk = super().recv(length)
        with self._state_lock:
            if not self.pending:
                self._exchange_active = False
        return chunk


class FakePgiGateway:
    """Stateful PGI protocol double backed by one or more fake sockets."""

    def __init__(self, initialization_sequence=None):
        self.initialization_state = 0
        self.initialization_sequence = list(initialization_sequence or (2, 1))
        self.target_position = 1000
        self.force = 20
        self.grip_state = 1
        self.requests = []

    def respond(self, request):
        self.requests.append(request)
        register = (request[5] << 8) | request[6]
        operation = request[7]
        value = request[9] | (request[10] << 8)
        if operation == WRITE_OPERATION:
            if register == INITIALIZATION_REGISTER:
                self.initialization_state = 2
            elif register == TARGET_FORCE_REGISTER:
                self.force = value
            elif register == TARGET_POSITION_REGISTER:
                self.target_position = value
                self.grip_state = 0
            return response_for(request, value)

        if register == INITIALIZATION_REGISTER:
            if self.initialization_state == 2 and self.initialization_sequence:
                self.initialization_state = self.initialization_sequence.pop(0)
            value = self.initialization_state
        elif register == TARGET_POSITION_REGISTER:
            value = self.target_position
        elif register == GRIP_STATE_REGISTER:
            value = self.grip_state
        else:
            value = 0
        return response_for(request, value)

    @property
    def writes(self):
        return [request for request in self.requests if request[7] == WRITE_OPERATION]


def run_async(coroutine):
    """Run one adapter scenario on an isolated Python 3.7-compatible event loop."""

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


def create_adapter(gateway, socket_factory=None, **settings):
    """Create an adapter whose TCP client can only reach prepared fake sockets."""

    factory = socket_factory or FakeSocketFactory(FakeSocket(responder=gateway.respond))

    def create_client():
        return PgiTcpClient(
            "test.invalid",
            retry_count=0,
            socket_factory=factory,
        )

    return PgiTcpGripperAdapter(
        "test.invalid",
        client_factory=create_client,
        initialization_poll_seconds=settings.pop("initialization_poll_seconds", 0.001),
        **settings
    )


class PgiTcpClientTests(unittest.TestCase):
    """Verify protocol framing, retries, validation, and instance isolation."""

    def test_builds_exact_vendor_read_and_write_frames(self):
        read_frame = PgiTcpClient.build_frame(1, TARGET_POSITION_REGISTER, READ_OPERATION, 0)
        write_frame = PgiTcpClient.build_frame(1, TARGET_POSITION_REGISTER, WRITE_OPERATION, 1000)

        self.assertEqual(14, len(read_frame))
        self.assertEqual(FRAME_HEADER, read_frame[:4])
        self.assertEqual(bytes((1, 0x06, 0x02, 0, 0, 0, 0, 0, 0, FRAME_FOOTER)), read_frame[4:])
        self.assertEqual(0xE8, write_frame[9])
        self.assertEqual(0x03, write_frame[10])
        self.assertEqual(WRITE_OPERATION, write_frame[7])

    def test_reads_a_response_split_across_multiple_recv_calls(self):
        fake_socket = FakeSocket(
            responder=lambda request: response_for(request, 513),
            receive_chunk_size=3,
        )
        client = PgiTcpClient(
            "test.invalid", retry_count=0, socket_factory=FakeSocketFactory(fake_socket)
        )

        client.connect()
        value = client.get_target_position()

        self.assertEqual(513, value)
        self.assertEqual(("test.invalid", 8888), fake_socket.endpoint)
        self.assertEqual(1.0, fake_socket.timeout)

    def test_rejects_mismatched_response_correlation_fields(self):
        mutations = {
            "header": lambda response: response.__setitem__(0, 0),
            "footer": lambda response: response.__setitem__(13, 0),
            "device": lambda response: response.__setitem__(4, 2),
            "register": lambda response: response.__setitem__(6, 3),
            "operation": lambda response: response.__setitem__(7, WRITE_OPERATION),
        }
        for name, mutate in mutations.items():
            with self.subTest(field=name):
                def responder(request, mutation=mutate):
                    response = bytearray(response_for(request, 1))
                    mutation(response)
                    return bytes(response)

                fake_socket = FakeSocket(responder=responder)
                client = PgiTcpClient(
                    "test.invalid", retry_count=0, socket_factory=FakeSocketFactory(fake_socket)
                )
                client.connect()
                with self.assertRaises(PgiTcpProtocolError):
                    client.get_initialization_state()
                self.assertFalse(client.connected)

    def test_reconnects_and_retries_after_a_transport_timeout(self):
        failed = FakeSocket(receive_error=socket.timeout("timed out"))
        recovered = FakeSocket(responder=lambda request: response_for(request, 2))
        factory = FakeSocketFactory(failed, recovered)
        client = PgiTcpClient("test.invalid", retry_count=1, socket_factory=factory)

        client.connect()
        value = client.get_grip_state()

        self.assertEqual(2, value)
        self.assertTrue(failed.closed)
        self.assertTrue(client.connected)
        self.assertEqual(2, len(factory.created))

    def test_does_not_retry_a_write_after_recv_timeout(self):
        """A delivered write is never repeated when its acknowledgement is unavailable."""

        gateway = FakePgiGateway()
        first_socket = FakeSocket(
            responder=gateway.respond,
            receive_error=socket.timeout("response timed out"),
        )
        unused_socket = FakeSocket()
        factory = FakeSocketFactory(first_socket, unused_socket)
        client = PgiTcpClient("test.invalid", retry_count=1, socket_factory=factory)

        client.connect()
        with self.assertRaisesRegex(PgiTcpWriteOutcomeUnknownError, "outcome is unknown"):
            client.set_target_position(250)

        self.assertEqual(1, len(first_socket.sent))
        self.assertEqual(250, gateway.target_position)
        self.assertEqual(1, len(gateway.writes))
        self.assertEqual(1, len(factory.created))
        self.assertTrue(first_socket.closed)
        self.assertFalse(client.connected)
        self.assertEqual([], unused_socket.sent)

    def test_connect_uses_the_same_bounded_retry_budget_without_register_io(self):
        failed = FakeSocket(connect_error=socket.timeout("offline"))
        recovered = FakeSocket()
        factory = FakeSocketFactory(failed, recovered)
        client = PgiTcpClient("test.invalid", retry_count=1, socket_factory=factory)

        client.connect()

        self.assertTrue(client.connected)
        self.assertTrue(failed.closed)
        self.assertEqual(2, len(factory.created))
        self.assertEqual([], recovered.sent)

    def test_reports_incomplete_response_and_discards_the_socket(self):
        fake_socket = FakeSocket(responder=lambda request: b"\xff\xfe")
        client = PgiTcpClient(
            "test.invalid", retry_count=0, socket_factory=FakeSocketFactory(fake_socket)
        )
        client.connect()

        with self.assertRaises(PgiTcpTransportError):
            client.get_grip_state()

        self.assertTrue(fake_socket.closed)
        self.assertFalse(client.connected)

    def test_serializes_request_response_pairs_between_threads(self):
        fake_socket = InterleaveDetectingSocket()
        client = PgiTcpClient(
            "test.invalid", retry_count=0, socket_factory=FakeSocketFactory(fake_socket)
        )
        client.connect()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(client.get_target_position)
            second = executor.submit(client.get_grip_state)
            self.assertEqual(0, first.result())
            self.assertEqual(0, second.result())

        self.assertFalse(fake_socket.interleaved)
        self.assertEqual(2, len(fake_socket.sent))

    def test_semantic_methods_use_only_demonstrated_registers(self):
        fake_socket = FakeSocket()
        client = PgiTcpClient(
            "test.invalid", retry_count=0, socket_factory=FakeSocketFactory(fake_socket)
        )
        client.connect()

        client.request_initialization()
        client.set_target_force(20)
        client.set_target_position(1000)
        client.get_initialization_state()
        client.get_target_position()
        client.get_grip_state()

        operations = [
            (((frame[5] << 8) | frame[6]), frame[7], frame[9] | (frame[10] << 8))
            for frame in fake_socket.sent
        ]
        self.assertEqual(
            [
                (INITIALIZATION_REGISTER, WRITE_OPERATION, 0),
                (TARGET_FORCE_REGISTER, WRITE_OPERATION, 20),
                (TARGET_POSITION_REGISTER, WRITE_OPERATION, 1000),
                (INITIALIZATION_REGISTER, READ_OPERATION, 0),
                (TARGET_POSITION_REGISTER, READ_OPERATION, 0),
                (GRIP_STATE_REGISTER, READ_OPERATION, 0),
            ],
            operations,
        )
        self.assertFalse(hasattr(client, "set_target_speed"))
        self.assertFalse(hasattr(client, "stop"))

    def test_validates_endpoint_and_motion_ranges_before_socket_io(self):
        with self.assertRaises(ValueError):
            PgiTcpClient("", socket_factory=FakeSocketFactory())
        with self.assertRaises(ValueError):
            PgiTcpClient("test.invalid", port=0, socket_factory=FakeSocketFactory())
        with self.assertRaises(ValueError):
            PgiTcpClient("test.invalid", timeout_seconds=0, socket_factory=FakeSocketFactory())

        fake_socket = FakeSocket()
        client = PgiTcpClient(
            "test.invalid", retry_count=0, socket_factory=FakeSocketFactory(fake_socket)
        )
        client.connect()
        with self.assertRaises(ValueError):
            client.set_target_force(19)
        with self.assertRaises(ValueError):
            client.set_target_position(1001)
        self.assertEqual([], fake_socket.sent)


class PgiTcpGripperAdapterTests(unittest.TestCase):
    """Verify lifecycle safety and explicit physical-operation boundaries."""

    def test_startup_and_initialize_only_read_status_without_register_writes(self):
        async def scenario():
            gateway = FakePgiGateway()
            fake_socket = FakeSocket(responder=gateway.respond)
            adapter = create_adapter(gateway, FakeSocketFactory(fake_socket))

            await adapter.startup()
            self.assertTrue(adapter.started)
            self.assertTrue(adapter.last_status.connected)
            self.assertEqual([], gateway.writes)

            status = await adapter.initialize()
            self.assertFalse(status.initialized)
            self.assertTrue(status.connected)
            self.assertEqual(1000, status.position)
            self.assertFalse(status.position_is_feedback)
            self.assertEqual([], gateway.writes)

            await adapter.shutdown()
            self.assertTrue(fake_socket.closed)
            self.assertFalse(adapter.last_status.connected)

        run_async(scenario())

    def test_operator_initialization_uses_only_ordinary_zero_value_request(self):
        async def scenario():
            gateway = FakePgiGateway(initialization_sequence=(2, 1))
            adapter = create_adapter(gateway)
            await adapter.startup()

            status = await adapter.operator_initialize()

            self.assertTrue(status.initialized)
            self.assertFalse(status.initializing)
            writes = [
                ((request[5] << 8) | request[6], request[9] | (request[10] << 8))
                for request in gateway.writes
            ]
            self.assertEqual([(INITIALIZATION_REGISTER, 0)], writes)
            self.assertNotIn(0xA5, [value for _, value in writes])
            await adapter.shutdown()

        run_async(scenario())

    def test_operator_initialization_timeout_is_bounded_and_retains_error(self):
        async def scenario():
            gateway = FakePgiGateway(initialization_sequence=(2, 2, 2, 2, 2, 2, 2))
            adapter = create_adapter(
                gateway,
                initialization_timeout_seconds=0.005,
                initialization_poll_seconds=0.001,
            )
            await adapter.startup()

            with self.assertRaisesRegex(PgiTcpAdapterError, "timeout"):
                await adapter.operator_initialize()
            self.assertIsNotNone(adapter.last_status.last_error)
            self.assertFalse(adapter.last_status.moving)
            await adapter.shutdown()

        run_async(scenario())

    def test_authorized_position_command_writes_force_then_position_without_speed(self):
        async def scenario():
            gateway = FakePgiGateway()
            gateway.initialization_state = 1
            adapter = create_adapter(gateway)
            await adapter.startup()

            status = await adapter.execute(
                GripperCommand(
                    GripperAction.MOVE_TO_POSITION,
                    target_position=250,
                    force_percent=35,
                )
            )

            writes = [
                ((request[5] << 8) | request[6], request[9] | (request[10] << 8))
                for request in gateway.writes
            ]
            self.assertEqual(
                [(TARGET_FORCE_REGISTER, 35), (TARGET_POSITION_REGISTER, 250)],
                writes,
            )
            self.assertEqual(250, status.position)
            self.assertFalse(status.position_is_feedback)
            self.assertTrue(status.moving)
            self.assertFalse(adapter.supports_speed)
            self.assertFalse(adapter.supports_stop)
            self.assertEqual("physical", adapter.control_mode)
            await adapter.shutdown()

        run_async(scenario())

    def test_adapter_surfaces_an_unconfirmed_write_as_an_unknown_outcome(self):
        """An adapter must preserve the safety distinction after the client closes itself."""

        class OutcomeUnknownClient:
            """Minimal PGI client double that loses its acknowledgement after one write."""

            def __init__(self):
                self.connected = False
                self.position_writes = 0

            def connect(self):
                self.connected = True

            def close(self):
                self.connected = False

            def get_initialization_state(self):
                return 1

            def get_target_position(self):
                return 500

            def get_grip_state(self):
                return 1

            def set_target_position(self, position):
                del position
                self.position_writes += 1
                self.connected = False
                raise PgiTcpWriteOutcomeUnknownError("acknowledgement lost")

        async def scenario():
            client = OutcomeUnknownClient()
            adapter = PgiTcpGripperAdapter(
                "test.invalid",
                client_factory=lambda: client,
            )
            await adapter.startup()

            with self.assertRaises(PgiTcpOperationOutcomeUnknownError):
                await adapter.execute(
                    GripperCommand(GripperAction.MOVE_TO_POSITION, target_position=250)
                )

            self.assertEqual(1, client.position_writes)
            self.assertFalse(adapter.last_status.connected)
            self.assertIn("acknowledgement lost", adapter.last_status.last_error)
            await adapter.shutdown()

        run_async(scenario())

    def test_rejects_speed_stop_hold_and_missing_target_before_device_writes(self):
        async def scenario():
            gateway = FakePgiGateway()
            gateway.initialization_state = 1
            adapter = create_adapter(gateway)
            await adapter.startup()

            commands = (
                GripperCommand(GripperAction.STOP),
                GripperCommand(GripperAction.HOLD),
                GripperCommand(GripperAction.OPEN),
                GripperCommand(
                    GripperAction.MOVE_TO_POSITION,
                    target_position=300,
                    speed_percent=20,
                ),
            )
            for command in commands:
                with self.assertRaises(PgiTcpAdapterError):
                    await adapter.execute(command)
            self.assertEqual([], gateway.writes)
            await adapter.shutdown()

        run_async(scenario())

    def test_failed_startup_stays_degraded_and_reconnect_only_reads(self):
        async def scenario():
            gateway = FakePgiGateway()
            failed = FakeSocket(connect_error=socket.timeout("offline"))
            recovered = FakeSocket(responder=gateway.respond)
            factory = FakeSocketFactory(failed, recovered)
            adapter = create_adapter(gateway, factory)

            await adapter.startup()
            self.assertTrue(adapter.started)
            self.assertFalse(adapter.last_status.connected)
            self.assertIn("offline", adapter.last_status.last_error)

            status = await adapter.reconnect()
            self.assertTrue(status.connected)
            self.assertEqual([], gateway.writes)
            await adapter.shutdown()

        run_async(scenario())

    def test_physical_adapter_rejects_mirror_synchronization(self):
        async def scenario():
            gateway = FakePgiGateway()
            adapter = create_adapter(gateway)
            await adapter.startup()
            with self.assertRaises(PgiTcpAdapterError):
                await adapter.synchronize(adapter.last_status)
            await adapter.shutdown()

        run_async(scenario())


if __name__ == "__main__":
    unittest.main()
