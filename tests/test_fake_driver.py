"""Fake Driver semantics matching the panel's driver_protocol.c."""

from __future__ import annotations

import sys
import types
import unittest

# Unit tests below do not open a COM port. Allow the protocol/state tests to run
# even on a build host where pyserial is not installed yet.
try:
    import serial  # noqa: F401
except ImportError:  # pragma: no cover - used on minimal CI images
    serial_stub = types.ModuleType("serial")

    class SerialException(Exception):
        pass

    serial_stub.SerialException = SerialException  # type: ignore[attr-defined]
    serial_stub.Serial = object  # type: ignore[attr-defined]
    sys.modules["serial"] = serial_stub

from app.driver.fake_driver import FakeDriver
from app.driver.protocol import DrvCmd, DrvFault, DrvReg, build_packet, parse_packet, reg_ids


class FakeDriverSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver("COM_TEST")

    def test_read_status_returns_fault_mask_not_run_state(self):
        self.driver.inject_fault(DrvFault.OVER_TEMP)
        app_id, reg_index = reg_ids(DrvReg.FAULT)
        request = parse_packet(build_packet(DrvCmd.READ_STATUS, app_id, reg_index))
        response = parse_packet(self.driver._build_response(request))
        self.assertEqual(response.command, DrvCmd.RSP_READ_STATUS)
        self.assertEqual(response.data, int(DrvFault.OVER_TEMP))

    def test_clear_status_clears_fault_and_acknowledges(self):
        self.driver.inject_fault(DrvFault.OVER_TEMP)
        app_id, reg_index = reg_ids(DrvReg.FAULT)
        request = parse_packet(build_packet(DrvCmd.CLEAR_STATUS, app_id, reg_index))
        response = parse_packet(self.driver._build_response(request))
        self.assertEqual(response.command, DrvCmd.RSP_CLEAR_STATUS)
        self.assertEqual(response.data, 0)
        self.assertEqual(self.driver.get_reg(DrvReg.FAULT), 0)


    def test_stream_resynchronizes_after_leading_noise_byte(self):
        class DummySerial:
            is_open = True

            def __init__(self):
                self.writes = []

            def write(self, data):
                self.writes.append(bytes(data))

        dummy = DummySerial()
        self.driver._ser = dummy
        app_id, reg_index = reg_ids(DrvReg.FAULT)
        request = build_packet(DrvCmd.READ_STATUS, app_id, reg_index)
        self.driver._rx_buf.extend(b"\xA5" + request)
        self.driver._drain_rx_buffer()
        exchanges = self.driver.exchange_snapshot()
        self.assertEqual(len(exchanges), 1)
        self.assertEqual(exchanges[0].request.command, DrvCmd.READ_STATUS)
        self.assertIsNotNone(exchanges[0].response)
        self.assertEqual(len(dummy.writes), 1)

    def test_rx_loop_reads_one_complete_frame_without_waiting_for_64_bytes(self):
        class DummySerial:
            is_open = True

            def __init__(self, driver, frame):
                self.driver = driver
                self.frame = frame
                self.read_sizes = []
                self.writes = []

            def read(self, size):
                self.read_sizes.append(size)
                self.driver._stop.set()
                return self.frame

            def write(self, data):
                self.writes.append(bytes(data))
                return len(data)

        app_id, reg_index = reg_ids(DrvReg.FAULT)
        request = build_packet(DrvCmd.CLEAR_STATUS, app_id, reg_index)
        dummy = DummySerial(self.driver, request)
        self.driver._ser = dummy
        self.driver._stop.clear()
        self.driver._rx_loop()

        self.assertEqual(dummy.read_sizes, [8])
        self.assertEqual(len(dummy.writes), 1)
        exchange = self.driver.exchange_snapshot()[0]
        self.assertIsNotNone(exchange.response)
        self.assertIsNotNone(exchange.response_latency_ms)
        self.assertGreaterEqual(exchange.response_latency_ms, 0.0)

    def test_read_dc_bus_responds_with_seeded_value(self):
        self.driver.set_reg(DrvReg.DC_BUS_VOLTAGE, 330)
        app_id, reg_index = reg_ids(DrvReg.DC_BUS_VOLTAGE)
        request = parse_packet(build_packet(DrvCmd.READ, app_id, reg_index))
        response = parse_packet(self.driver._build_response(request))
        self.assertEqual(response.command, DrvCmd.RSP_READ)
        self.assertEqual(response.data, 330)

    def test_pump_valve_are_one_combined_register(self):
        self.driver.set_reg(DrvReg.PUMP, 0x30)
        self.assertEqual(self.driver.get_reg(DrvReg.PUMP), 0x30)
        self.assertEqual(self.driver.get_reg(DrvReg.VALVE), 0x30)
        self.driver.set_reg(DrvReg.VALVE, 0x20)
        self.assertEqual(self.driver.get_reg(DrvReg.PUMP), 0x20)
        self.assertFalse(self.driver.state.pump)
        self.assertTrue(self.driver.state.valve)

    def test_speed_torque_power_are_one_shared_register(self):
        self.driver.set_reg(DrvReg.SPEED, 1450)
        self.assertEqual(self.driver.get_reg(DrvReg.TORQUE), 1450)
        self.assertEqual(self.driver.get_reg(DrvReg.POWER), 1450)
        self.driver.set_reg(DrvReg.POWER, 900)
        self.assertEqual(self.driver.get_reg(DrvReg.SPEED), 900)
        self.assertEqual(self.driver.get_reg(DrvReg.TORQUE), 900)

    def test_write_unknown_register_is_not_acknowledged(self):
        request = parse_packet(build_packet(DrvCmd.WRITE, 0xAA, 0x55, 123))
        self.assertIsNone(self.driver._build_response(request))

    def test_read_status_rejects_wrong_register_address(self):
        request = parse_packet(build_packet(DrvCmd.READ_STATUS, 0x34, 0x41))
        self.assertIsNone(self.driver._build_response(request))

    def test_force_bad_crc_corrupts_response(self):
        class DummySerial:
            is_open = True
            def __init__(self):
                self.writes = []
            def write(self, data):
                self.writes.append(bytes(data))

        dummy = DummySerial()
        self.driver._ser = dummy
        self.driver.state.force_bad_crc = True
        app_id, reg_index = reg_ids(DrvReg.FAULT)
        self.driver._handle_packet(build_packet(DrvCmd.READ_STATUS, app_id, reg_index))
        self.assertEqual(len(dummy.writes), 1)
        self.assertFalse(parse_packet(dummy.writes[0]).crc_ok)


if __name__ == "__main__":
    unittest.main()
