"""PySide6 UI for Cooler Panel Auto-Tester."""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.config import APP_VERSION, DEFAULT_BAUD, DEFAULT_MCU
from app.driver.fake_driver import FakeDriver
from app.engine.scenarios import TestContext, TestResult, TestStatus, build_default_suite, build_safe_suite
from app.engine.test_engine import TestEngine
from app.stlink.flash import STLinkController

try:
    from serial.tools import list_ports
except Exception:  # pragma: no cover
    list_ports = None


STATUS_COLORS = {
    TestStatus.PENDING: QColor("#EDF1F5"),
    TestStatus.RUNNING: QColor("#D6EAF8"),
    TestStatus.PASS: QColor("#D5F5E3"),
    TestStatus.FAIL: QColor("#FADBD8"),
    TestStatus.SKIP: QColor("#FCF3CF"),
}


class Worker(QObject):
    log = Signal(str)
    result = Signal(object)
    finished = Signal(list)

    def __init__(
        self,
        firmware_path: str,
        port: str,
        baud: int,
        mcu: str,
        stvp_cli_path: str,
        backend: str,
        suite_mode: str,
    ) -> None:
        super().__init__()
        self.firmware_path = firmware_path
        self.port = port
        self.baud = baud
        self.mcu = mcu
        self.stvp_cli_path = stvp_cli_path
        self.backend = backend
        self.suite_mode = suite_mode
        self._engine: Optional[TestEngine] = None

    @Slot()
    def run(self) -> None:
        driver: Optional[FakeDriver] = None
        try:
            def emit_log(message: str) -> None:
                self.log.emit(message)

            stlink = STLinkController(
                mcu=self.mcu,
                on_log=emit_log,
                stvp_cli_path=self.stvp_cli_path or None,
                backend=self.backend,
            )
            driver = FakeDriver(self.port, baud=self.baud, on_log=emit_log)
            engine = TestEngine(
                on_result=lambda item: self.result.emit(item),
                on_log=emit_log,
                on_finished=lambda items: self.finished.emit(items),
            )
            self._engine = engine
            engine.suite = build_safe_suite() if self.suite_mode == "safe" else build_default_suite()
            emit_log("حالت اجرا: " + ("ارتباط امن؛ بدون فلش و تست مداخله‌ای" if self.suite_mode == "safe" else "مجموعه کامل؛ شامل فلش و تست‌های مداخله‌ای"))
            context = TestContext(
                driver=driver,
                stlink=stlink,
                hex_path=self.firmware_path,
                log=emit_log,
                config={"mcu": self.mcu, "backend": self.backend},
            )
            engine.run(context)
        except Exception:
            self.log.emit(traceback.format_exc())
            self.finished.emit([])
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass

    def abort(self) -> None:
        if self._engine:
            self._engine.abort()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"تستر خودکار پنل کولر | نسخه {APP_VERSION}")
        self.resize(1180, 800)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._thread: Optional[QThread] = None
        self._worker: Optional[Worker] = None
        self._last_results: list[TestResult] = []
        self._last_report_path: Optional[Path] = None
        self._last_run_info: dict = {}
        self._build_ui()
        self._load_suite_rows()
        self.refresh_ports()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Firmware and STVP configuration
        fw_group = QGroupBox("فایل Firmware و پروگرامر ST-Link")
        fw_grid = QGridLayout(fw_group)
        self.firmware_edit = QLineEdit()
        self.firmware_edit.setPlaceholderText("فقط Intel HEX یا Motorola S-record؛ فایل IAR با پسوند OUT قابل فلش نیست")
        btn_firmware = QPushButton("انتخاب فایل")
        btn_firmware.clicked.connect(self.browse_firmware)
        fw_grid.addWidget(QLabel("Firmware:"), 0, 0)
        fw_grid.addWidget(self.firmware_edit, 0, 1)
        fw_grid.addWidget(btn_firmware, 0, 2)

        self.stvp_edit = QLineEdit()
        self.stvp_edit.setPlaceholderText("خالی بماند تا STVP_CmdLine.exe به‌صورت خودکار پیدا شود")
        btn_stvp = QPushButton("انتخاب STVP")
        btn_stvp.clicked.connect(self.browse_stvp)
        fw_grid.addWidget(QLabel("مسیر STVP_CmdLine:"), 1, 0)
        fw_grid.addWidget(self.stvp_edit, 1, 1)
        fw_grid.addWidget(btn_stvp, 1, 2)

        self.mcu_edit = QLineEdit(DEFAULT_MCU)
        self.mcu_edit.setMaximumWidth(180)
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("خودکار (اول STVP)", "auto")
        self.backend_combo.addItem("ST Visual Programmer", "stvp")
        self.backend_combo.addItem("stm8flash (در صورت نصب)", "stm8flash")
        fw_grid.addWidget(QLabel("مدل MCU:"), 2, 0)
        fw_grid.addWidget(self.mcu_edit, 2, 1)
        fw_grid.addWidget(self.backend_combo, 2, 2)
        layout.addWidget(fw_group)

        # Serial setup
        serial_group = QGroupBox("اتصال USB-Serial به درایور مجازی")
        serial_row = QHBoxLayout(serial_group)
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(260)
        self.port_combo.setEditable(False)
        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(1200, 921600)
        self.baud_spin.setValue(DEFAULT_BAUD)
        btn_refresh = QPushButton("به‌روزرسانی پورت‌ها")
        btn_refresh.clicked.connect(self.refresh_ports)
        serial_row.addWidget(QLabel("پورت COM:"))
        serial_row.addWidget(self.port_combo, 2)
        serial_row.addWidget(btn_refresh)
        serial_row.addWidget(QLabel("Baud:"))
        serial_row.addWidget(self.baud_spin)
        layout.addWidget(serial_group)

        # Actions
        action_row = QHBoxLayout()
        self.test_mode_combo = QComboBox()
        self.test_mode_combo.addItem("ارتباط امن (بدون فلش)", "safe")
        self.test_mode_combo.addItem("مجموعه کامل (فلش و تست مداخله‌ای)", "full")
        self.test_mode_combo.currentIndexChanged.connect(self._on_test_mode_changed)
        self.btn_start = QPushButton("شروع تست‌ها")
        self.btn_start.setMinimumWidth(130)
        self.btn_start.clicked.connect(self.start_tests)
        self.btn_stop = QPushButton("توقف")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_tests)
        self.btn_export = QPushButton("ذخیره گزارش JSON")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export_report)
        self.summary_label = QLabel("قبول: ۰ | مردود: ۰ | ردشده: ۰")
        self.summary_label.setStyleSheet("font-weight: bold; padding: 5px 10px;")
        action_row.addWidget(QLabel("حالت اجرا:"))
        action_row.addWidget(self.test_mode_combo)
        action_row.addWidget(self.btn_start)
        action_row.addWidget(self.btn_stop)
        action_row.addWidget(self.btn_export)
        action_row.addWidget(self.summary_label)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["شناسه", "سناریوی تست", "وضعیت", "پیام / جزئیات"])
        self.table.setColumnWidth(0, 75)
        self.table.setColumnWidth(1, 300)
        self.table.setColumnWidth(2, 100)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        splitter.addWidget(self.table)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        splitter.addWidget(self.log_view)
        splitter.setSizes([430, 270])
        layout.addWidget(splitter, 1)

        hint = QLabel(
            "Firmware این برد از USART1 روی PC2/PC3 با 9600 و 8N1 استفاده می‌کند. USART2 با 115200 مربوط به ESP است. "
            "حالت پیش‌فرض فقط ابزار، بازشدن COM و مشاهده تبادل UART را بررسی می‌کند؛ فلش و تست‌های مداخله‌ای فقط در حالت مجموعه کامل و پس از تأیید اجرا می‌شوند. نوع رابط خروجی برد و مدار جهت PE6 باید پیش از اتصال با شماتیک تأیید شود."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5D6D7E; padding: 6px; background: #F4F6F7; border-radius: 4px;")
        layout.addWidget(hint)

    def _load_suite_rows(self, suite_mode: Optional[str] = None) -> None:
        mode = suite_mode or self.test_mode_combo.currentData()
        suite = build_safe_suite() if mode == "safe" else build_default_suite()
        self.table.setRowCount(len(suite))
        for row, case in enumerate(suite):
            self.table.setItem(row, 0, QTableWidgetItem(case.test_id))
            self.table.setItem(row, 1, QTableWidgetItem(case.name))
            status = QTableWidgetItem(TestStatus.PENDING.value)
            status.setBackground(STATUS_COLORS[TestStatus.PENDING])
            self.table.setItem(row, 2, status)
            self.table.setItem(row, 3, QTableWidgetItem(case.description))
            self.table.setRowHeight(row, 34)

    def _on_test_mode_changed(self) -> None:
        if self._thread is None:
            self._load_suite_rows(self.test_mode_combo.currentData())

    def browse_firmware(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "انتخاب Firmware",
            "",
            "Firmware متنی (*.hex *.ihx *.s19 *.srec *.mot);;همه فایل‌ها (*.*)",
        )
        if path:
            if Path(path).suffix.lower() == ".out":
                QMessageBox.warning(
                    self,
                    "فرمت نامعتبر",
                    "فایل .out خروجی لینک‌شده IAR است. در تنظیمات Debug خروجی HEX را فعال کن و فایل Debug/Exe را انتخاب کن.",
                )
                return
            self.firmware_edit.setText(path)

    def browse_stvp(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "انتخاب STVP_CmdLine.exe",
            "",
            "ST Visual Programmer CLI (STVP_CmdLine.exe)",
        )
        if path:
            self.stvp_edit.setText(path)

    def refresh_ports(self) -> None:
        current = self.port_combo.currentData()
        self.port_combo.clear()
        if list_ports is None:
            self.port_combo.addItem("pyserial نصب نیست", "")
            return
        ports = list(list_ports.comports())
        for port in ports:
            self.port_combo.addItem(f"{port.device} — {port.description}", port.device)
        if not ports:
            self.port_combo.addItem("پورت سریال پیدا نشد", "")
        elif current:
            index = self.port_combo.findData(current)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)

    def append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")
        self.log_view.moveCursor(QTextCursor.End)

    def start_tests(self) -> None:
        port = self.port_combo.currentData()
        if not port:
            QMessageBox.warning(self, "پورت سریال", "یک COM Port معتبر انتخاب کن.")
            return
        mcu = self.mcu_edit.text().strip()
        if not mcu:
            QMessageBox.warning(self, "مدل میکرو", "مدل MCU را وارد کن؛ مقدار پیش‌فرض STM8L052R8 است.")
            return

        suite_mode = self.test_mode_combo.currentData()
        if suite_mode == "full":
            answer = QMessageBox.warning(
                self,
                "تأیید اجرای مجموعه کامل",
                "این حالت ممکن است Firmware انتخاب‌شده را روی STM8 فلش کند و تست‌های مداخله‌ای را اجرا کند. "
                "T05 با تزریق دستی POWER در IAR می‌تواند به فرمان راه‌اندازی موتور منجر شود؛ "
                "T08 نیز Fault آزمایشی تزریق می‌کند. فقط با فیکسچر ایمن و آمادگی کامل ادامه بده.\n\nادامه می‌دهی؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._load_suite_rows(suite_mode)
        self._last_results = []
        self._last_run_info = {
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "firmware": self.firmware_edit.text().strip(),
            "com_port": port,
            "baud": self.baud_spin.value(),
            "mcu": mcu,
            "stvp_cli": self.stvp_edit.text().strip(),
            "backend": self.backend_combo.currentData(),
            "suite_mode": suite_mode,
        }
        self.summary_label.setText("تست‌ها در حال اجرا هستند...")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_export.setEnabled(False)
        self.append_log("========== شروع مجموعه تست ==========")

        self._thread = QThread()
        self._worker = Worker(
            self.firmware_edit.text().strip(),
            port,
            self.baud_spin.value(),
            mcu,
            self.stvp_edit.text().strip(),
            self.backend_combo.currentData(),
            suite_mode,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.append_log)
        self._worker.result.connect(self.on_result)
        self._worker.finished.connect(self.on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def stop_tests(self) -> None:
        if self._worker:
            self._worker.abort()
            self.append_log("درخواست توقف ثبت شد؛ تست جاری پس از پایان timeout متوقف می‌شود.")

    def on_result(self, result: TestResult) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == result.test_id:
                status_item = QTableWidgetItem(result.status.value)
                status_item.setBackground(STATUS_COLORS.get(result.status, QColor("white")))
                self.table.setItem(row, 2, status_item)
                message = result.message
                if result.detail:
                    message += "\n" + result.detail
                self.table.setItem(row, 3, QTableWidgetItem(message))
                self.table.resizeRowToContents(row)
                break

    def on_finished(self, results: list) -> None:
        self._last_results = list(results)
        counts = {TestStatus.PASS: 0, TestStatus.FAIL: 0, TestStatus.SKIP: 0}
        for result in results:
            if result.status in counts:
                counts[result.status] += 1
        self.summary_label.setText(
            f"قبول: {counts[TestStatus.PASS]} | مردود: {counts[TestStatus.FAIL]} | ردشده: {counts[TestStatus.SKIP]}"
        )
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_export.setEnabled(bool(results))
        self.append_log("========== پایان مجموعه تست ==========")
        if results:
            report_path = self._save_report_automatically(results)
            if report_path:
                self._last_report_path = report_path
                self.append_log(f"گزارش JSON ذخیره شد: {report_path}")

    def _report_payload(self, results: list) -> dict:
        counts = {status.value: 0 for status in (TestStatus.PASS, TestStatus.FAIL, TestStatus.SKIP)}
        for result in results:
            if result.status.value in counts:
                counts[result.status.value] += 1
        return {
            "application": "Cooler Panel Auto-Tester",
            "version": APP_VERSION,
            "started_at": self._last_run_info.get("started_at"),
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "configuration": self._last_run_info,
            "summary": counts,
            "tests": [
                {
                    "id": result.test_id,
                    "name": result.name,
                    "status": result.status.value,
                    "message": result.message,
                    "detail": result.detail,
                }
                for result in results
            ],
        }

    def _save_report_automatically(self, results: list) -> Optional[Path]:
        try:
            project_root = Path(__file__).resolve().parents[2]
            report_dir = project_root / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            filename = "CoolerPanel_Report_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
            path = report_dir / filename
            path.write_text(json.dumps(self._report_payload(results), ensure_ascii=False, indent=2), encoding="utf-8")
            return path
        except Exception as exc:
            self.append_log(f"ذخیره خودکار گزارش ناموفق بود: {exc}")
            return None

    def export_report(self) -> None:
        if not self._last_results:
            return
        default_name = self._last_report_path.name if self._last_report_path else "CoolerPanel_Report.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "ذخیره گزارش تست",
            str((Path.cwd() / default_name)),
            "JSON (*.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            Path(path).write_text(
                json.dumps(self._report_payload(self._last_results), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.append_log(f"گزارش ذخیره شد: {path}")
            QMessageBox.information(self, "گزارش", "گزارش تست با موفقیت ذخیره شد.")
        except Exception as exc:
            QMessageBox.critical(self, "خطای ذخیره گزارش", str(exc))

    def _cleanup_thread(self) -> None:
        self._worker = None
        self._thread = None


def run_app() -> None:
    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
