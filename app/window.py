import subprocess

from pathlib import Path
from typing import Optional

from app.updater import UpdateChecker

from app.constants import APP_NAME, APP_VERSION

import keyring
from PySide6.QtCore import QSettings, QThread, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.constants import (
    CREDENTIAL_SERVICE,
    CREDENTIAL_USERNAME_KEY,
    SETTINGS_ORGANIZATION_KEY,
)
from app.dialogs import (
    AlternateStockDialog,
    AttachmentReviewDialog,
    LoginDialog,
)
from app.formatting import (
    canonical_duplicate_key,
    normalize_part_number,
)
from app.models import PartInput, PartResult
from app.parser import extract_parts_from_text, read_attachment_candidates
from app.vip import EPLBatchWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1150, 720)

        self.parts_by_key: dict[str, PartInput] = {}
        self.thread: Optional[QThread] = None
        self.worker: Optional[EPLBatchWorker] = None
        self.result_row_by_part: dict[str, int] = {}

        self.manual_input = QPlainTextEdit()
        self.manual_input.setPlaceholderText(
            "Paste or type part numbers here.\n"
            "Use one per line, or separate them with commas/spaces."
        )

        self.add_manual_button = QPushButton("Add Manual Entries")
        self.add_manual_button.clicked.connect(self.add_manual_entries)

        self.add_attachment_button = QPushButton("Add Attachment")
        self.add_attachment_button.clicked.connect(self.add_attachment)

        self.clear_button = QPushButton("Clear List")
        self.clear_button.clicked.connect(self.clear_parts)

        self.save_login_button = QPushButton("Save Login")
        self.save_login_button.clicked.connect(self.save_login)

        self.forget_login_button = QPushButton("Forget Login")
        self.forget_login_button.clicked.connect(self.forget_login)

        self.parts_table = QTableWidget(0, 3)
        self.parts_table.setHorizontalHeaderLabels(
            ["Search?", "Part Number", "Source"]
        )
        self.parts_table.horizontalHeader().setStretchLastSection(True)

        self.search_all_button = QPushButton("Search All")
        self.search_all_button.clicked.connect(self.start_batch_lookup)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.status_label = QLabel("Ready")

        self.results_table = QTableWidget(0, 10)
        self.results_table.setHorizontalHeaderLabels(
            [
                "Status",
                "Requested Part",
                "Current Part",
                "EPL Item ID",
                "Description",
                "Stock Status",
                "Lead Time",
                "Piqua Available",
                "Piqua Open PO",
                "Alternate Stock",
            ]
        )
        self.results_table.setColumnWidth(0, 45)
        self.results_table.horizontalHeader().setStretchLastSection(True)

        input_buttons = QHBoxLayout()
        input_buttons.addWidget(self.add_attachment_button)
        input_buttons.addWidget(self.add_manual_button)
        input_buttons.addWidget(self.clear_button)
        input_buttons.addStretch()
        input_buttons.addWidget(self.save_login_button)
        input_buttons.addWidget(self.forget_login_button)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Manual Entry"))
        left_layout.addWidget(self.manual_input)
        left_layout.addLayout(input_buttons)
        left_layout.addWidget(QLabel("Review Parts Before Searching"))
        left_layout.addWidget(self.parts_table)
        left_layout.addWidget(self.search_all_button)

        left_widget = QWidget()
        left_widget.setLayout(left_layout)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("EPL Results"))
        right_layout.addWidget(self.status_label)
        right_layout.addWidget(self.progress)
        right_layout.addWidget(self.results_table)

        right_widget = QWidget()
        right_widget.setLayout(right_layout)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([470, 680])

        self.setCentralWidget(splitter)

    def save_login(self) -> None:
        dialog = LoginDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        username, password, organization = dialog.values()
        if not username or not password:
            QMessageBox.warning(
                self,
                "Missing Login",
                "Enter both the VIP username and password.",
            )
            return

        keyring.set_password(CREDENTIAL_SERVICE, CREDENTIAL_USERNAME_KEY, username)
        keyring.set_password(CREDENTIAL_SERVICE, username, password)

        settings = QSettings("EPL Inventory Checker", "EPL Inventory Checker")
        settings.setValue(SETTINGS_ORGANIZATION_KEY, organization)

        QMessageBox.information(
            self,
            "Login Saved",
            "The VIP login was saved in Windows Credential Manager.",
        )

    def forget_login(self) -> None:
        username = keyring.get_password(CREDENTIAL_SERVICE, CREDENTIAL_USERNAME_KEY)
        if username:
            try:
                keyring.delete_password(CREDENTIAL_SERVICE, username)
            except keyring.errors.PasswordDeleteError:
                pass
            try:
                keyring.delete_password(CREDENTIAL_SERVICE, CREDENTIAL_USERNAME_KEY)
            except keyring.errors.PasswordDeleteError:
                pass

        QMessageBox.information(
            self,
            "Login Removed",
            "The saved VIP login was removed from Windows Credential Manager.",
        )

    def add_manual_entries(self) -> None:
        text = self.manual_input.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Nothing Entered", "Enter at least one part number.")
            return

        extracted = extract_parts_from_text(text, "Manual entry")
        if not extracted:
            QMessageBox.warning(
                self,
                "No Part Numbers Found",
                "No likely part numbers were detected. Try entering one part number per line.",
            )
            return

        added = self.add_parts(extracted)
        self.manual_input.clear()
        self.status_label.setText(f"Added {added} new part(s). Duplicates were removed.")

    def add_attachment(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Attachments",
            "",
            (
                "Supported files (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.txt);;"
                "PDF files (*.pdf);;"
                "Image files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff);;"
                "Text files (*.txt);;"
                "All files (*.*)"
            ),
        )
        if not paths:
            return

        total_added = 0

        for path_str in paths:
            path = Path(path_str)
            try:
                try:
                    candidates = read_attachment_candidates(path, self)
                except ValueError as exc:
                    QMessageBox.warning(self, "Unsupported File", str(exc))
                    continue

                dialog = AttachmentReviewDialog(candidates, path, self)
                if dialog.exec() == QDialog.Accepted:
                    total_added += self.add_parts(dialog.selected_parts())

            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Attachment Error",
                    f"Could not read {path.name}:\n\n{exc}",
                )

        self.status_label.setText(
            f"Added {total_added} new part(s) from attachments. Duplicates were removed."
        )


    def add_parts(self, parts: list[PartInput]) -> int:
        added = 0

        for part in parts:
            key = canonical_duplicate_key(part.display_number)
            if not key or key in self.parts_by_key:
                continue

            self.parts_by_key[key] = part
            row = self.parts_table.rowCount()
            self.parts_table.insertRow(row)

            include_item = QTableWidgetItem()
            include_item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
            )
            include_item.setCheckState(Qt.Checked)

            number_item = QTableWidgetItem(part.normalized_number)
            source_item = QTableWidgetItem(part.source)

            self.parts_table.setItem(row, 0, include_item)
            self.parts_table.setItem(row, 1, number_item)
            self.parts_table.setItem(row, 2, source_item)
            added += 1

        return added

    def clear_parts(self) -> None:
        self.parts_by_key.clear()
        self.parts_table.setRowCount(0)
        self.results_table.setRowCount(0)
        self.result_row_by_part.clear()
        self.progress.setValue(0)
        self.status_label.setText("Ready")

    def selected_parts(self) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()

        for row in range(self.parts_table.rowCount()):
            include_item = self.parts_table.item(row, 0)
            number_item = self.parts_table.item(row, 1)

            if not include_item or not number_item:
                continue
            if include_item.checkState() != Qt.Checked:
                continue

            number = normalize_part_number(number_item.text())
            key = canonical_duplicate_key(number)
            if key and key not in seen:
                selected.append(number)
                seen.add(key)

        return selected

    def start_batch_lookup(self) -> None:
        parts = self.selected_parts()
        if not parts:
            QMessageBox.warning(
                self,
                "No Parts Selected",
                "Add part numbers and leave at least one row checked.",
            )
            return

        self.set_controls_enabled(False)
        self.results_table.setRowCount(0)
        self.result_row_by_part.clear()
        self.progress.setMaximum(len(parts))
        self.progress.setValue(0)
        self.status_label.setText("Starting browser...")

        settings = QSettings("EPL Inventory Checker", "EPL Inventory Checker")
        organization = settings.value(SETTINGS_ORGANIZATION_KEY, "FEGNA")

        self.thread = QThread()
        self.worker = EPLBatchWorker(parts, organization)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.status.connect(self.status_label.setText)
        self.worker.progress.connect(self.update_progress)
        self.worker.result_ready.connect(self.add_result)
        self.worker.finished.connect(self.batch_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    def update_progress(self, current: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(current)

    def add_result(self, result: PartResult) -> None:
        if not result.requested_part and result.error:
            QMessageBox.critical(self, "Browser Error", result.error)
            return

        row = self.results_table.rowCount()
        self.results_table.insertRow(row)

        is_superseded = (
            result.current_part
            and EPLBatchWorker._part_key(result.requested_part)
            != EPLBatchWorker._part_key(result.current_part)
        )

        status_item = QTableWidgetItem("SUP" if is_superseded else "✓")
        status_item.setTextAlignment(Qt.AlignCenter)
        font = status_item.font()
        font.setBold(True)
        status_item.setFont(font)
        status_item.setForeground(
            QColor(220, 0, 0) if is_superseded else QColor(0, 150, 0)
        )
        self.results_table.setItem(row, 0, status_item)

        values = [
            result.requested_part,
            result.current_part or result.item_id or result.requested_part,
            result.item_id,
            result.description,
            result.stock_status,
            result.lead_time,
            result.piqua_available,
            result.piqua_open_po,
        ]

        for column, value in enumerate(values, start=1):
            item = QTableWidgetItem(value)
            if result.error and column == 4:
                item.setText(f"ERROR: {result.error}")
            self.results_table.setItem(row, column, item)

        piqua_quantity = EPLBatchWorker.parse_inventory_quantity(
            result.piqua_available
        )

        if piqua_quantity <= 0 and result.alternate_stock:
            count = len(result.alternate_stock)
            button = QPushButton(
                f"View {count} location{'s' if count != 1 else ''}"
            )
            button.setToolTip(
                "Show Hobart branches and service contractors with positive stock."
            )
            button.clicked.connect(
                lambda checked=False, current_result=result:
                    self.show_alternate_stock(current_result)
            )
            self.results_table.setCellWidget(row, 9, button)

        elif piqua_quantity <= 0 and not result.error:
            out_of_stock_item = QTableWidgetItem("OUT OF STOCK")
            out_of_stock_item.setToolTip(
                "Piqua has zero or negative stock and no other qualifying "
                "location shows positive Available Physical inventory."
            )
            self.results_table.setItem(row, 9, out_of_stock_item)

            out_of_stock_background = QColor(255, 205, 205)
            for column in range(1, self.results_table.columnCount()):
                item = self.results_table.item(row, column)
                if item is None:
                    item = QTableWidgetItem("")
                    self.results_table.setItem(row, column, item)
                item.setBackground(out_of_stock_background)

        else:
            self.results_table.setItem(row, 9, QTableWidgetItem("Piqua stocked"))

    def show_alternate_stock(self, result: PartResult) -> None:
        dialog = AlternateStockDialog(
            result.item_id or result.requested_part,
            result.alternate_stock,
            self,
        )
        dialog.exec()

    def batch_finished(self) -> None:
        self.set_controls_enabled(True)
        self.status_label.setText("Batch lookup finished.")

    def set_controls_enabled(self, enabled: bool) -> None:
        self.add_attachment_button.setEnabled(enabled)
        self.add_manual_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self.search_all_button.setEnabled(enabled)
        self.save_login_button.setEnabled(enabled)
        self.forget_login_button.setEnabled(enabled)

    def check_for_updates(self) -> None:
        update_checker = UpdateChecker()
        update = update_checker.check()

        if update is None:
            return

        message_box = QMessageBox(self)
        message_box.setWindowTitle("Update Available")
        message_box.setIcon(QMessageBox.Information)
        message_box.setText(
            f"A new version ({update.latest_version}) is available."
        )
        message_box.setInformativeText(
            "Would you like to download and install it now?"
        )

        install_button = message_box.addButton(
            "Download and Install",
            QMessageBox.AcceptRole,
        )
        message_box.addButton("Later", QMessageBox.RejectRole)

        message_box.exec()

        if message_box.clickedButton() != install_button:
            return

        try:
            self.status_label.setText("Downloading update...")

            installer_path = update_checker.download_installer(
                update.download_url,
                update.latest_version
            )
            
            subprocess.Popen(
                [
                    "cmd",
                    "/c",
                    (
                        'timeout /t 1 /nobreak > nul '
                        f'& start "" "{installer_path}"'
                    ),
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            QApplication.quit()

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Update Failed",
                f"The update could not be downloaded or opened.\n\n{exc}",
            )
            self.status_label.setText("Update failed.")
        
