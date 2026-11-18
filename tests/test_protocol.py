"""Unit tests for driver packet codec (no hardware required)."""

from __future__ import annotations

import unittest

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.driver.protocol import (
    DrvCmd,
    DrvReg,
    build_packet,
    calc_crc,
    parse_packet,
    reg_ids,
    response_command,
    response_matches,
    ADDRESS_TABLE_V1,
    verify_packet,
)


def test_crc_roundtrip():
    pkt = build_packet(DrvCmd.READ, 0x01, 0x78, 0x0001, address=0x01)
    assert len(pkt) == 8
    assert verify_packet(pkt)
    parsed = parse_packet(pkt)
    assert parsed.crc_ok
    assert parsed.command == DrvCmd.READ
    assert parsed.data == 1


def test_response_cmd_mapping():
    assert response_command(DrvCmd.READ_STATUS) == DrvCmd.RSP_READ_STATUS
    assert response_command(DrvCmd.WRITE) == DrvCmd.RSP_WRITE


def test_reg_ids_stop_start():
    app_id, reg_index = reg_ids(DrvReg.STOP_START)
    assert (app_id, reg_index) == (0x01, 0x40)


def test_firmware_address_table_v1():
    assert ADDRESS_TABLE_V1 == [
        (0x03, 0x42), (0x34, 0x41), (0x34, 0x41), (0x00, 0x00),
        (0x01, 0x4C), (0x34, 0x40), (0xF1, 0x02), (0x01, 0x40),
        (0xF1, 0x00), (0xF1, 0x00), (0xF1, 0x00), (0x04, 0x40),
        (0x22, 0x00), (0x21, 0x00), (0xF1, 0x04), (0x1B, 0x45), (0x03, 0x57),
    ]


def test_response_matches_request_identity():
    app_id, reg_index = reg_ids(DrvReg.FAULT)
    request = parse_packet(build_packet(DrvCmd.READ_STATUS, app_id, reg_index))
    response = parse_packet(build_packet(DrvCmd.RSP_READ_STATUS, app_id, reg_index, 0x0040))
    assert response_matches(request, response)
    wrong = parse_packet(build_packet(DrvCmd.RSP_READ, app_id, reg_index, 0x0040))
    assert not response_matches(request, wrong)


def test_known_vector_style():
    app_id, reg_index = reg_ids(DrvReg.STOP_START)
    pkt = build_packet(DrvCmd.WRITE, app_id, reg_index, 1)
    assert verify_packet(pkt)
    # mutate CRC -> fail
    bad = bytearray(pkt)
    bad[7] ^= 0xFF
    assert not verify_packet(bytes(bad))


class ProtocolCodecTests(unittest.TestCase):
    def test_crc_roundtrip(self):
        test_crc_roundtrip()

    def test_response_cmd_mapping(self):
        test_response_cmd_mapping()

    def test_reg_ids_stop_start(self):
        test_reg_ids_stop_start()

    def test_firmware_address_table_v1(self):
        test_firmware_address_table_v1()

    def test_response_matches_request_identity(self):
        test_response_matches_request_identity()

    def test_known_vector_style(self):
        test_known_vector_style()


if __name__ == "__main__":
    test_crc_roundtrip()
    test_response_cmd_mapping()
    test_reg_ids_stop_start()
    test_firmware_address_table_v1()
    test_response_matches_request_identity()
    test_known_vector_style()
    print("protocol tests OK")
