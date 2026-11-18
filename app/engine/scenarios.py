"""Built-in automated regression scenarios for the real panel protocol."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List

from app.config import BOOT_DELAY_S
from app.driver.protocol import DrvCmd, DrvFault, DrvReg, response_command, response_matches


class TestStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    PASS = "Pass"
    FAIL = "Fail"
    SKIP = "Skip"


@dataclass
class TestResult:
    test_id: str
    name: str
    status: TestStatus
    message: str = ""
    detail: str = ""


@dataclass
class TestCase:
    test_id: str
    name: str
    description: str
    run: Callable[..., TestResult]
    enabled: bool = True


@dataclass
class TestContext:
    driver: Any
    stlink: Any
    hex_path: str
    log: Callable[[str], None]
    config: Dict[str, Any] = field(default_factory=dict)


def _pass(tc: TestCase, msg: str = "", detail: str = "") -> TestResult:
    return TestResult(tc.test_id, tc.name, TestStatus.PASS, msg, detail)


def _fail(tc: TestCase, msg: str, detail: str = "") -> TestResult:
    return TestResult(tc.test_id, tc.name, TestStatus.FAIL, msg, detail)


def _skip(tc: TestCase, msg: str) -> TestResult:
    return TestResult(tc.test_id, tc.name, TestStatus.SKIP, msg)


def test_tools_available(ctx: TestContext, tc: TestCase) -> TestResult:
    info = ctx.stlink.available()
    ctx.log(info)
    backend = getattr(ctx.stlink, "backend", "auto")
    if backend == "stvp" and not getattr(ctx.stlink, "stvp_cli", None):
        return _fail(tc, "حالت STVP انتخاب شده اما STVP_CmdLine.exe پیدا نشد؛ مسیر آن را در UI وارد کن.")
    if backend == "stm8flash" and not getattr(ctx.stlink, "stm8flash", None):
        return _fail(tc, "حالت stm8flash انتخاب شده اما فایل اجرایی آن پیدا نشد.")
    if backend == "auto" and not getattr(ctx.stlink, "stvp_cli", None) and not getattr(ctx.stlink, "stm8flash", None):
        return _fail(tc, "هیچ پروگرامر STM8 پیدا نشد؛ ST Visual Programmer را نصب کن یا مسیر آن را بده.")
    return _pass(tc, info)


def test_serial_open(ctx: TestContext, tc: TestCase) -> TestResult:
    if not ctx.driver.is_open:
        try:
            ctx.driver.open()
        except Exception as exc:
            return _fail(tc, f"باز کردن پورت سریال ناموفق بود: {exc}")
    return _pass(tc, f"پورت {ctx.driver.port_name} با {ctx.driver.baud} baud باز شد.")


def test_flash_hex(ctx: TestContext, tc: TestCase) -> TestResult:
    if not ctx.hex_path:
        return _skip(tc, "فایل Firmware انتخاب نشده است.")
    result = ctx.stlink.flash_hex(ctx.hex_path)
    if result.ok:
        ctx.log(f"فلش تمام شد؛ {BOOT_DELAY_S:.1f} ثانیه برای پایدار شدن ارتباط پس از خروج پروگرامر صبر می‌کنم.")
        time.sleep(BOOT_DELAY_S)
        return _pass(tc, result.message, result.cmdline)
    return _fail(tc, result.message, result.cmdline + "\n" + result.stdout + "\n" + result.stderr)


def test_reset(ctx: TestContext, tc: TestCase) -> TestResult:
    result = ctx.stlink.reset()
    if result.ok:
        return _pass(tc, result.message)
    return _skip(tc, result.message)


def test_driver_hears_traffic(ctx: TestContext, tc: TestCase) -> TestResult:
    """Wait for a complete request/response pair from the panel."""
    ctx.driver.reset_history()
    ctx.log("در انتظار فریم معتبر USART1 از پنل (حداکثر ۸ ثانیه)...")
    exchange = ctx.driver.wait_for_exchange(
        lambda x: x.request.crc_ok and x.response is not None and response_matches(x.request, x.response),
        timeout_s=8.0,
    )
    if exchange is None:
        return _fail(
            tc,
            "هیچ تبادل معتبر UART دریافت نشد. سیم‌بندی TX/RX، GND، سطح 3.3V، baud=9600 و جدا بودن درایور واقعی را بررسی کن. همچنین ممکن است پنل در Standby/خاموش باشد.",
        )
    req = exchange.request
    return _pass(tc, f"{req.command_name} / reg={req.reg.name if req.reg else 'unknown'} دریافت و پاسخ ارسال شد.")


def test_read_status_exchange(ctx: TestContext, tc: TestCase) -> TestResult:
    """Observe a READ_STATUS transaction and verify its matching response."""
    start_index = len(ctx.driver.exchange_snapshot())
    exchange = ctx.driver.wait_for_exchange(
        lambda x: x.request.command == DrvCmd.READ_STATUS
        and x.request.reg == DrvReg.FAULT
        and x.response is not None
        and response_matches(x.request, x.response),
        timeout_s=10.0,
        start_index=start_index,
    )
    if exchange is None:
        return _fail(tc, "در ۱۰ ثانیه هیچ تبادل کامل READ_STATUS / RSP_READ_STATUS دیده نشد.")
    return _pass(tc, f"READ_STATUS پاسخ داده شد؛ Fault mask=0x{exchange.response.data:04X}.")


def test_inject_overtemp_fault(ctx: TestContext, tc: TestCase) -> TestResult:
    """Verify the Fake Driver returns the exact OVER_TEMP status bit on the bus."""
    start_index = len(ctx.driver.exchange_snapshot())
    ctx.config["fault_test_start_index"] = start_index
    was_running = bool(ctx.driver.get_reg(DrvReg.STOP_START))
    ctx.driver.inject_fault(DrvFault.OVER_TEMP)
    exchange = ctx.driver.wait_for_exchange(
        lambda x: x.request.command == DrvCmd.READ_STATUS
        and x.request.reg == DrvReg.FAULT
        and x.response is not None
        and response_matches(x.request, x.response)
        and (x.response.data & int(DrvFault.OVER_TEMP)) != 0,
        timeout_s=10.0,
        start_index=start_index,
    )
    if exchange is None:
        return _fail(
            tc,
            "پنل در مهلت تست وضعیت Fault را نخواند یا پاسخ READ_STATUS فاقد بیت OVER_TEMP بود.",
            f"Fake Driver fault register=0x{ctx.driver.get_reg(DrvReg.FAULT):04X}",
        )
    msg = f"پاسخ READ_STATUS با بیت OVER_TEMP=1 ارسال شد (data=0x{exchange.response.data:04X})."
    if was_running:
        start_after_fault = len(ctx.driver.exchange_snapshot())
        stopped = ctx.driver.wait_for_exchange(
            lambda x: x.request.command == DrvCmd.WRITE
            and x.request.reg == DrvReg.STOP_START
            and x.request.data == 0
            and x.response is not None
            and response_matches(x.request, x.response)
            and x.response.data == 0,
            timeout_s=6.0,
            start_index=start_after_fault,
        )
        if stopped is None:
            return _fail(tc, msg + " با وجود فعال بودن Run، فرمان STOP در UART مشاهده نشد.")
        msg += " سپس فرمان STOP نیز روی UART مشاهده شد."
    else:
        msg += " پنل پیش از تزریق در حالت Run مشاهده نشد؛ واکنش توقف موتور در این اجرا قابل ارزیابی نبود."
    return _pass(tc, msg)


def test_clear_fault(ctx: TestContext, tc: TestCase) -> TestResult:
    """Wait for firmware to issue CLEAR_STATUS; the fake driver clears only then."""
    start_index = int(ctx.config.get("fault_test_start_index", len(ctx.driver.exchange_snapshot())))
    exchange = ctx.driver.wait_for_exchange(
        lambda x: x.request.command == DrvCmd.CLEAR_STATUS
        and x.request.reg == DrvReg.FAULT
        and x.response is not None
        and response_matches(x.request, x.response)
        and x.response.data == 0,
        timeout_s=8.0,
        start_index=start_index,
    )
    if exchange is None:
        if (ctx.driver.get_reg(DrvReg.FAULT) & 0xFFFF) == 0:
            return _skip(tc, "پس از تزریق، Fault پیش از مشاهدهٔ CLEAR_STATUS پاک شده است؛ این اجرا برای ارزیابی فرمان کافی نیست.")
        return _fail(tc, "فرمان CLEAR_STATUS از پنل دریافت نشد یا Fake Driver پاسخ نداد.")
    if exchange.response.command != response_command(DrvCmd.CLEAR_STATUS):
        return _fail(tc, f"کد پاسخ CLEAR_STATUS نادرست است: 0x{exchange.response.command:02X}")
    if ctx.driver.get_reg(DrvReg.FAULT) != 0:
        return _fail(tc, "پس از CLEAR_STATUS رجیستر Fault در Fake Driver صفر نشد.")
    return _pass(tc, "پنل فرمان CLEAR_STATUS فرستاد؛ Fake Driver Fault را پاک کرد و RSP_CLEAR_STATUS پاسخ داد.")


def test_silence_driver_timeout(ctx: TestContext, tc: TestCase) -> TestResult:
    """Suppress replies and verify that the panel retries the same transaction."""
    ctx.driver.reset_history()
    start_index = len(ctx.driver.exchange_snapshot())
    ctx.driver.set_respond(False)
    try:
        first = ctx.driver.wait_for_exchange(
            lambda x: x.response is None and x.request.crc_ok,
            timeout_s=3.0,
            start_index=start_index,
        )
        if first is None:
            return _fail(tc, "در بازهٔ قطع پاسخ، هیچ فرمان معتبر از پنل دریافت نشد.")

        # Keep the simulated driver silent long enough for the firmware's
        # response timeout/retry path to run (nominal timeout is 35 ms).
        deadline = time.monotonic() + 2.0
        matching = []
        signature = (first.request.command, first.request.app_id, first.request.reg_index, first.request.data)
        while time.monotonic() < deadline:
            matching = [
                x for x in ctx.driver.exchange_snapshot()[start_index:]
                if x.response is None and x.request.crc_ok
                and (x.request.command, x.request.app_id, x.request.reg_index, x.request.data) == signature
            ]
            if len(matching) >= 2:
                break
            time.sleep(0.05)
    finally:
        ctx.driver.set_respond(True)

    if len(matching) < 2:
        return _fail(
            tc,
            "در زمان قطع پاسخ، درخواست تکراری متناظر مشاهده نشد؛ مسیر Retry تأیید نشد.",
            f"درخواست اولیه: {first.request.raw.hex(' ')}؛ تعداد درخواست‌های همسان بدون پاسخ: {len(matching)}",
        )
    return _pass(
        tc,
        f"درایور به مدت حدود ۲ ثانیه پاسخ نداد و پنل همان درخواست را {len(matching)} بار ارسال کرد؛ Retry روی UART تأیید شد.",
        "این تست به‌تنهایی بیت داخلی Connection_Fault را اثبات نمی‌کند؛ آن بیت در Firmware خارج از محدودهٔ دادهٔ ۱۶ بیتی این فریم است.",
    )


def test_read_dc_bus_value(ctx: TestContext, tc: TestCase) -> TestResult:
    """Seed a fake register and wait for the panel to read that exact value."""
    value = 330
    ctx.driver.set_reg(DrvReg.DC_BUS_VOLTAGE, value)
    start_index = len(ctx.driver.exchange_snapshot())
    exchange = ctx.driver.wait_for_exchange(
        lambda x: x.request.command == DrvCmd.READ
        and x.request.reg == DrvReg.DC_BUS_VOLTAGE
        and x.response is not None
        and response_matches(x.request, x.response),
        timeout_s=8.0,
        start_index=start_index,
    )
    if exchange is None:
        return _skip(tc, "پنل در این اجرا رجیستر DC_BUS را نخواند؛ برای این تست صفحهٔ پارامترها/مسیر خواندن ولتاژ را فعال کن.")
    if exchange.response.data != value:
        return _fail(tc, f"مقدار پاسخ DC_BUS نادرست است: انتظار {value}، دریافت {exchange.response.data}.")
    return _pass(tc, f"پنل رجیستر DC_BUS را خواند و مقدار {value} از طریق USART پاسخ داده شد.")


def test_power_startup_sequence(ctx: TestContext, tc: TestCase) -> TestResult:
    """Observe the real startup writes after the operator injects POWER in IAR."""
    start_index = len(ctx.driver.exchange_snapshot())
    ctx.log(
        "برای شروع تست، در IAR Live Watch مقدار keyHandle.key[3].type=1 و "
        "keyHandle.key[3].step=2 را تنظیم کن و اجازه بده APP_Handle_Key اجرا شود. "
        "تا ۳۰ ثانیه منتظر توالی راه‌اندازی می‌مانم."
    )
    start_exchange = ctx.driver.wait_for_exchange(
        lambda x: x.request.command == DrvCmd.WRITE
        and x.request.reg == DrvReg.STOP_START
        and x.request.data == 1
        and x.response is not None
        and response_matches(x.request, x.response)
        and x.response.data == x.request.data,
        timeout_s=30.0,
        start_index=start_index,
    )
    if start_exchange is None:
        return _fail(
            tc,
            "فرمان START معتبر از پنل دریافت نشد.",
            "تزریق RAM را در IAR بررسی کن؛ همچنین باید شرط آماده‌شدن OW/ADC برقرار باشد و setpoint صفر نباشد.",
        )

    exchanges = ctx.driver.exchange_snapshot()[start_index:]
    successful_writes = [
        (i, x) for i, x in enumerate(exchanges)
        if x.request.command == DrvCmd.WRITE
        and x.response is not None
        and response_matches(x.request, x.response)
        and x.response.data == x.request.data
    ]

    # The firmware startup path writes pump/valve, control mode, setpoint,
    # then STOP_START. Address aliases resolve to PUMP and SPEED canonically.
    required = [DrvReg.PUMP, DrvReg.CTRL_MODE_SELECT, DrvReg.SPEED, DrvReg.STOP_START]
    ordered = []
    cursor = -1
    for reg in required:
        found = next(
            ((i, x) for i, x in successful_writes
             if i > cursor and x.request.reg == reg
             and (reg != DrvReg.STOP_START or x.request.data == 1)),
            None,
        )
        if found is None:
            return _fail(
                tc,
                f"فرمان START دریافت شد، اما پاسخ معتبر به نوشتن رجیستر {reg.name} در توالی راه‌اندازی پیدا نشد.",
                "هر چهار مرحله باید پاسخ متناظر با CRC صحیح و دادهٔ echo شده داشته باشند.",
            )
        cursor, _ = found
        ordered.append(found[1])

    return _pass(
        tc,
        "تزریق دستی POWER به توالی واقعی راه‌اندازی رسید؛ فرمان‌های PUMP/VALVE، CTRL_MODE_SELECT، SPEED و STOP_START=1 همگی پاسخ معتبر و متناظر گرفتند.",
        "این تست مسیر نرم‌افزار و تبادل Fake Driver را بررسی می‌کند؛ روشن‌شدن فیزیکی موتور/پمپ باید جداگانه روی برد تأیید شود.",
    )



def build_default_suite() -> List[TestCase]:
    return [
        TestCase("T01", "شناسایی ابزار پروگرام", "کشف STVP_CmdLine یا stm8flash", test_tools_available),
        TestCase("T02", "اتصال USB-Serial", "باز شدن UART شبیه‌ساز درایور", test_serial_open),
        TestCase("T03", "فلش و Verify Firmware", "برنامه‌ریزی حافظه Flash با ST-Link/SWIM", test_flash_hex),
        TestCase("T04", "Reset سخت‌افزاری هدف", "ریست مستقل STM8 پس از فلش", test_reset),
        TestCase("T05", "توالی راه‌اندازی POWER", "تزریق دستی RAM در IAR و اعتبارسنجی فرمان‌های واقعی راه‌اندازی", test_power_startup_sequence),
        TestCase("T06", "دریافت فریم از پنل", "تبادل معتبر درخواست/پاسخ روی USART1", test_driver_hears_traffic),
        TestCase("T07", "پاسخ به READ_STATUS", "اعتبارسنجی پاسخ وضعیت درایور", test_read_status_exchange),
        TestCase("T08", "تزریق Fault دمای موتور", "برگرداندن بیت OVER_TEMP در READ_STATUS و بررسی STOP در صورت Run", test_inject_overtemp_fault),
        TestCase("T09", "پاک‌سازی Fault", "مشاهده فرمان CLEAR_STATUS صادرشده از Firmware", test_clear_fault),
        TestCase("T10", "قطع پاسخ Fake Driver", "مشاهده درخواست‌های پنل هنگام عدم پاسخ درایور", test_silence_driver_timeout),
        TestCase("T11", "خواندن رجیستر DC Bus", "پاسخ‌دهی مقدار 330 به درخواست READ پنل", test_read_dc_bus_value),
    ]


def build_safe_suite() -> List[TestCase]:
    """Non-destructive default suite: tool discovery, COM open, and passive UART observation.

    Flashing, IAR-triggered POWER startup, fault injection, and timeout manipulation
    remain available only in the explicitly selected full suite.
    """
    safe_ids = {"T01", "T02", "T06"}
    return [case for case in build_default_suite() if case.test_id in safe_ids]
