"""Windows onboarding for the optional Google Docs caret helper."""
import winreg
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)


def show_docs_setup(parent):
    dialog = QDialog(parent)
    dialog.setWindowTitle("Set up Google Docs")
    dialog.setMinimumWidth(420)
    dialog.setMaximumWidth(540)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 20)
    layout.setSpacing(10)

    def paragraph(text, style=None):
        label = QLabel(text)
        label.setWordWrap(True)
        if style:
            label.setObjectName(style)
        layout.addWidget(label)

    paragraph("Google Docs in Chrome", "windowHeading")
    paragraph("The optional helper keeps the microphone beside your typing cursor. It shares the cursor position with Violet on this PC.")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\NativeMessagingHosts\org.violet.docs_caret") as key:
            manifest = Path(winreg.QueryValueEx(key, "")[0])
        folder = manifest.parent / "docs-caret"
        installed = manifest.is_file() and (folder / "manifest.json").is_file()
    except OSError:
        installed = False
    if installed:
        paragraph("1. Open chrome://extensions in the Chrome profile you use for Docs. Turn on Developer mode.")
        paragraph("2. Choose Load unpacked. Paste this folder path into the folder picker and select the folder.")
        row = QHBoxLayout()
        path = QLineEdit(str(folder))
        path.setReadOnly(True)
        path.setAccessibleName("Installed extension folder")
        row.addWidget(path, 1)
        copy = QPushButton("Copy path")
        def copy_path():
            QApplication.clipboard().setText(str(folder))
            copy.setText("Copied")
        copy.clicked.connect(copy_path)
        row.addWidget(copy)
        layout.addLayout(row)
        paragraph("3. Refresh your Google Doc. Click in its body and press Enter. The microphone should follow you to the new line.")
        paragraph("Already installed? When Violet updates the helper, click Reload on Violet Docs Caret in Chrome, then refresh your Doc.", "secondary")
    else:
        paragraph("Run the Violet Windows installer to add the helper and register it with Chrome. Then reopen these steps.")
    close = QPushButton("Done")
    close.clicked.connect(dialog.accept)
    layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
    dialog.exec()
