"""STM8L052R8 programming helpers.

The supported Windows path is ST Visual Programmer's STVP_CmdLine.exe using
ST-LINK/USB/SWIM.  The optional stm8flash backend is retained for environments
where it has been validated locally.  IAR .out files are ELF/DWARF outputs and
must never be passed to this programmer; choose the Intel HEX from IAR Debug.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from app.config import DEFAULT_MCU, FLASH_TIMEOUT_S

log = logging.getLogger(__name__)
LogCallback = Callable[[str], None]


@dataclass
class FlashResult:
    ok: bool
    message: str
    cmdline: str = ""
    stdout: str = ""
    stderr: str = ""


class FirmwareFileError(ValueError):
    pass


def _candidate_stvp_paths() -> List[Path]:
    candidates: List[Path] = []
    env_roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")]
    for root in filter(None, env_roots):
        candidates.append(Path(root) / "STMicroelectronics" / "st_toolset" / "stvp" / "STVP_CmdLine.exe")
        candidates.append(Path(root) / "STMicroelectronics" / "ST Toolset" / "stvp" / "STVP_CmdLine.exe")
    candidates.extend(
        [
            Path(r"C:\STMicroelectronics\st_toolset\stvp\STVP_CmdLine.exe"),
            Path(r"C:\Program Files (x86)\STMicroelectronics\st_toolset\stvp\STVP_CmdLine.exe"),
            Path(r"C:\Program Files\STMicroelectronics\st_toolset\stvp\STVP_CmdLine.exe"),
        ]
    )
    return candidates


def discover_stvp() -> Optional[str]:
    for name in ("STVP_CmdLine.exe", "STVP_CmdLine"):
        found = shutil.which(name)
        if found:
            return found
    for path in _candidate_stvp_paths():
        if path.is_file():
            return str(path)
    return None


def discover_stm8flash() -> Optional[str]:
    return shutil.which("stm8flash") or shutil.which("stm8flash.exe")


def validate_firmware_file(path: str) -> Tuple[str, int]:
    """Validate Intel HEX or Motorola S-record contents, not just extension."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise FirmwareFileError(f"فایل Firmware پیدا نشد: {p}")
    raw = p.read_bytes()
    if not raw:
        raise FirmwareFileError("فایل Firmware خالی است.")
    if raw.startswith(b"\x7fELF") or p.suffix.lower() == ".out":
        raise FirmwareFileError(
            "فایل .out خروجی لینک‌شدهٔ IAR (ELF/DWARF) است و ورودی مستقیم STVP نیست. "
            "در IAR، Configuration=Debug را Build کن و فایل "
            "Debug\\Exe\\FW_CoolerPanel_V4.x.hex را انتخاب کن."
        )
    if b"\x00" in raw[:256]:
        raise FirmwareFileError("فایل انتخاب‌شده باینری خام است؛ فقط Intel HEX یا Motorola S-record پشتیبانی می‌شود.")

    try:
        lines = [line.strip() for line in raw.decode("ascii").splitlines() if line.strip()]
    except UnicodeDecodeError as exc:
        raise FirmwareFileError("فرمت فایل پشتیبانی نمی‌شود؛ فایل HEX یا S-record متنی انتخاب کن.") from exc
    if not lines:
        raise FirmwareFileError("فایل Firmware رکورد متنی ندارد.")

    if all(line.startswith(":") for line in lines):
        eof_index: Optional[int] = None
        for idx, line in enumerate(lines, 1):
            try:
                if len(line) < 11 or (len(line) - 1) % 2:
                    raise ValueError("طول رکورد نامعتبر")
                data = bytes.fromhex(line[1:])
                if len(data) != data[0] + 5:
                    raise ValueError("byte count نامعتبر")
                if sum(data) & 0xFF:
                    raise ValueError("checksum نامعتبر")
                record_type = data[3]
                if record_type == 0x01:
                    if data[0] != 0:
                        raise ValueError("EOF باید بدون data باشد")
                    if eof_index is not None:
                        raise ValueError("EOF تکراری")
                    eof_index = idx
            except Exception as exc:
                raise FirmwareFileError(f"رکورد Intel HEX شمارهٔ {idx} خراب است: {exc}") from exc
        if eof_index is None:
            raise FirmwareFileError("رکورد پایان فایل Intel HEX (EOF) وجود ندارد.")
        if eof_index != len(lines):
            raise FirmwareFileError("بعد از رکورد EOF در Intel HEX رکورد اضافه وجود دارد.")
        return "Intel HEX", len(lines)

    if all(re.match(r"^S[0-9][0-9A-Fa-f]+$", line) for line in lines):
        termination_seen = False
        for idx, line in enumerate(lines, 1):
            try:
                data = bytes.fromhex(line[2:])
                if not data or len(data) != data[0] + 1:
                    raise ValueError("byte count نامعتبر")
                if (sum(data) & 0xFF) != 0xFF:
                    raise ValueError("checksum نامعتبر")
                if line[:2] in ("S7", "S8", "S9"):
                    if termination_seen:
                        raise ValueError("رکورد پایان تکراری")
                    termination_seen = True
                    if idx != len(lines):
                        raise ValueError("بعد از رکورد پایان، رکورد اضافه وجود دارد")
            except Exception as exc:
                raise FirmwareFileError(f"رکورد Motorola S-record شمارهٔ {idx} خراب است: {exc}") from exc
        if not termination_seen:
            raise FirmwareFileError("رکورد پایان فایل Motorola S-record (S7/S8/S9) وجود ندارد.")
        return "Motorola S-record", len(lines)

    raise FirmwareFileError(
        "فرمت فایل قابل تشخیص نیست. فقط Intel HEX (.hex/.ihx) یا Motorola S-record (.s19/.srec/.mot) مجاز است."
    )


