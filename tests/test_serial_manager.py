import itertools
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import picobot.transport.serial_manager as serial_manager


class SerialManagerHelpersTests(unittest.TestCase):
    def test_finalize_handshake_sends_probe_and_clears_buffer(self) -> None:
        ser = mock.Mock()
        ser.readline.side_effect = [b"", b"PICO_READY\n", b""]
        ser.reset_input_buffer = mock.Mock()
        time_values = itertools.chain([0.0, 0.0, 0.2, 0.4, 0.6], itertools.repeat(1.0))
        with mock.patch(
            "picobot.transport.serial_manager.time.time",
            side_effect=lambda: next(time_values),
        ):
            serial_manager.finalize_handshake(ser)
        ser.write.assert_called_with(serial_manager.HANDSHAKE_COMMAND)
        ser.flush.assert_called()
        ser.reset_input_buffer.assert_called_once()

    def test_wait_for_ack_returns_true_on_ack(self) -> None:
        ser = mock.Mock()
        ser.readline.side_effect = [b"", b"ACK\n"]
        time_values = itertools.chain([0.0, 0.0, 0.2], itertools.repeat(1.0))
        with mock.patch(
            "picobot.transport.serial_manager.time.time",
            side_effect=lambda: next(time_values),
        ):
            self.assertTrue(serial_manager.wait_for_ack(ser, timeout=1.0))

    def test_wait_for_ack_times_out_without_ack(self) -> None:
        ser = mock.Mock()
        ser.readline.side_effect = [b"", b"", b""]
        time_values = itertools.chain([0.0, 0.0, 0.6, 1.2, 1.8], itertools.repeat(2.0))
        with mock.patch(
            "picobot.transport.serial_manager.time.time",
            side_effect=lambda: next(time_values),
        ):
            self.assertFalse(serial_manager.wait_for_ack(ser, timeout=1.0))

    @mock.patch("picobot.transport.serial_manager.time.sleep", return_value=None)
    @mock.patch("picobot.transport.serial_manager.serial.Serial")
    @mock.patch("picobot.transport.serial_manager.serial.tools.list_ports.comports")
    def test_discover_data_port_prefers_ready_port(
        self, mock_comports, mock_serial, _sleep
    ) -> None:
        mock_comports.return_value = [
            SimpleNamespace(device="COM1"),
            SimpleNamespace(device="COM2"),
        ]

        def serial_factory(port, *args, **kwargs):
            ser = mock.Mock()
            ser.dtr = True
            ser.rts = False
            ser.flush = mock.Mock()
            if port == "COM1":
                ser.readline.side_effect = [b">>>\n", b"", b""]
            else:
                ser.readline.side_effect = [b"PICO_READY\n", b"", b""]
            return ser

        mock_serial.side_effect = serial_factory

        result = serial_manager.discover_data_port()
        self.assertEqual(result, "COM2")
        self.assertGreaterEqual(mock_serial.call_count, 2)

    def test_send_payload_waits_for_ack(self) -> None:
        manager = serial_manager.SerialManager("COM9")
        mock_serial = mock.Mock()
        mock_serial.is_open = True
        manager._serial = mock_serial

        def resolve_ack() -> None:
            manager._resolve_next_ack()

        ack_thread = threading.Timer(0.01, resolve_ack)
        ack_thread.start()
        try:
            self.assertTrue(
                manager.send_payload("hid|key|down|w", wait_ack=True, timeout=0.5)
            )
        finally:
            ack_thread.cancel()

        mock_serial.write.assert_called()
        args, _ = mock_serial.write.call_args
        self.assertTrue(args[0].endswith(b"\n"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
