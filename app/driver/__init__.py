from app.driver.protocol import DrvCmd, DrvFault, DrvReg, parse_packet, build_packet

# FakeDriver imports pyserial; keep optional for pure protocol unit tests
try:
    from app.driver.fake_driver import FakeDriver
except ImportError:  # pragma: no cover
    FakeDriver = None  # type: ignore

__all__ = [
    "FakeDriver",
    "DrvCmd",
    "DrvFault",
    "DrvReg",
    "parse_packet",
    "build_packet",
]
