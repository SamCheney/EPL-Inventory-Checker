import sys

from PySide6.QtWidgets import QApplication

from app.window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    window.check_for_updates()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
