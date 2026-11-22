"""
Fake IMC motor driver over USB-Serial.

Listens for 8-byte packets from the cooler panel and replies according to
driver_protocol.c semantics. Maintains internal register/state for tests.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import serial
from serial import SerialException

from app.config import (
    DEFAULT_BAUD,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
    PACKET_LEN,
    SERIAL_TIMEOUT_S,
)
from app.driver.protocol import (
    ADDRESS_TABLE_V1,
    DrvCmd,
    DrvFault,
    DrvReg,
    ParsedPacket,
    build_packet,
    parse_packet,
    reg_ids,
    response_command,
    verify_packet,
)

log = logging.getLogger(__name__)

LogCallback = Callable[[str], None]
PacketCallback = Callable[[ParsedPacket, Optional[bytes]], None]


@dataclass(frozen=True)
class PacketExchange:
    request: ParsedPacket
    response: Optional[ParsedPacket]
    timestamp: float
    # Host-side time from completed frame recognition to serial.write() entry.
    # This does not measure the electrical start bit on the one-wire bus.
    response_latency_ms: Optional[float] = None


@dataclass
class DriverState:
    """Simulated driver registers / runtime state."""

    pfc: int = 0
    pump: int = 0
    valve: int = 0
    fault: int = 0  # 32-bit conceptually; protocol data is 16-bit on bus
    fault_hi: int = 0
    vth: int = 250  # arbitrary temp-ish raw
    gpio: int = 0
    ctrl_mode: int = 0
    stop_start: int = 0  # 0=stop, 1=start
    speed: int = 0
    torque: int = 0
    power: int = 0
    script: int = 0x11
    load_param: int = 0
    save_param: int = 0
    limit_flag: int = 0
    dc_bus: int = 320  # raw units panel interprets
    vac: int = 2200  # raw
    # Behaviour switches for tests
    respond: bool = True
    force_bad_crc: bool = False
    rx_count: int = 0
    tx_count: int = 0
    last_commands: List[str] = field(default_factory=list)

    def read_reg(self, reg: DrvReg) -> int:
        mapping = {
            DrvReg.PFC: self.pfc,
            # Firmware exposes one combined register: bit 0x10 = pump,
            # bit 0x20 = valve. Both enum names intentionally alias it.
            DrvReg.PUMP: (0x10 if self.pump else 0) | (0x20 if self.valve else 0),
            DrvReg.VALVE: (0x10 if self.pump else 0) | (0x20 if self.valve else 0),
            DrvReg.FAULT: self.fault & 0xFFFF,
            DrvReg.VTH: self.vth,
            DrvReg.GPIO: self.gpio,
            DrvReg.CTRL_MODE_SELECT: self.ctrl_mode,
            DrvReg.STOP_START: self.stop_start,
            # SPEED/TORQUE/POWER are aliases of one firmware address.
            DrvReg.SPEED: self.speed,
            DrvReg.TORQUE: self.speed,
            DrvReg.POWER: self.speed,
            DrvReg.SCRIPT: self.script,
            DrvReg.LOAD_PARAM: self.load_param,
            DrvReg.SAVE_PARAM: self.save_param,
            DrvReg.LIMIT_FLAG: self.limit_flag,
            DrvReg.DC_BUS_VOLTAGE: self.dc_bus,
            DrvReg.VAC: self.vac,
        }
        return int(mapping.get(reg, 0)) & 0xFFFF

    def write_reg(self, reg: DrvReg, value: int) -> None:
        value &= 0xFFFF
        if reg == DrvReg.PFC:
            self.pfc = value
        elif reg in (DrvReg.PUMP, DrvReg.VALVE):
            self.pump = bool(value & 0x10)
            self.valve = bool(value & 0x20)
        elif reg == DrvReg.FAULT:
            self.fault = (self.fault & 0xFFFF0000) | value
        elif reg == DrvReg.VTH:
            self.vth = value
        elif reg == DrvReg.GPIO:
            self.gpio = value
        elif reg == DrvReg.CTRL_MODE_SELECT:
            self.ctrl_mode = value
        elif reg == DrvReg.STOP_START:
            self.stop_start = value
        elif reg in (DrvReg.SPEED, DrvReg.TORQUE, DrvReg.POWER):
            self.speed = value
            self.torque = value
            self.power = value
        elif reg == DrvReg.SCRIPT:
            self.script = value
        elif reg == DrvReg.LOAD_PARAM:
            self.load_param = value
        elif reg == DrvReg.SAVE_PARAM:
            self.save_param = value
        elif reg == DrvReg.LIMIT_FLAG:
            self.limit_flag = value
        elif reg == DrvReg.DC_BUS_VOLTAGE:
            self.dc_bus = value
        elif reg == DrvReg.VAC:
            self.vac = value

    def inject_fault(self, fault_bits: int) -> None:
        self.fault |= int(fault_bits)

    def clear_fault(self) -> None:
        self.fault = 0
        self.fault_hi = 0


class FakeDriver:
    def __init__(
        self,
        port: str,
        baud: int = DEFAULT_BAUD,
        on_log: Optional[LogCallback] = None,
        on_packet: Optional[PacketCallback] = None,
    ) -> None:
        self.port_name = port
        self.baud = baud
        self.on_log = on_log or (lambda m: None)
        self.on_packet = on_packet or (lambda req, rsp: None)
        self.state = DriverState()
        self._ser: Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._rx_buf = bytearray()
        self._lock = threading.RLock()
        self._history_changed = threading.Condition(self._lock)
        self._exchanges: List[PacketExchange] = []

    # ── lifecycle ─────────────────────────────────────────────
    def open(self) -> None:
        if self._ser and self._ser.is_open:
            return
        self._ser = serial.Serial(
            port=self.port_name,
            baudrate=self.baud,
            bytesize=DEFAULT_BYTESIZE,
            parity=DEFAULT_PARITY,
            stopbits=DEFAULT_STOPBITS,
            timeout=SERIAL_TIMEOUT_S,
        )
        self._rx_buf.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._rx_loop, name="FakeDriverRX", daemon=True)
        self._thread.start()
        self._log(f"FakeDriver opened on {self.port_name} @ {self.baud}")

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._log("FakeDriver closed")

    @property
    def is_open(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    # ── public test helpers ───────────────────────────────────
    def set_respond(self, enabled: bool) -> None:
        with self._lock:
            self.state.respond = bool(enabled)
        self._log(f"respond = {enabled}")

    def inject_fault(self, bits: int) -> None:
        with self._lock:
            self.state.inject_fault(bits)
        self._log(f"inject fault bits=0x{bits:X} -> fault=0x{self.state.fault:X}")

    def clear_fault(self) -> None:
        with self._lock:
            self.state.clear_fault()
        self._log("fault cleared")

    def set_reg(self, reg: DrvReg, value: int) -> None:
        with self._lock:
            self.state.write_reg(reg, value)
        self._log(f"set {reg.name} = {value}")

    def get_reg(self, reg: DrvReg) -> int:
        with self._lock:
            return self.state.read_reg(reg)

    def get_exchange_count(self, predicate=None, start_index: int = 0) -> int:
        """Count recorded requests, optionally filtered by an exchange predicate."""
        with self._lock:
            items = self._exchanges[max(0, start_index):]
            return sum(1 for item in items if predicate is None or predicate(item))

    def wait_for_command(
        self,
        pred,
        timeout_s: float = 5.0,
    ) -> Optional[ParsedPacket]:
        """Wait for a received request without swapping reader-thread callbacks."""
        start_index = len(self.exchange_snapshot())
        exchange = self.wait_for_exchange(
            lambda item: bool(pred(item.request)), timeout_s=timeout_s, start_index=start_index
        )
        return exchange.request if exchange else None

    def snapshot_commands(self) -> List[str]:
        with self._lock:
            return list(self.state.last_commands)

    def reset_history(self) -> None:
        with self._history_changed:
            self.state.last_commands.clear()
            self.state.rx_count = 0
            self.state.tx_count = 0
            self._exchanges.clear()

    def exchange_snapshot(self) -> List[PacketExchange]:
        with self._lock:
            return list(self._exchanges)

    def wait_for_exchange(
        self,
        predicate: Callable[[PacketExchange], bool],
        timeout_s: float = 5.0,
        start_index: int = 0,
    ) -> Optional[PacketExchange]:
        """Wait for a request/response pair without swapping callbacks from another thread."""
        deadline = time.monotonic() + timeout_s
        with self._history_changed:
            while True:
                for exchange in self._exchanges[max(0, start_index):]:
                    if predicate(exchange):
                        return exchange
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._history_changed.wait(min(remaining, 0.1))

    # ── RX loop ───────────────────────────────────────────────
    def _rx_loop(self) -> None:
        assert self._ser is not None
        while not self._stop.is_set():
            try:
                # Read only enough bytes to complete the next 8-byte frame.
                # Reading 64 bytes here made the driver wait for bytes that never
                # belong to a single protocol frame, delaying the response.
                needed = max(1, PACKET_LEN - len(self._rx_buf))
                data = self._ser.read(needed)
                if not data:
                    continue
                self._rx_buf.extend(data)
                self._drain_rx_buffer()
            except SerialException as e:
                self._log(f"serial error: {e}")
                break
            except Exception as e:
                self._log(f"rx loop error: {e}")
                time.sleep(0.05)

    def _drain_rx_buffer(self) -> None:
        """Recover 8-byte frame alignment after opening the port mid-frame or receiving noise."""
        valid_commands = {int(DrvCmd.READ_STATUS), int(DrvCmd.CLEAR_STATUS), int(DrvCmd.READ),
                          int(DrvCmd.WRITE), int(DrvCmd.LOAD_PARAM)}
        while len(self._rx_buf) >= PACKET_LEN:
            candidate = bytes(self._rx_buf[:PACKET_LEN])
            if candidate[0] == 0x01 and candidate[1] in valid_commands and verify_packet(candidate):
                del self._rx_buf[:PACKET_LEN]
                frame_ready_ns = time.perf_counter_ns()
                self._handle_packet(candidate, frame_ready_ns)
                continue

            # Find the next valid packet boundary instead of permanently consuming
            # fixed 8-byte chunks after a single dropped/noisy byte.
            next_offset = None
            for offset in range(1, len(self._rx_buf) - PACKET_LEN + 1):
                frame = bytes(self._rx_buf[offset:offset + PACKET_LEN])
                if frame[0] == 0x01 and frame[1] in valid_commands and verify_packet(frame):
                    next_offset = offset
                    break
            if next_offset is not None:
                skipped = bytes(self._rx_buf[:next_offset])
                del self._rx_buf[:next_offset]
                self._log(f"RX resync: discarded {len(skipped)} byte(s): {skipped.hex(' ')}")
            else:
                # Keep the last seven bytes in case a valid packet is split across reads.
                discard = len(self._rx_buf) - (PACKET_LEN - 1)
                if discard > 0:
                    skipped = bytes(self._rx_buf[:discard])
                    del self._rx_buf[:discard]
                    self._log(f"RX resync: skipped {len(skipped)} non-frame byte(s)")
                break

    def _handle_packet(self, raw: bytes, frame_ready_ns: Optional[int] = None) -> None:
        try:
            parsed = parse_packet(raw)
        except Exception as e:
            self._log(f"bad packet {raw.hex(' ')} ({e})")
            return

        with self._lock:
            self.state.rx_count += 1
            self.state.last_commands.append(
                f"{parsed.command_name}/{parsed.reg.name if parsed.reg else '??'} data={parsed.data}"
            )

        if not parsed.crc_ok:
            self._log(f"CRC FAIL {raw.hex(' ')}")
            self.on_packet(parsed, None)
            return

        self._log(
            f"RX {parsed.command_name} reg={parsed.reg.name if parsed.reg else f'{parsed.app_id:02X}/{parsed.reg_index:02X}'} "
            f"data={parsed.data} raw={raw.hex(' ')}"
        )

        rsp = self._build_response(parsed)
        with self._lock:
            should_respond = self.state.respond
            corrupt_crc = self.state.force_bad_crc
        response_latency_ms: Optional[float] = None
        if rsp and should_respond and self._ser and self._ser.is_open:
            if corrupt_crc:
                rsp = bytearray(rsp)
                rsp[7] ^= 0xFF
                rsp = bytes(rsp)
            try:
                # Measure host-side processing latency from complete-frame
                # recognition to immediately before handing the reply to pyserial.
                tx_start_ns = time.perf_counter_ns()
                if frame_ready_ns is not None:
                    response_latency_ms = (tx_start_ns - frame_ready_ns) / 1_000_000.0
                self._ser.write(rsp)
                with self._lock:
                    self.state.tx_count += 1
                self._log(f"TX {rsp.hex(' ')}")
                if response_latency_ms is not None:
                    status = "OK" if response_latency_ms <= 5.0 else "OVER_5MS"
                    self._log(
                        f"[TIMING] RX-frame-ready -> TX-write-start: "
                        f"{response_latency_ms:.3f} ms ({status}; target <= 5.000 ms)"
                    )
            except SerialException as e:
                self._log(f"TX failed: {e}")
                rsp = None
        else:
            if not should_respond:
                self._log("TX suppressed (respond=False)")
            elif not self._ser or not self._ser.is_open:
                self._log("TX not sent (serial port is closed)")
            rsp = None

        parsed_rsp = parse_packet(rsp) if rsp else None
        with self._history_changed:
            self._exchanges.append(
                PacketExchange(parsed, parsed_rsp, time.time(), response_latency_ms)
            )
            self._history_changed.notify_all()
        self.on_packet(parsed, rsp)

    def _build_response(self, req: ParsedPacket) -> Optional[bytes]:
        cmd = req.command
        app_id, reg_index = req.app_id, req.reg_index
        data = 0

        with self._lock:
            st = self.state
            reg = req.reg

            if cmd == DrvCmd.READ_STATUS:
                if reg != DrvReg.FAULT:
                    return None
                # Firmware Drv_Parse_Response() copies RSP_READ_STATUS.data into
                # drvState.error low 16 bits. It is the driver's fault bitmask,
                # not motor run/stop state.
                data = st.fault & 0xFFFF
            elif cmd == DrvCmd.CLEAR_STATUS:
                if reg != DrvReg.FAULT:
                    return None
                st.clear_fault()
                data = 0
            elif cmd == DrvCmd.READ:
                if reg is None:
                    return None
                data = st.read_reg(reg)
            elif cmd == DrvCmd.WRITE:
                if reg is None:
                    return None
                st.write_reg(reg, req.data)
                data = req.data
            elif cmd == DrvCmd.LOAD_PARAM:
                data = 0
            else:
                data = req.data

        rsp_cmd = response_command(cmd)
        # Echo same app_id/reg_index as request (matches panel expectation for matching)
        pkt = build_packet(rsp_cmd, app_id, reg_index, data, address=req.address)
        return pkt

    def _log(self, msg: str) -> None:
        line = f"[Driver] {msg}"
        log.info(line)
        try:
            self.on_log(line)
        except Exception:
            pass
