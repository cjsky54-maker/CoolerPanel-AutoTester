"""Host-side tests for firmware file validation and STVP argument construction."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.stlink.flash import FirmwareFileError, STLinkController, validate_firmware_file


def ihex_record(address: int, record_type: int, payload: bytes) -> str:
    data = bytes([len(payload), (address >> 8) & 0xFF, address & 0xFF, record_type]) + payload
    checksum = (-sum(data)) & 0xFF
    return ":" + (data + bytes([checksum])).hex().upper()


class FirmwareValidationTests(unittest.TestCase):
    def test_valid_intel_hex(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "firmware.hex"
            path.write_text(
                ihex_record(0x8000, 0x00, b"\x12\x34") + "\n" + ihex_record(0, 0x01, b"") + "\n",
                encoding="ascii",
            )
            self.assertEqual(validate_firmware_file(str(path)), ("Intel HEX", 2))

    def test_reject_iar_out_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "firmware.out"
            path.write_bytes(b"\x7fELF" + b"\x00" * 24)
            with self.assertRaises(FirmwareFileError):
                validate_firmware_file(str(path))

    def test_reject_bad_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.hex"
            path.write_text(":020000000102FA\n:00000001FF\n", encoding="ascii")
            with self.assertRaises(FirmwareFileError):
                validate_firmware_file(str(path))


class STVPCommandTests(unittest.TestCase):
    def test_stvp_command_uses_swim_and_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "firmware.hex"
            firmware.write_text(
                ihex_record(0x8000, 0x00, b"\x12\x34") + "\n" + ihex_record(0, 0x01, b"") + "\n",
                encoding="ascii",
            )
            fake_exe = root / "STVP_CmdLine.exe"
            fake_exe.write_text("placeholder", encoding="ascii")
            stage = root / "CPAT" / "firmware.hex"
            stage.parent.mkdir()
            stage.write_bytes(firmware.read_bytes())
            controller = STLinkController(mcu="STM8L052R8", stvp_cli_path=str(fake_exe), backend="stvp")
            controller._stage_stvp_file = lambda source, fmt: (str(stage), None)  # type: ignore[method-assign]
            output = (
                "<<< Programming PROGRAM MEMORY succeeds\n"
                "<<< Verifying PROGRAM MEMORY succeeds\n"
            )
            with patch("app.stlink.flash.subprocess.run") as mocked_run:
                mocked_run.return_value = subprocess.CompletedProcess([], 0, output, "")
                result = controller.flash_hex(str(firmware))
            self.assertTrue(result.ok, result.message)
            args = mocked_run.call_args.args[0]
            self.assertIn("-BoardName=ST-LINK", args)
            self.assertIn("-ProgMode=SWIM", args)
            self.assertIn("-Device=STM8L052R8", args)
            self.assertIn("-verif", args)
            self.assertIn(f"-FileProg={stage}", args)
            self.assertIn("-no_progData", args)
            self.assertIn("-no_progOption", args)

    def test_missing_verify_marker_is_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "firmware.hex"
            firmware.write_text(
                ihex_record(0x8000, 0x00, b"\x12\x34") + "\n" + ihex_record(0, 0x01, b"") + "\n",
                encoding="ascii",
            )
            fake_exe = root / "STVP_CmdLine.exe"
            fake_exe.write_text("placeholder", encoding="ascii")
            stage = root / "CPAT" / "firmware.hex"
            stage.parent.mkdir()
            stage.write_bytes(firmware.read_bytes())
            controller = STLinkController(stvp_cli_path=str(fake_exe), backend="stvp")
            controller._stage_stvp_file = lambda source, fmt: (str(stage), None)  # type: ignore[method-assign]
            with patch("app.stlink.flash.subprocess.run") as mocked_run:
                mocked_run.return_value = subprocess.CompletedProcess([], 0, "<<< Programming PROGRAM MEMORY succeeds", "")
                result = controller.flash_hex(str(firmware))
            self.assertFalse(result.ok)
            self.assertIn("Verify", result.message)


if __name__ == "__main__":
    unittest.main()