class STLinkController:
    def __init__(
        self,
        mcu: str = DEFAULT_MCU,
        on_log: Optional[LogCallback] = None,
        stvp_cli_path: Optional[str] = None,
        stm8flash_path: Optional[str] = None,
        backend: str = "auto",
    ) -> None:
        self.mcu = (mcu or DEFAULT_MCU).strip()
        self.on_log = on_log or (lambda m: None)
        self.stvp_cli = self._valid_tool_path(stvp_cli_path) or discover_stvp()
        self.stm8flash = self._valid_tool_path(stm8flash_path) or discover_stm8flash()
        self.backend = backend.lower().strip()

    @staticmethod
    def _valid_tool_path(path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        candidate = Path(path).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        return shutil.which(path)

    def available(self) -> str:
        parts = []
        if self.stvp_cli:
            parts.append(f"STVP_CmdLine: {self.stvp_cli}")
        if self.stm8flash:
            parts.append(f"stm8flash: {self.stm8flash}")
        return " | ".join(parts) if parts else "No STM8 programmer found (install ST Visual Programmer / STVP_CmdLine.exe)"

    def flash_hex(self, firmware_path: str, timeout_s: float = FLASH_TIMEOUT_S) -> FlashResult:
        try:
            fmt, record_count = validate_firmware_file(firmware_path)
        except FirmwareFileError as exc:
            return FlashResult(False, str(exc))

        firmware = str(Path(firmware_path).expanduser().resolve())
        self._log(f"Firmware validated: {fmt}, {record_count} records, MCU={self.mcu}")
        backend = self.backend
        if backend == "auto":
            backend = "stvp" if self.stvp_cli else ("stm8flash" if self.stm8flash else "none")
        if backend == "stvp":
            if not self.stvp_cli:
                return FlashResult(False, "STVP_CmdLine.exe پیدا نشد؛ مسیر فایل اجرایی STVP را در برنامه وارد کن.")
            return self._flash_stvp(firmware, fmt, timeout_s)
        if backend == "stm8flash":
            if not self.stm8flash:
                return FlashResult(False, "stm8flash پیدا نشد.")
            return self._flash_stm8flash(firmware, timeout_s)
        return FlashResult(False, "هیچ پروگرامر معتبری انتخاب/پیدا نشده است.")

    def _stage_stvp_file(self, source: str, fmt: str) -> Tuple[Optional[str], Optional[str]]:
        """STVP v1.x has path parsing problems with spaces/non-ASCII paths.

        Copy the input to a short ASCII-only path before passing -FileProg.
        Returns (staged_path, error_message).
        """
        suffix = ".hex" if fmt == "Intel HEX" else ".s19"
        roots = [
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "CoolerPanelAutoTester" / "stage",
            Path(r"C:\CPAT"),
            Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "CPAT",
        ]
        for root in roots:
            try:
                root.mkdir(parents=True, exist_ok=True)
                text_root = str(root)
                if " " in text_root or not text_root.isascii():
                    continue
                staged = root / ("firmware" + suffix)
                shutil.copyfile(source, staged)
                return str(staged), None
            except (OSError, PermissionError):
                continue
        return None, (
            "نتوانستم فایل را در مسیر کوتاه ASCII برای STVP آماده کنم. "
            "دسترسی نوشتن به C:\\ProgramData\\CoolerPanelAutoTester یا C:\\CPAT را بررسی کن."
        )

    def _flash_stvp(self, firmware_path: str, fmt: str, timeout_s: float) -> FlashResult:
        assert self.stvp_cli
        staged, error = self._stage_stvp_file(firmware_path, fmt)
        if error or not staged:
            return FlashResult(False, error or "ساخت فایل موقت STVP ناموفق شد.")
        cmd = [
            self.stvp_cli,
            "-BoardName=ST-LINK",
            "-Port=USB",
            "-ProgMode=SWIM",
            f"-Device={self.mcu}",
            "-verbose",
            "-no_log",
            "-no_progress",
            "-no_loop",
            "-verif",
            "-no_progData",
            "-no_progOption",
            f"-FileProg={staged}",
        ]
        tool_dir = str(Path(self.stvp_cli).resolve().parent)
        result = self._run(cmd, timeout_s, action="STVP flash+verify", cwd=tool_dir, require_stvp_markers=True)
        self._log(f"STVP staged image: {staged}")
        return result

    def _flash_stm8flash(self, firmware_path: str, timeout_s: float) -> FlashResult:
        assert self.stm8flash
        cmd = [self.stm8flash, "-c", "stlinkv2", "-p", self.mcu.lower(), "-s", "flash", "-w", firmware_path]
        return self._run(cmd, timeout_s, action="stm8flash write", require_stvp_markers=False)

    def reset(self, timeout_s: float = 15.0) -> FlashResult:
        """STVP_CmdLine has no documented stand-alone run/reset switch for STM8.

        Keep the scenario as SKIP instead of sending the invalid `-rst` argument
        that the old tester used. A deterministic reset requires NRST control or
        a separately validated low-level SWIM implementation.
        """
        return FlashResult(
            False,
            "ریست مستقل با STVP_CmdLine در این نسخه قابل اجرا نیست؛ سوئیچ -rst در CLI مستند نشده است. "
            "برای ریست خودکار قابل اتکا باید NRST را به یک خروجی کنترل‌شده متصل کنیم یا ماژول SWIM سطح پایین اضافه شود.",
        )

    def _run(
        self,
        cmd: Sequence[str],
        timeout_s: float,
        action: str,
        cwd: Optional[str] = None,
        require_stvp_markers: bool = False,
    ) -> FlashResult:
        cmdline = subprocess.list2cmdline(list(cmd)) if os.name == "nt" else " ".join(repr(x) for x in cmd)
        self._log(f"{action}: {cmdline}")
        try:
            proc = subprocess.run(
                list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout_s, cwd=cwd, shell=False,
            )
            stdout, stderr = proc.stdout or "", proc.stderr or ""
            combined = stdout + "\n" + stderr
            if stdout.strip():
                self._log(stdout.strip())
            if stderr.strip():
                self._log(stderr.strip())
            lowered = combined.lower()
            fail_markers = (
                "argument error", "unknown argument", "cannot communicate with the tool",
                "programming program memory fails", "verifying program memory fails",
                "programming failed", "verification failed", "device is protected",
                "cannot connect", "no st-link detected", "target not detected",
            )
            if proc.returncode != 0:
                return FlashResult(False, f"{action} ناموفق شد (exit={proc.returncode})", cmdline, stdout, stderr)
            for marker in fail_markers:
                if marker in lowered:
                    return FlashResult(False, f"{action} در خروجی پروگرامر خطا گزارش کرد: {marker}", cmdline, stdout, stderr)
            if require_stvp_markers:
                program_ok = re.search(r"programming\s+program\s+memory\s+succeeds", lowered)
                verify_ok = re.search(r"verifying\s+program\s+memory\s+succeeds", lowered)
                if not program_ok or not verify_ok:
                    return FlashResult(
                        False,
                        "STVP با exit code صفر تمام شد، اما پیام‌های موفقیت Program و Verify هر دو پیدا نشدند؛ "
                        "نتیجه را موفق فرض نکردم. خروجی کامل در لاگ ثبت شده است.",
                        cmdline, stdout, stderr,
                    )
            return FlashResult(True, f"{action} و در صورت فعال بودن Verify با موفقیت پایان یافت.", cmdline, stdout, stderr)
        except FileNotFoundError:
            return FlashResult(False, f"ابزار پروگرامر پیدا نشد: {cmd[0]}", cmdline)
        except subprocess.TimeoutExpired as exc:
            return FlashResult(False, f"{action} پس از {timeout_s} ثانیه timeout شد.", cmdline, str(exc.stdout or ""), str(exc.stderr or ""))
        except Exception as exc:
            return FlashResult(False, f"خطای اجرای {action}: {exc}", cmdline)

    def _log(self, message: str) -> None:
        line = f"[ST-Link] {message}"
        log.info(line)
        try:
            self.on_log(line)
        except Exception:
            pass
