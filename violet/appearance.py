"""Shared native-widget styling for Violet's desktop surfaces."""

WINDOW_STYLE = """
QWidget {
    background: #f8f7f5; color: #25232b;
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 14px;
}
QLabel#windowHeading { font-size: 30px; font-weight: 600; }
QLabel#sectionHeading { font-size: 16px; font-weight: 600; }
QLabel#secondary, QLabel#violetNote { color: #605d69; font-size: 13px; }
QLabel#violetStatus {
    color: #423451; background: #ede9f3; border-left: 3px solid #796194;
    padding: 8px 12px; font-weight: 600;
}
QPushButton, QComboBox, QPlainTextEdit {
    background: #ffffff; border: 1px solid #bbb7c0;
    border-radius: 5px; padding: 6px 12px;
}
QPushButton:hover, QComboBox:hover { background: #f1eef5; }
QPushButton:pressed { background: #e7e1ed; }
QPushButton#primary, QPushButton#violetDownloadModel:enabled {
    background: #58436c; color: white; border-color: #58436c; padding: 6px 22px;
}
QPushButton#primary:hover, QPushButton#violetDownloadModel:enabled:hover { background: #463456; }
QPushButton#textButton { color: #59416e; background: transparent; border-color: transparent; padding: 3px 2px; }
QPushButton#textButton:hover { text-decoration: underline; }
QCheckBox { spacing: 9px; padding: 4px 0; }
QPushButton:focus, QComboBox:focus, QPlainTextEdit:focus, QPushButton#textButton:focus,
QPushButton#primary:focus { border: 2px solid #79549a; }
QPushButton:disabled, QComboBox:disabled, QCheckBox:disabled { color: #736e78; }
"""
