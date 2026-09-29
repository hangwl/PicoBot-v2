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

    def _manager(self):
        manager = serial_manager.SerialManager("COM9")
        ser = mock.Mock()
        ser.is_open = True
        manager._serial = ser
        manager.numbered = True             # v2 firmware
        return manager, ser

    def test_older_firmware_gets_plain_commands_and_no_keepalive(self) -> None:
        manager, ser = self._manager()
        manager.numbered = False
        manager.send_payload("hid|key|down|w")
        self.assertEqual(ser.write.call_args[0][0], b"hid|key|down|w\n")
        ser.write.reset_mock()
        manager._last_tx = 0.0
        manager._keepalive(ser)
        ser.write.assert_not_called()

    def test_ready_line_selects_the_protocol(self) -> None:
        manager, ser = self._manager()
        manager.numbered = False
        ser.readline.side_effect = [b"PICO_READY v2\n", OSError("done")]
        manager._reader_loop()
        self.assertTrue(manager.numbered)
        manager, ser = self._manager()
        ser.readline.side_effect = [b"PICO_READY\n", OSError("done")]
        manager._reader_loop()
        self.assertFalse(manager.numbered)

    @staticmethod
    def _seq_of(ser, call=-1) -> str:
        return ser.write.call_args_list[call][0][0].decode().split(":", 1)[0]

    def test_send_payload_waits_for_its_numbered_ack(self) -> None:
        manager, ser = self._manager()
        t = threading.Timer(0.01, lambda: manager._resolve(f"ACK {self._seq_of(ser)}"))
        t.start()
        try:
            self.assertTrue(manager.send_payload("hid|key|down|w", wait_ack=True, timeout=0.5))
        finally:
            t.cancel()
        sent = ser.write.call_args[0][0].decode()
        self.assertRegex(sent, r"^\d+:hid\|key\|down\|w\n$")

    def test_late_ack_is_not_credited_to_the_next_command(self) -> None:
        manager, ser = self._manager()
        self.assertFalse(manager.send_payload("hid|key|down|a", wait_ack=True, timeout=0.05))
        late = self._seq_of(ser)
        # The next command's waiter must ignore the first command's reply.
        t = threading.Timer(0.01, lambda: manager._resolve(f"ACK {late}"))
        t.start()
        try:
            self.assertFalse(manager.send_payload("hid|key|down|b", wait_ack=True, timeout=0.1))
        finally:
            t.cancel()

    def test_nack_fails_the_sender(self) -> None:
        manager, ser = self._manager()
        t = threading.Timer(0.01, lambda: manager._resolve(f"NACK {self._seq_of(ser)}"))
        t.start()
        try:
            self.assertFalse(manager.send_payload("hid|key|down|zz", wait_ack=True, timeout=0.5))
        finally:
            t.cancel()

    def test_bare_ack_from_older_firmware_resolves_in_order(self) -> None:
        manager, ser = self._manager()
        t = threading.Timer(0.01, lambda: manager._resolve("ACK"))
        t.start()
        try:
            self.assertTrue(manager.send_payload("hid|key|down|w", wait_ack=True, timeout=0.5))
        finally:
            t.cancel()
        self.assertFalse(manager._ack_waiters)
        self.assertFalse(manager._by_seq)

    def test_keepalive_only_when_idle(self) -> None:
        manager, ser = self._manager()
        manager._last_tx = serial_manager.time.monotonic()
        manager._keepalive(ser)
        ser.write.assert_not_called()
        manager._last_tx = 0.0
        manager._keepalive(ser)
        ser.write.assert_called_once_with(serial_manager.KEEPALIVE_COMMAND)

    def test_lost_port_closes_and_fails_waiters(self) -> None:
        manager, ser = self._manager()
        ser.readline.side_effect = OSError("device gone")
        lost = []
        manager.on_lost = lost.append
        waiter = serial_manager._Waiter()
        manager._ack_waiters.append(waiter)
        manager._reader_loop()
        self.assertFalse(manager.is_open)
        self.assertTrue(waiter.event.is_set())
        self.assertFalse(waiter.ok)
        self.assertEqual(lost, ["device gone"])


if __name__ == "__main__":
    unittest.main()
