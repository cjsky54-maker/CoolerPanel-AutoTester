"""Hardware defaults for the Cooler Panel V4.x tester."""

from __future__ import annotations

DEFAULT_BAUD = 9600
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1
SERIAL_TIMEOUT_S = 0.05

PACKET_LEN = 8
PANEL_TX_ADDRESS = 0x01
IMC_CODE_VERSION = 1

BOOT_DELAY_S = 2.0
FLASH_TIMEOUT_S = 90.0
RESPONSE_TIMEOUT_S = 0.2
INTER_CMD_DELAY_S = 0.03

# Confirmed from FW_CoolerPanel_V4.x.ewp (Debug configuration).
DEFAULT_MCU = "STM8L052R8"
STVP_CLI_NAME = "STVP_CmdLine.exe"
STM8FLASH_CLI = "stm8flash"
DEFAULT_MAP_PATH = ""

APP_VERSION = "0.5.1"
