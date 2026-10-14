"""Optional TOML configuration, shared by CLI and lab scripts."""

import tomllib
from pathlib import Path

from .controller import Chiller
from .simulator import Simulator
from .transport import SerialSettings, SerialTransport


def create_chiller(config_path=None, *, simulate=False, port=None, allow_writes=False):
    """Build an unopened controller; explicit CLI options override TOML settings."""
    config = {}
    if config_path is not None:
        with Path(config_path).open("rb") as stream:
            config = tomllib.load(stream)
    unknown = set(config) - {"serial", "control"}
    if unknown:
        raise ValueError(f"Unknown configuration sections: {sorted(unknown)}")
    serial_config = dict(config.get("serial", {}))
    control = dict(config.get("control", {}))
    if set(control) - {"allow_writes", "min_c", "max_c", "expected_serial_number"}:
        raise ValueError("Unknown control setting")
    if port is not None:
        serial_config["port"] = port
    if simulate:
        serial_config["port"] = "SIMULATOR"
    settings = SerialSettings(**serial_config)
    if allow_writes:
        control["allow_writes"] = True
    transport = Simulator(settings) if simulate else SerialTransport(settings)
    return Chiller(transport, **control)
