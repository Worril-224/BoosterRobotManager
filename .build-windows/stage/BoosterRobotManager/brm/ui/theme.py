"""界面主题（QSS）。

整体走深色 VS Code 风格：深底、低饱和度分隔线、蓝色强调色，
长时间盯机器人日志时更省眼。
"""

from __future__ import annotations

APP_QSS = """
* {
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", sans-serif;
    font-size: 13px;
    color: #d7dce3;
}

QMainWindow, QDialog {
    background-color: #1e1f22;
}

QWidget#Root {
    background-color: #1e1f22;
}

/* 左侧机器人面板 */
QFrame#Sidebar {
    background-color: #26282c;
    border-right: 1px solid #34373c;
}

QLabel#SidebarTitle {
    font-size: 15px;
    font-weight: 600;
    color: #f0f3f7;
}

QLabel#MutedLabel {
    color: #8b929c;
}

QLabel#PasswordHint {
    color: #ffb454;
    background-color: #3a2f1c;
    border: 1px solid #6b5428;
    border-radius: 6px;
    padding: 8px 10px;
}

QLabel#PasswordOk {
    color: #7ec699;
    background-color: #22301f;
    border: 1px solid #365232;
    border-radius: 6px;
    padding: 6px 10px;
}

QListWidget {
    background-color: #202226;
    border: 1px solid #34373c;
    border-radius: 8px;
    padding: 4px;
    outline: none;
}

QListWidget::item {
    padding: 9px 10px;
    border-radius: 6px;
    margin: 2px 1px;
}

QListWidget::item:selected {
    background-color: #2d4a6b;
    color: #ffffff;
}

QListWidget::item:hover:!selected {
    background-color: #2a2d33;
}

/* 按钮 */
QPushButton {
    background-color: #33373d;
    border: 1px solid #434850;
    border-radius: 6px;
    padding: 6px 14px;
    color: #e6eaf0;
}

QPushButton:hover {
    background-color: #3d424a;
}

QPushButton:pressed {
    background-color: #2c3037;
}

QPushButton:disabled {
    color: #6b7280;
    background-color: #2a2c30;
    border-color: #34373c;
}

QPushButton#PrimaryButton {
    background-color: #2563eb;
    border: 1px solid #3b82f6;
    color: #ffffff;
    font-weight: 600;
}

QPushButton#PrimaryButton:hover {
    background-color: #3b82f6;
}

QPushButton#PrimaryButton:disabled {
    background-color: #2b3a55;
    border-color: #33415c;
    color: #8b9bb4;
}

QPushButton#DangerButton {
    color: #ff8a8a;
    border-color: #5a3338;
}

QPushButton#FlatButton {
    background-color: transparent;
    border: 1px solid transparent;
    color: #9aa3af;
    padding: 5px 8px;
}

QPushButton#FlatButton:hover {
    background-color: #2f3238;
    color: #e6eaf0;
}

/* 输入框 */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget {
    background-color: #1b1d20;
    border: 1px solid #3a3e45;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: #2f5fa8;
    selection-color: #ffffff;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {
    border-color: #3b82f6;
}

QLineEdit#PathEdit {
    font-family: "SF Mono", "Consolas", "Menlo", monospace;
}

QComboBox::drop-down {
    border: none;
    width: 18px;
}

QComboBox QAbstractItemView {
    background-color: #26282c;
    border: 1px solid #3a3e45;
    selection-background-color: #2d4a6b;
}

/* 表格 */
QTableWidget {
    gridline-color: #2c2f34;
    alternate-background-color: #212327;
    selection-background-color: #2d4a6b;
    selection-color: #ffffff;
}

QHeaderView::section {
    background-color: #2a2d32;
    color: #aeb6c2;
    border: none;
    border-right: 1px solid #34373c;
    border-bottom: 1px solid #34373c;
    padding: 6px;
    font-weight: 600;
}

QTableCornerButton::section {
    background-color: #2a2d32;
    border: none;
}

/* 进度条 */
QProgressBar {
    background-color: #1b1d20;
    border: 1px solid #3a3e45;
    border-radius: 6px;
    height: 18px;
    text-align: center;
    color: #cbd5e1;
}

QProgressBar::chunk {
    background-color: #2563eb;
    border-radius: 5px;
}

/* 分组框 / 分割线 */
QGroupBox {
    border: 1px solid #34373c;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
    color: #c7cede;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

QFrame#SectionHeader {
    border-bottom: 1px solid #34373c;
}

QSplitter::handle {
    background-color: #2a2d32;
}

QSplitter::handle:horizontal {
    width: 3px;
}

QSplitter::handle:vertical {
    height: 3px;
}

/* 日志区 */
QPlainTextEdit#LogView {
    background-color: #17181a;
    border: 1px solid #34373c;
    border-radius: 8px;
    font-family: "SF Mono", "Consolas", "Menlo", monospace;
    font-size: 12px;
    padding: 8px;
}

QLabel#StageLabel {
    color: #9aa3af;
}

QLabel#ProjectPath {
    color: #8ab4f8;
    font-family: "SF Mono", "Consolas", "Menlo", monospace;
}

QLabel#StatusRunning {
    color: #7ec699;
    font-weight: 600;
}

QLabel#StatusStopped {
    color: #8b929c;
    font-weight: 600;
}

QLabel#StatusError {
    color: #ff8a8a;
    font-weight: 600;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #3f444c;
    border-radius: 5px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background: #50565f;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0px;
    background: transparent;
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: #3f444c;
    border-radius: 5px;
    min-width: 28px;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    width: 0px;
    background: transparent;
}

QStatusBar {
    background-color: #26282c;
    border-top: 1px solid #34373c;
    color: #9aa3af;
}

QToolTip {
    background-color: #2b2e33;
    color: #e6eaf0;
    border: 1px solid #434850;
    padding: 4px 6px;
}

QCheckBox {
    spacing: 6px;
}

QMessageBox {
    background-color: #1e1f22;
}

QMenuBar {
    background-color: #1e1f22;
    color: #d7dce3;
}

QMenuBar::item {
    padding: 4px 10px;
    background: transparent;
}

QMenuBar::item:selected {
    background-color: #2d4a6b;
    border-radius: 4px;
}

QMenu {
    background-color: #26282c;
    border: 1px solid #3a3e45;
}

QMenu::item {
    padding: 6px 24px;
}

QMenu::item:selected {
    background-color: #2d4a6b;
}

QMenu::separator {
    height: 1px;
    background-color: #3a3e45;
    margin: 4px 8px;
}

QTabWidget::pane {
    border: 1px solid #34373c;
    border-radius: 8px;
    background-color: #202226;
}

QTabBar::tab {
    background-color: #26282c;
    border: 1px solid #34373c;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 7px 16px;
    color: #9aa3af;
}

QTabBar::tab:selected {
    background-color: #2d4a6b;
    color: #ffffff;
}
"""
