"""06 · Local NRC400 dashboard. Script only; one owner per serial port."""

import logging
from pathlib import Path

from tark_nrc import create_chiller
from tark_nrc.gui import run_dashboard

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.toml"
SIMULATE = False
INTERVAL_S = 1.0
CSV_PATH = None  # Optional NEW path, e.g. Path("outputs/dashboard_session.csv").
HTTP_PORT = 8057
SERIAL_TRACE = False  # True logs exact TX/RX and device identity to this terminal.

logging.basicConfig(
    level=logging.DEBUG if SERIAL_TRACE else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
chiller = create_chiller(None if SIMULATE else CONFIG_PATH, simulate=SIMULATE)
print(f"Backend: {chiller.transport.backend}; controls become available after Connect.")
print(f"Open http://127.0.0.1:{HTTP_PORT}, then press Connect. Starts disconnected.")
print("Use Shut down GUI or Ctrl+C to close the server and serial connection.")
print("Browser closure leaves the server running. Software shutdown retains equipment state.")
try:
    run_dashboard(chiller, csv_path=CSV_PATH, interval_s=INTERVAL_S, port=HTTP_PORT)
except KeyboardInterrupt:
    print("Dashboard interrupted.")
finally:
    chiller.close()  # Also covers an error before the dashboard session is created.
print("Server and connection closed. Follow the equipment shutdown procedure separately.")
