#!/usr/bin/env python3
import sys
import os
import json
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QTreeWidget, QTreeWidgetItem,
                             QLabel, QLineEdit, QMessageBox, QHeaderView, QGroupBox,
                             QFormLayout, QStatusBar, QStyledItemDelegate, QStyle,
                             QFrame)
from PyQt6.QtCore import Qt, QRectF, QSize, QTimer, QRect, QEvent
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QBrush

# Configuration
MOUNT_TARGET_BASE = Path("/media/mount")
BITLOCKER_FUSE_BASE = Path("/mnt/bitlocker_raw")


# ---------------------------------------------------------------------------
# Custom delegate: paints the └─ symbol BEFORE the icon in the Device column
# ---------------------------------------------------------------------------
class DeviceColumnDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        # 1. Draw the native background (handles selection/hover highlights correctly)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        is_child = index.parent().isValid()
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        icon = index.data(Qt.ItemDataRole.DecorationRole)

        left_padding = 4

        # 2. Draw Branch Symbol for children (Before the icon)
        if is_child:
            painter.setPen(QPen(QColor("#585b70")))
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRect(rect.x() + left_padding, rect.y(), 20, rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                "└─"
            )
            left_padding += 20

        # 3. Draw Icon
        if isinstance(icon, QIcon) and not icon.isNull():
            icon_size = 16
            icon_y = rect.y() + (rect.height() - icon_size) // 2
            icon_rect = QRect(rect.x() + left_padding, icon_y, icon_size, icon_size)
            icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Normal, QIcon.State.On)
            left_padding += icon_size + 8

        # 4. Draw Text
        if not is_child:
            painter.setPen(QPen(QColor("#89b4fa")))
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
        else:
            painter.setPen(QPen(QColor("#cdd6f4")))

        text_rect = QRect(rect.x() + left_padding, rect.y(), rect.width() - left_padding, rect.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + 28, max(size.height(), 28))


