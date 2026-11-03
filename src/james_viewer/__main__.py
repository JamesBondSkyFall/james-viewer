"""James Viewer's entry point. Heavy modules load only when a document needs them."""

import ctypes
import sys


def main() -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    from james_viewer import APP_NAME
    from james_viewer.ui import theme
    from james_viewer.ui.mainwindow import MainWindow

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JamesViewer.DocumentViewer")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(theme.sans(13))
    app.setStyleSheet(theme.STYLE_SHEET)
    window = MainWindow()
    window.show()
    files = [value for value in sys.argv[1:] if not value.startswith("-")]
    if files:
        QTimer.singleShot(0, lambda: window.open_paths(files))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
