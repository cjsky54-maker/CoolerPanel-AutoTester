"""
Cooler panel <-> IMC motor driver UART protocol.

Firmware source of truth:
  driver_protocol.c / driver_protocol.h

Packet layout (8 bytes):
  [0] address (panel uses 0x01 toward driver)
  [1] command
  [2] app_id   (from addressTable)
  [3] reg_index (from addressTable)
  [4] data low
  [5] data high
  [6] crc high
  [7] crc low

CRC:
  sum of three little-endian uint16 words from bytes 0..5
  crc = (65536 - sum) & 0xFFFF
  then byte-swap for storage in buffer[6:8]
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional, Tuple


class DrvCmd(IntEnum):
    READ_STATUS = 0x00
    CLEAR_STATUS = 0x01
    READ = 0x05
    WRITE = 0x06
    LOAD_PARAM = 0x20
    NOPE = 0xFF
    # responses
    RSP_READ_STATUS = 0x80
    RSP_CLEAR_STATUS = 0x81
    RSP_READ = 0x85
    RSP_WRITE = 0x86
    RSP_LOAD_PARAM = 0xA0


class DrvReg(IntEnum):
    PFC = 0
    PUMP = 1
    VALVE = 2
    FAULT = 3
    VTH = 4
    GPIO = 5
    CTRL_MODE_SELECT = 6
    STOP_START = 7
    SPEED = 8
    TORQUE = 9
    POWER = 10
    SCRIPT = 11
    LOAD_PARAM = 12
    SAVE_PARAM = 13
    LIMIT_FLAG = 14
    DC_BUS_VOLTAGE = 15
    VAC = 16


# addressTable[IMC_CODE_VERSION=1][reg] = (app_id, reg_index)
# Order matches DRV_REG enum indices in firmware.
ADDRESS_TABLE_V1: List[Tuple[int, int]] = [
    (0x03, 0x42),  # PFC
    (0x34, 0x41),  # PUMP (shared PUMP/VALVE register)
    (0x34, 0x41),  # VALVE (shared PUMP/VALVE register)
    (0x00, 0x00),  # FAULT / READ_STATUS
    (0x01, 0x4C),  # VTH
    (0x34, 0x40),  # GPIO
    (0xF1, 0x02),  # CTRL_MODE_SELECT
    (0x01, 0x40),  # STOP_START
    (0xF1, 0x00),  # SPEED (shared SPEED/TORQUE/POWER register)
    (0xF1, 0x00),  # TORQUE (shared)
    (0xF1, 0x00),  # POWER (shared)
    (0x04, 0x40),  # SCRIPT
    (0x22, 0x00),  # LOAD_PARAM
    (0x21, 0x00),  # SAVE_PARAM
    (0xF1, 0x04),  # LIMIT_FLAG
    (0x1B, 0x45),  # DC_BUS_VOLTAGE
    (0x03, 0x57),  # VAC
]

# Fault bits from DRV_Fault enum
class DrvFault(IntEnum):
    MOTOR_GATEKILL = 1 << 0
    DC_BUS_CRITICAL_OV = 1 << 1
    DC_BUS_OV = 1 << 2
    DC_BUS_UV = 1 << 3
    FLUX_PLL = 1 << 4
    OVER_TEMP = 1 << 6
    ROTOR_LOCK = 1 << 7
    PHASE_LOSS = 1 << 8
    EXECUTION_FAULT = 1 << 10
    PARAM_LOAD_FAULT = 1 << 12
    UART_LINK_BREAK = 1 << 13
    HALL_TIMEOUT = 1 << 14
    HALL_INVALID = 1 << 15
    CONNECTION_FAULT = 1 << 16


def calc_crc(buffer6: bytes) -> int:
    """Calculate protocol CRC for first 6 bytes of packet."""
    if len(buffer6) < 6:
        raise ValueError("CRC needs at least 6 bytes")
    crc = 0
    crc += buffer6[0]
    crc += buffer6[1] << 8
    crc += buffer6[2]
    crc += buffer6[3] << 8
    crc += buffer6[4]
    crc += buffer6[5] << 8
    crc = (65536 - crc) & 0xFFFF
    # byte swap as in firmware
    crc = ((crc & 0xFF) << 8) | ((crc & 0xFF00) >> 8)
    return crc


def get_crc_from_packet(packet: bytes) -> int:
    return (packet[6] << 8) | packet[7]


def build_packet(
    command: int,
    app_id: int,
    reg_index: int,
    data: int = 0,
    address: int = 0x01,
) -> bytes:
    buf = bytearray(8)
    buf[0] = address & 0xFF
    buf[1] = command & 0xFF
    buf[2] = app_id & 0xFF
    buf[3] = reg_index & 0xFF
    buf[4] = data & 0xFF
    buf[5] = (data >> 8) & 0xFF
    crc = calc_crc(bytes(buf[:6]))
    buf[6] = (crc >> 8) & 0xFF
    buf[7] = crc & 0xFF
    return bytes(buf)


def verify_packet(packet: bytes) -> bool:
    if len(packet) != 8:
        return False
    return calc_crc(packet[:6]) == get_crc_from_packet(packet)


@dataclass
class ParsedPacket:
    raw: bytes
    address: int
    command: int
    app_id: int
    reg_index: int
    data: int
    crc_ok: bool
    reg: Optional[DrvReg] = None

    @property
    def command_name(self) -> str:
        try:
            return DrvCmd(self.command).name
        except ValueError:
            return f"CMD_0x{self.command:02X}"


def resolve_reg(app_id: int, reg_index: int) -> Optional[DrvReg]:
    """Resolve an address to its canonical enum member.

    Firmware has intentional aliases: PUMP/VALVE share one address and
    SPEED/TORQUE/POWER share another. Return the first enum as canonical;
    callers that need the semantic alias should use the requested DrvReg.
    """
    for i, (aid, ridx) in enumerate(ADDRESS_TABLE_V1):
        if aid == app_id and ridx == reg_index:
            return DrvReg(i)
    return None


def parse_packet(packet: bytes) -> ParsedPacket:
    if len(packet) != 8:
        raise ValueError(f"expected 8 bytes, got {len(packet)}")
    app_id = packet[2]
    reg_index = packet[3]
    data = packet[4] | (packet[5] << 8)
    return ParsedPacket(
        raw=bytes(packet),
        address=packet[0],
        command=packet[1],
        app_id=app_id,
        reg_index=reg_index,
        data=data,
        crc_ok=verify_packet(packet),
        reg=resolve_reg(app_id, reg_index),
    )


def response_command(request_cmd: int) -> int:
    mapping = {
        DrvCmd.READ_STATUS: DrvCmd.RSP_READ_STATUS,
        DrvCmd.CLEAR_STATUS: DrvCmd.RSP_CLEAR_STATUS,
        DrvCmd.READ: DrvCmd.RSP_READ,
        DrvCmd.WRITE: DrvCmd.RSP_WRITE,
        DrvCmd.LOAD_PARAM: DrvCmd.RSP_LOAD_PARAM,
    }
    return int(mapping.get(DrvCmd(request_cmd), request_cmd | 0x80))


def reg_ids(reg: DrvReg) -> Tuple[int, int]:
    return ADDRESS_TABLE_V1[int(reg)]


def response_matches(request: ParsedPacket, response: ParsedPacket) -> bool:
    """Check that a response belongs to a request (not merely CRC-valid)."""
    try:
        expected_command = response_command(request.command)
    except ValueError:
        return False
    return (
        request.crc_ok
        and response.crc_ok
        and response.address == request.address
        and response.command == expected_command
        and response.app_id == request.app_id
        and response.reg_index == request.reg_index
    )