# ---------------------------------------------------------------------------
# Custom delegate: paints a colored pill badge for the "Status" column
# ---------------------------------------------------------------------------
class StatusBadgeDelegate(QStyledItemDelegate):

    _MOUNTED_BG = QColor("#1a3a2a")
    _MOUNTED_FG = QColor("#a6e3a1")
    _UNMOUNTED_BG = QColor("#2a2a3a")
    _UNMOUNTED_FG = QColor("#585b70")

    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text or text.strip() == "":
            super().paint(painter, option, index)
            return

        # Let Qt draw selection / hover / alternating-row backgrounds first
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        is_mounted = text == "Mounted"
        bg = self._MOUNTED_BG if is_mounted else self._UNMOUNTED_BG
        fg = self._MOUNTED_FG if is_mounted else self._UNMOUNTED_FG
        label = "Mounted" if is_mounted else "Unmounted"

        # Pill dimensions
        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(label) + 30      # dot + padding
        pill_h = 22
        pill_rect = QRectF(
            option.rect.x() + 10,
            option.rect.y() + (option.rect.height() - pill_h) / 2,
            text_w,
            pill_h,
        )

        # -- pill background --
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)

        # -- status dot --
        dot_r = 3.5
        dot_cx = pill_rect.x() + 11
        dot_cy = pill_rect.center().y()
        painter.setBrush(QBrush(fg))
        painter.drawEllipse(QRectF(dot_cx - dot_r, dot_cy - dot_r,
                                   dot_r * 2, dot_r * 2))

        # -- label text --
        text_rect = QRectF(dot_cx + dot_r + 6, pill_rect.y(),
                           pill_rect.width() - 24, pill_h)
        painter.setPen(QPen(fg))
        painter.drawText(text_rect,
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         label)
        painter.restore()

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        # Increased minimum width to 160 to ensure space for the toggle expand button in header
        return QSize(max(base.width(), 160), max(base.height(), 38))


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------
class DiskMountManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Advanced Disk Mount Manager")

        # --- Set Window Icon ---
        script_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(script_dir, "disk-icon.png")
        self.setWindowIcon(QIcon(icon_path))

        self.setMinimumSize(960, 720)

        # Internal state
        self._is_busy = False
        self._status_timer = None
        self._status_delegate = StatusBadgeDelegate(self)
        self._device_delegate = DeviceColumnDelegate(self)

        self._init_ui()
        self._apply_stylesheet()
        self.refresh_partitions()

    # ------------------------------------------------------------------
    # Icon helper
    # ------------------------------------------------------------------
    @staticmethod
    def _themed_icon(*names):
        """Return the first available theme icon from *names*."""
        for name in names:
            icon = QIcon.fromTheme(name)
            if not icon.isNull():
                return icon
        return QIcon()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 12)
        root.setSpacing(16)

        # ── Header ────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(10)

        icon_label = QLabel()
        hdd_icon = self._themed_icon("drive-harddisk", "drive-harddisk-symbolic")
        if not hdd_icon.isNull():
            icon_label.setPixmap(hdd_icon.pixmap(26, 26))
        header.addWidget(icon_label)

        title = QLabel("Storage Devices")
        title.setObjectName("header_title")
        header.addWidget(title)
        header.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("btn_refresh")
        self.refresh_btn.setIcon(
            self._themed_icon("view-refresh", "view-refresh-symbolic"))
        self.refresh_btn.setFixedWidth(130)
        self.refresh_btn.setToolTip("Rescan block devices")
        self.refresh_btn.clicked.connect(self.refresh_partitions)
        header.addWidget(self.refresh_btn)

        root.addLayout(header)

        # ── Tree widget (data grid) ──────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(
            ["Device / Partition", "Size", "File System", "Status"])
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.tree.header().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
                
        # Assign custom delegates
        self.tree.setItemDelegateForColumn(0, self._device_delegate)
        self.tree.setItemDelegateForColumn(3, self._status_delegate)
        
        self.tree.setAlternatingRowColors(True)
        self.tree.setIndentation(24)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self.update_ui_state)
        self.tree.itemDoubleClicked.connect(self.handle_double_click)
        root.addWidget(self.tree)

        # ── Toggle Expand All Button inside Header ───────────────────
        self.toggle_expand_btn = QPushButton("Collapse All", self.tree.header())
        self.toggle_expand_btn.setObjectName("btn_toggle_expand")
        self.toggle_expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_expand_btn.clicked.connect(self.on_toggle_expand_clicked)
        
        # Install event filter to keep it positioned on the far right
        self.tree.header().installEventFilter(self)
        
        self.tree.itemExpanded.connect(self.update_expand_button_state)
        self.tree.itemCollapsed.connect(self.update_expand_button_state)

        # ── Bottom controls ──────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.setSpacing(20)

        # -- Left: credentials & settings --
        settings_grp = QGroupBox("Mount Settings and Credentials")
        settings_lay = QFormLayout(settings_grp)
        settings_lay.setContentsMargins(18, 24, 18, 18)
        settings_lay.setSpacing(14)
        settings_lay.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.sudo_input = QLineEdit()
        self.sudo_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.sudo_input.setPlaceholderText("Sudo password (required)...")

        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Normal)
        self.key_input.setPlaceholderText("Required for BitLocker only...")
        self.key_input.setEnabled(False)

        settings_lay.addRow("Admin Password:", self.sudo_input)
        settings_lay.addRow("BitLocker Key:", self.key_input)

        # -- Right: actions --
        actions_grp = QGroupBox("Selected Device Actions")
        actions_lay = QVBoxLayout(actions_grp)
        actions_lay.setContentsMargins(18, 24, 18, 18)
        actions_lay.setSpacing(10)

        self.mount_btn = QPushButton("Mount / Decrypt")
        self.mount_btn.setObjectName("btn_mount")
        self.mount_btn.setIcon(
            self._themed_icon("media-mount", "emblem-unlocked",
                              "emblem-unlocked-symbolic"))
        self.mount_btn.setEnabled(False)
        self.mount_btn.setToolTip("Mount or decrypt the selected partition")
        self.mount_btn.clicked.connect(self.handle_mount)

        self.unmount_btn = QPushButton("Unmount Selected")
        self.unmount_btn.setObjectName("btn_unmount")
        self.unmount_btn.setIcon(
            self._themed_icon("media-eject", "media-eject-symbolic"))
        self.unmount_btn.setEnabled(False)
        self.unmount_btn.setToolTip("Unmount the selected partition")
        self.unmount_btn.clicked.connect(self.handle_unmount)

        actions_lay.addWidget(self.mount_btn)
        actions_lay.addWidget(self.unmount_btn)
        actions_lay.addStretch()

        # Danger-zone visual separator
        sep_row = QHBoxLayout()
        sep_row.setSpacing(8)
        line_l = QFrame()
        line_l.setFrameShape(QFrame.Shape.HLine)
        line_l.setObjectName("danger_sep")
        dz_label = QLabel("DANGER ZONE")
        dz_label.setObjectName("danger_zone_label")
        dz_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line_r = QFrame()
        line_r.setFrameShape(QFrame.Shape.HLine)
        line_r.setObjectName("danger_sep")
        sep_row.addWidget(line_l, 1)
        sep_row.addWidget(dz_label, 0)
        sep_row.addWidget(line_r, 1)
        actions_lay.addLayout(sep_row)

        self.unmount_all_btn = QPushButton("Unmount All")
        self.unmount_all_btn.setObjectName("btn_danger")
        self.unmount_all_btn.setIcon(
            self._themed_icon("process-stop", "process-stop-symbolic",
                              "edit-clear-all"))
        self.unmount_all_btn.setToolTip(
            "Force unmount all managed mount points")
        self.unmount_all_btn.clicked.connect(self.handle_unmount_all)
        actions_lay.addWidget(self.unmount_all_btn)

        bottom.addWidget(settings_grp, 3)
        bottom.addWidget(actions_grp, 2)
        root.addLayout(bottom)

        # ── Status bar ───────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._status_label = QLabel("Ready. Select a partition to begin.")
        self._status_label.setContentsMargins(4, 0, 0, 0)
        self.status_bar.addPermanentWidget(self._status_label, 1)

    # ------------------------------------------------------------------
    # Expand All / Collapse All Handling Logic
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        if obj == self.tree.header() and event.type() == QEvent.Type.Resize:
            self._reposition_toggle_button()
        return super().eventFilter(obj, event)

    def _reposition_toggle_button(self):
        header = self.tree.header()
        btn_w = 85
        btn_h = 22
        # Right aligned with a small gap
        x = header.width() - btn_w - 6
        y = (header.height() - btn_h) // 2
        if y < 0: y = 0
        self.toggle_expand_btn.setGeometry(x, y, btn_w, btn_h)

    def on_toggle_expand_clicked(self):
        # Block signals to prevent redundant checking loops
        self.tree.blockSignals(True)
        if self.toggle_expand_btn.text() == "Collapse All":
            self.tree.collapseAll()
            self.toggle_expand_btn.setText("Expand All")
        else:
            self.tree.expandAll()
            self.toggle_expand_btn.setText("Collapse All")
        self.tree.blockSignals(False)

    def update_expand_button_state(self):
        any_expanded = False
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            # Check if at least one parent item is expanded and has children
            if item.childCount() > 0 and item.isExpanded():
                any_expanded = True
                break
                
        if any_expanded:
            self.toggle_expand_btn.setText("Collapse All")
        else:
            self.toggle_expand_btn.setText("Expand All")

    # ------------------------------------------------------------------
    # Stylesheet (Catppuccin Macchiato-inspired dark theme)
    # ------------------------------------------------------------------
    def _apply_stylesheet(self):
        self.setStyleSheet("""
            /* ── Base ─────────────────────────────────────────────── */
            QMainWindow {
                background-color: #1e1e2e;
            }
            QWidget {
                color: #cdd6f4;
                font-family: "Inter", "Cantarell", "Noto Sans", "Ubuntu",
                             sans-serif;
                font-size: 13px;
            }

            /* ── Header title ─────────────────────────────────────── */
            QLabel#header_title {
                font-size: 18px;
                font-weight: bold;
                color: #cdd6f4;
                padding: 0;
            }

            /* ── Tree widget ──────────────────────────────────────── */
            QTreeWidget {
                background-color: #181825;
                alternate-background-color: #1c1c30;
                color: #cdd6f4;
                border: 1px solid #313244;
                border-radius: 8px;
                padding: 4px;
                outline: none;
                selection-background-color: transparent;
                show-decoration-selected: 1; /* Fixes hover arrow disappearing while maintaining selection background */
            }
            QTreeWidget::item {
                padding: 6px 4px;
                min-height: 28px;
                border: none;
                margin: 1px 0px; 
            }
            QTreeWidget::item:selected {
                background-color: #313244;
            }
            QTreeWidget::item:hover:!selected {
                background-color: #252538;
            }

            /* ── Header view (column headers) ─────────────────────── */
            QHeaderView::section {
                background-color: #11111b;
                color: #a6adc8;
                padding: 10px 8px;
                font-weight: bold;
                font-size: 11px;
                border: none;
                border-bottom: 2px solid #313244;
            }

            /* ── Toggle Expand All Button inside Header ───────────── */
            QPushButton#btn_toggle_expand {
                background-color: #1e1e2e;
                color: #a6adc8;
                border: 1px solid #313244;
                border-radius: 4px;
                font-size: 11px;
                padding: 2px 6px;
                min-height: 0px;
                font-weight: normal;
            }
            QPushButton#btn_toggle_expand:hover {
                background-color: #313244;
                color: #cdd6f4;
            }
            QPushButton#btn_toggle_expand:pressed {
                background-color: #45475a;
            }

            /* ── Group boxes ──────────────────────────────────────── */
            QGroupBox {
                border: 1px solid #313244;
                border-radius: 10px;
                margin-top: 18px;
                padding-top: 16px;
                font-weight: bold;
                background-color: #181825;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 16px;
                top: 0px; 
                color: #89b4fa;
                padding: 0 8px;
                background-color: #1e1e2e; 
                border-radius: 0px;
            }

            /* ── Danger-zone separator ────────────────────────────── */
            QFrame#danger_sep {
                background-color: #45273a;
                max-height: 1px;
                border: none;
            }
            QLabel#danger_zone_label {
                color: #f38ba8;
                font-size: 10px;
                font-weight: bold;
                padding: 0 6px;
            }

            /* ── Line edits ───────────────────────────────────────── */
            QLineEdit {
                padding: 10px 14px;
                min-height: 20px;
                background-color: #11111b;
                border: 1px solid #313244;
                border-radius: 8px;
                color: #cdd6f4;
                font-size: 13px;
                selection-background-color: #45475a;
            }
            QLineEdit:focus {
                border: 1px solid #89b4fa;
            }
            QLineEdit:disabled {
                background-color: #252537;
                color: #585b70;
                border-color: #252537;
            }

            /* ── Buttons – base ───────────────────────────────────── */
            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                padding: 10px 18px;
                min-height: 22px;
                border-radius: 8px;
                border: 1px solid transparent;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #45475a;
                border-color: #585b70;
            }
            QPushButton:pressed {
                background-color: #585b70;
            }
            QPushButton:disabled {
                background-color: #1a1a2e;
                color: #45475a;
                border-color: transparent;
            }

            /* Refresh (blue tint) */
            QPushButton#btn_refresh {
                background-color: #253555;
                color: #89b4fa;
                border: 1px solid #2e4a72;
            }
            QPushButton#btn_refresh:hover {
                background-color: #2e4a72;
            }
            QPushButton#btn_refresh:pressed {
                background-color: #1a2a42;
            }

            /* Mount (green tint) */
            QPushButton#btn_mount {
                background-color: #24453a;
                color: #a6e3a1;
                border: 1px solid #2f6050;
            }
            QPushButton#btn_mount:hover {
                background-color: #2f6050;
            }
            QPushButton#btn_mount:pressed {
                background-color: #1a3328;
            }
            QPushButton#btn_mount:disabled {
                background-color: #1a1a2e;
                color: #45475a;
                border-color: transparent;
            }

            /* Unmount (amber tint) */
            QPushButton#btn_unmount {
                background-color: #4a3325;
                color: #fab387;
                border: 1px solid #6a4835;
            }
            QPushButton#btn_unmount:hover {
                background-color: #6a4835;
            }
            QPushButton#btn_unmount:pressed {
                background-color: #35251a;
            }
            QPushButton#btn_unmount:disabled {
                background-color: #1a1a2e;
                color: #45475a;
                border-color: transparent;
            }

            /* Danger (red tint) */
            QPushButton#btn_danger {
                background-color: #4a2535;
                color: #f38ba8;
                border: 1px solid #6a3548;
            }
            QPushButton#btn_danger:hover {
                background-color: #6a3548;
            }
            QPushButton#btn_danger:pressed {
                background-color: #351a28;
            }

            /* ── Status bar ───────────────────────────────────────── */
            QStatusBar {
                background-color: #11111b;
                border-top: 1px solid #313244;
                min-height: 30px;
                padding: 0 8px;
            }

            /* ── Tooltips ─────────────────────────────────────────── */
            QToolTip {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }

            /* ── Scrollbar ────────────────────────────────────────── */
            QScrollBar:vertical {
                background-color: #181825;
                width: 10px;
                border-radius: 5px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background-color: #313244;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #45475a;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }

            /* ── Message boxes ────────────────────────────────────── */
            QMessageBox {
                background-color: #1e1e2e;
            }
            QMessageBox QLabel {
                color: #cdd6f4;
                font-size: 13px;
            }
            QMessageBox QPushButton {
                min-width: 80px;
                padding: 8px 16px;
            }
        """)

    # ------------------------------------------------------------------
    # Status-bar helper (colored messages with optional auto-clear)
    # ------------------------------------------------------------------
    def _show_status(self, message, level="info", timeout=0):
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None

        palette = {
            "info":    ("#a6adc8", "normal"),
            "success": ("#a6e3a1", "bold"),
            "error":   ("#f38ba8", "bold"),
            "warning": ("#fab387", "bold"),
        }
        color, weight = palette.get(level, palette["info"])
        self._status_label.setStyleSheet(
            f"color: {color}; font-weight: {weight}; font-size: 12px; "
            f"padding: 2px 4px;"
        )
        self._status_label.setText(message)

        if timeout > 0:
            self._status_timer = QTimer(self)
            self._status_timer.setSingleShot(True)
            self._status_timer.timeout.connect(
                lambda: self._show_status("Ready."))
            self._status_timer.start(timeout)

    # ------------------------------------------------------------------
    # Busy-state helpers (wait cursor + button disable)
    # ------------------------------------------------------------------
    def _begin_operation(self, message):
        self._is_busy = True
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.mount_btn.setEnabled(False)
        self.unmount_btn.setEnabled(False)
        self.unmount_all_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self._show_status(message)
        QApplication.processEvents()

    def _end_operation(self):
        if self._is_busy:
            QApplication.restoreOverrideCursor()
            self._is_busy = False
        self.refresh_btn.setEnabled(True)
        self.unmount_all_btn.setEnabled(True)
        self.update_ui_state()

    # ------------------------------------------------------------------
    # UI state
    # ------------------------------------------------------------------
    def update_ui_state(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            self.mount_btn.setEnabled(False)
            self.unmount_btn.setEnabled(False)
            self.key_input.setEnabled(False)
            return

        item = selected_items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)

        if not data:
            self.mount_btn.setEnabled(False)
            self.unmount_btn.setEnabled(False)
            self.key_input.setEnabled(False)
            return

        is_mounted = bool(data.get("is_mounted", False))
        self.mount_btn.setEnabled(not is_mounted)
        self.unmount_btn.setEnabled(is_mounted)

        fstype = data.get("fstype", "")
        if "bitlocker" in fstype and not is_mounted:
            self.key_input.setEnabled(True)
            self.key_input.setPlaceholderText("Enter BitLocker Key...")
        else:
            self.key_input.setEnabled(False)
            self.key_input.clear()
            self.key_input.setPlaceholderText("Not required for this format...")

    # ==================================================================
    # Backend helpers
    # ==================================================================
    def run_root_cmd(self, cmd_list, check=True):
        sudo_pwd = self.sudo_input.text()
        is_root = (os.geteuid() == 0)

        if is_root:
            result = subprocess.run(cmd_list, text=True, capture_output=True)
            if check and result.returncode != 0:
                raise RuntimeError(f"Command failed:\n{result.stderr.strip()}")
            return result

        # Attempt 1: Try sudo -S (Uses the password from your custom UI box)
        if sudo_pwd:
            sudo_cmd = ['sudo', '-S', '-p', ''] + cmd_list
            result = subprocess.run(sudo_cmd, input=sudo_pwd + '\n', text=True, capture_output=True)

            if result.returncode == 0:
                return result

            err_msg = result.stderr.strip().lower()
            if "incorrect password" in err_msg or "authentication failure" in err_msg:
                raise PermissionError("Incorrect sudo password provided in the UI.")

            # If it failed for a reason OTHER than the tmux sandbox trap, stop here.
            if "no new privileges" not in err_msg:
                if check:
                    raise RuntimeError(f"Command failed:\n{result.stderr.strip()}")
                return result

        # Attempt 2 (Fallback): If sudo was blocked by tmux/sandbox, use pkexec.
        pkexec_cmd = ['pkexec'] + cmd_list
        result = subprocess.run(pkexec_cmd, text=True, capture_output=True)

        if check and result.returncode != 0:
            raise RuntimeError(f"Command failed (or prompt cancelled):\n{result.stderr.strip()}")

        return result

    def run_root_cmd_safe(self, cmd_list):
        """Run a command without raising exceptions - prints errors to stderr."""
        sudo_pwd = self.sudo_input.text()
        is_root = (os.geteuid() == 0)

        if is_root:
            subprocess.run(cmd_list, text=True, capture_output=True)
            return

        # Attempt 1: UI Password
        if sudo_pwd:
            sudo_cmd = ['sudo', '-S', '-p', ''] + cmd_list
            result = subprocess.run(sudo_cmd, input=sudo_pwd + '\n', text=True, capture_output=True)
            if result.returncode == 0:
                return

            if "no new privileges" not in result.stderr.strip().lower():
                print(f"[ERROR] stderr: {result.stderr.strip()}", file=sys.stderr)
                return

        # Attempt 2 (Fallback): pkexec GUI prompt
        pkexec_cmd = ['pkexec'] + cmd_list
        result = subprocess.run(pkexec_cmd, text=True, capture_output=True)
        if result.returncode != 0:
            print(f"[ERROR] Command failed: {' '.join(cmd_list)}", file=sys.stderr)
            print(f"[ERROR] stderr: {result.stderr.strip()}", file=sys.stderr)

    def open_path(self, path):
        try:
            subprocess.Popen(["xdg-open", path])
            self._show_status(f"Opened file manager at {path}", "info", 5000)
        except Exception as e:
            print(f"[ERROR] Could not open file manager for {path}: {e}", file=sys.stderr)
            QMessageBox.warning(self, "Error", f"Could not open file manager: {e}")

    def handle_double_click(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data: return
        target_mnt = MOUNT_TARGET_BASE / data['name']
        if os.path.ismount(str(target_mnt)):
            self.open_path(str(target_mnt))

    # ==================================================================
    # Core operations
    # ==================================================================
    def refresh_partitions(self):
        self.tree.clear()
        self._show_status("Refreshing partition list...")
        QApplication.processEvents()
        try:
            cmd = ['lsblk', '-J', '-e', '7', '-o', 'NAME,SIZE,FSTYPE,TYPE,MOUNTPOINT']
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)

            disk_icon = self._themed_icon(
                "drive-harddisk", "drive-harddisk-symbolic")
            part_icon = self._themed_icon(
                "drive-removable-media", "media-flash",
                "drive-removable-media-symbolic")
            lock_icon = self._themed_icon(
                "channel-insecure-symbolic", "emblem-locked",
                "security-high-symbolic")

            for dev in data.get('blockdevices', []):
                dev_name = dev.get('name', '')
                if dev_name.startswith(('sr', 'loop')): continue
                children = dev.get('children', [])
                if any(c.get('mountpoint') in ['/', '/boot'] for c in children): continue

                disk_item = QTreeWidgetItem(self.tree)
                disk_item.setIcon(0, disk_icon)
                disk_item.setText(0, dev_name)
                disk_item.setText(1, dev.get('size', ''))
                disk_item.setText(2, "Physical Disk")

                for part in children:
                    part_item = QTreeWidgetItem(disk_item)
                    fstype = (part.get('fstype') or "Unknown").lower()

                    # Use lock icon for BitLocker, generic drive for others
                    if "bitlocker" in fstype and not lock_icon.isNull():
                        part_item.setIcon(0, lock_icon)
                    else:
                        part_item.setIcon(0, part_icon)

                    part_item.setText(0, part['name'])
                    part_item.setText(1, part.get('size', ''))
                    part_item.setText(2, fstype)

                    target_path = MOUNT_TARGET_BASE / part['name']
                    is_active = os.path.ismount(str(target_path)) or part.get('mountpoint')

                    # Plain text — the StatusBadgeDelegate handles rendering
                    part_item.setText(3, "Mounted" if is_active else "Unmounted")

                    part_item.setData(0, Qt.ItemDataRole.UserRole, {
                        "path": f"/dev/{part['name']}",
                        "fstype": fstype,
                        "name": part['name'],
                        "is_mounted": is_active,
                        "mount_target": str(target_path) if is_active else None
                    })

            # Block signals while expanding initially to prevent spamming
            self.tree.blockSignals(True)
            self.tree.expandAll()
            self.tree.blockSignals(False)
            self.update_expand_button_state()
            
            self._show_status("Partition list updated.", "info", 5000)
        except Exception as e:
            print(f"[ERROR] Refresh partitions failed: {e}", file=sys.stderr)
            QMessageBox.critical(self, "Refresh Error", f"Failed to list block devices:\n{e}")
            self._show_status("Error refreshing partitions.", "error")

        self.update_ui_state()

    def handle_mount(self):
        item = self.tree.currentItem()
        if not item or not item.parent(): return
        data = item.data(0, Qt.ItemDataRole.UserRole)

        mode = "ro"
        uid, gid = os.getuid(), os.getgid()

        self._begin_operation(f"Mounting {data['name']}...")
        try:
            target_mnt = MOUNT_TARGET_BASE / data['name']
            self.run_root_cmd(['mkdir', '-p', str(target_mnt)])

            if "bitlocker" in data['fstype']:
                key = self.key_input.text().strip()
                if not key:
                    QMessageBox.warning(self, "Key Required", "Enter BitLocker key to proceed.")
                    return

                fuse_mnt = BITLOCKER_FUSE_BASE / f"{data['name']}_raw"
                self.run_root_cmd(['mkdir', '-p', str(fuse_mnt)])
                self.run_root_cmd(["umount", "-l", str(fuse_mnt)], check=False)

                dislocker_cmd = ["dislocker", "-V", data['path'], f"-p{key}", "--", str(fuse_mnt), "-o", "allow_other"]
                self.run_root_cmd(dislocker_cmd)

                mount_opts = f"loop,{mode},uid={uid},gid={gid},umask=022"
                self.run_root_cmd(["mount", "-o", mount_opts, str(fuse_mnt / "dislocker-file"), str(target_mnt)])
            else:
                mount_opts = f"{mode},uid={uid},gid={gid},umask=022" if any(x in data['fstype'] for x in ['ntfs', 'fat', 'exfat']) else mode
                self.run_root_cmd(["mount", "-o", mount_opts, data['path'], str(target_mnt)])

            self.refresh_partitions()
            self._show_status(f"Successfully mounted to {target_mnt}", "success", 8000)
            self.open_path(str(target_mnt))

        except Exception as e:
            print(f"[ERROR] Mount failed for {data['name']}: {e}", file=sys.stderr)
            self._show_status("Mount failed.", "error", 8000)
            QMessageBox.critical(self, "Mount Error", str(e))
        finally:
            self._end_operation()

    def handle_unmount(self):
        item = self.tree.currentItem()
        if not item or not item.parent(): return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        target_mnt = MOUNT_TARGET_BASE / data['name']
        fuse_mnt = BITLOCKER_FUSE_BASE / f"{data['name']}_raw"

        self._begin_operation(f"Unmounting {data['name']}...")
        try:
            self.run_root_cmd_safe(["umount", "-l", str(target_mnt)])
            if "bitlocker" in data['fstype']:
                self.run_root_cmd_safe(["umount", "-l", str(fuse_mnt)])

            self.run_root_cmd_safe(["rmdir", str(target_mnt)])
            self.run_root_cmd_safe(["rmdir", str(fuse_mnt)])

            self.refresh_partitions()
            self._show_status(f"Successfully unmounted {data['name']}.", "success", 8000)
        except Exception as e:
            print(f"[ERROR] Unmount failed for {data['name']}: {e}", file=sys.stderr)
            self._show_status("Unmount failed.", "error", 8000)
            QMessageBox.critical(self, "Error", str(e))
        finally:
            self._end_operation()

    def handle_unmount_all(self):
        reply = QMessageBox.question(self, "Confirm Clean", "Are you sure you want to force unmount all managed disks?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._begin_operation("Cleaning all managed mounts...")
        try:
            for base in [MOUNT_TARGET_BASE, BITLOCKER_FUSE_BASE]:
                if base.exists():
                    for child in base.iterdir():
                        self.run_root_cmd_safe(["umount", "-l", str(child)])
                        self.run_root_cmd_safe(["rmdir", str(child)])

            self.refresh_partitions()
            self._show_status("All mount points cleaned successfully.", "success", 8000)
        except Exception as e:
            print(f"[ERROR] Clean all failed: {e}", file=sys.stderr)
            self._show_status("Clean all failed.", "error", 8000)
            QMessageBox.critical(self, "Error", str(e))
        finally:
            self._end_operation()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Force the Fusion style to ensure we get expand/collapse arrows 
    # instead of OS-default boxes.
    app.setStyle("Fusion")

    # --- Set App-level Icon and Wayland identifier ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(script_dir, "disk-icon.png")
    app.setWindowIcon(QIcon(icon_path))
    app.setDesktopFileName("disk-mount-manager.desktop") # Must match your .desktop file name

    window = DiskMountManager()
    window.show()
    sys.exit(app.exec())