import sys
from pathlib import Path

# `main.py` is normally launched from src/simulation_python. Add the repository
# root so Python can resolve the locally built `orderbook` package and its
# pybind11 extension modules without requiring a separate installation step.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Market Simulation")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
