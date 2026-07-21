from pathlib import Path
from typing import Optional

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.constants import SETTINGS_ORGANIZATION_KEY
from app.formatting import format_hobart_part_number, normalize_part_number
from app.models import AlternateStockLocation, PartInput, ReviewCandidate


class AlternateStockDialog(QDialog):
    def __init__(
        self,
        part_number: str,
        locations: list[AlternateStockLocation],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Alternate Stock - {part_number}")
        self.resize(760, 430)

        heading = QLabel(
            f"<b>{part_number}</b> is not currently available from Piqua. "
            "These EPL locations show positive Available Physical stock."
        )
        heading.setWordWrap(True)

        note = QLabel(
            "Red EPL rows are listed as Hobart Branches. Dark-gray EPL rows "
            "are listed as Service Contractors."
        )
        note.setWordWrap(True)

        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(
            ["Location Type", "Site", "Site Name", "Warehouse", "Available"]
        )
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        for location in locations:
            row = table.rowCount()
            table.insertRow(row)
            values = [
                location.location_type,
                location.site_code,
                location.site_name,
                location.warehouse,
                str(location.available),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 4:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, column, item)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addWidget(table)
        layout.addWidget(buttons)
        self.setLayout(layout)

class AttachmentReviewDialog(QDialog):
    def __init__(
        self,
        candidates: list[ReviewCandidate],
        preview_path: Optional[Path] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Review Detected Parts")
        self.resize(1200, 760)
        self.preview_path = preview_path

        self.preview_label = QLabel("No preview available")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumWidth(430)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.preview_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Import?", "Qty", "Part Number", "Description / OCR Line", "Source", "Confidence"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

        for candidate in candidates:
            self.add_candidate(candidate)

        add_row_button = QPushButton("Add Blank Row")
        add_row_button.clicked.connect(self.add_blank_row)

        delete_button = QPushButton("Delete Selected")
        delete_button.clicked.connect(self.delete_selected)

        select_all_button = QPushButton("Select All")
        select_all_button.clicked.connect(lambda: self.set_all_checked(True))

        select_none_button = QPushButton("Select None")
        select_none_button.clicked.connect(lambda: self.set_all_checked(False))

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        import_button = buttons.addButton("Import Checked Parts", QDialogButtonBox.AcceptRole)
        import_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)

        controls = QHBoxLayout()
        controls.addWidget(add_row_button)
        controls.addWidget(delete_button)
        controls.addWidget(select_all_button)
        controls.addWidget(select_none_button)
        controls.addStretch()
        controls.addWidget(buttons)

        right = QVBoxLayout()
        note = QLabel(
            "Review every OCR result before importing. Part Number cells are editable."
        )
        note.setWordWrap(True)
        right.addWidget(note)
        right.addWidget(self.table)
        right.addLayout(controls)

        right_widget = QWidget()
        right_widget.setLayout(right)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(scroll)
        splitter.addWidget(right_widget)
        splitter.setSizes([480, 720])

        layout = QVBoxLayout()
        layout.addWidget(splitter)
        self.setLayout(layout)

        self.load_preview()

    def add_candidate(self, candidate: ReviewCandidate) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        include = QTableWidgetItem()
        include.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        include.setCheckState(Qt.Checked)

        self.table.setItem(row, 0, include)
        self.table.setItem(row, 1, QTableWidgetItem(candidate.quantity))
        self.table.setItem(
            row,
            2,
            QTableWidgetItem(format_hobart_part_number(candidate.part_number)),
        )
        self.table.setItem(row, 3, QTableWidgetItem(candidate.description))
        self.table.setItem(row, 4, QTableWidgetItem(candidate.source))
        self.table.setItem(row, 5, QTableWidgetItem(candidate.confidence))

    def add_blank_row(self) -> None:
        self.add_candidate(
            ReviewCandidate(
                part_number="",
                source=self.preview_path.name if self.preview_path else "Manual correction",
                confidence="Manual",
            )
        )
        self.table.setCurrentCell(self.table.rowCount() - 1, 2)
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 2))

    def delete_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def selected_parts(self) -> list[PartInput]:
        parts: list[PartInput] = []
        for row in range(self.table.rowCount()):
            include = self.table.item(row, 0)
            number_item = self.table.item(row, 2)
            source_item = self.table.item(row, 4)

            if not include or include.checkState() != Qt.Checked or not number_item:
                continue

            number = number_item.text().strip()
            if not number:
                continue

            parts.append(
                PartInput(
                    display_number=number,
                    normalized_number=normalize_part_number(number),
                    source=source_item.text() if source_item else "Attachment",
                )
            )
        return parts

    def load_preview(self) -> None:
        if not self.preview_path:
            return

        suffix = self.preview_path.suffix.lower()
        pixmap = QPixmap()

        try:
            if suffix == ".pdf" and fitz is not None:
                document = fitz.open(str(self.preview_path))
                page = document.load_page(0)
                rendered = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
                temp_path = self.preview_path.parent / f".{self.preview_path.stem}_preview.png"
                rendered.save(str(temp_path))
                pixmap.load(str(temp_path))
                try:
                    temp_path.unlink()
                except OSError:
                    pass
                document.close()
            elif suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
                pixmap.load(str(self.preview_path))
        except Exception as exc:
            self.preview_label.setText(f"Preview could not be loaded:\n{exc}")
            return

        if not pixmap.isNull():
            self.preview_label.setPixmap(
                pixmap.scaled(520, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

class LoginDialog(QDialog):
    ORGANIZATIONS = [
        ("Hobart Service", "HOBARTSVC"),
        ("ITW FEG", "ITWFEG"),
        ("Vulcan Hart", "VULCANHART"),
        ("Resource Center", "FEGNA"),
        ("Vulcan Hart Charlotte", "ITWCE"),
        ("Wittco", "WFE"),
        ("Baxter", "BAXTER"),
        ("Other", "AspNetSqlMembershipProvider"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save VIP Login")
        self.setModal(True)

        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.organization = QComboBox()

        for label, value in self.ORGANIZATIONS:
            self.organization.addItem(label, value)

        settings = QSettings("EPL Inventory Checker", "EPL Inventory Checker")
        saved_org = settings.value(SETTINGS_ORGANIZATION_KEY, "FEGNA")
        index = self.organization.findData(saved_org)
        if index >= 0:
            self.organization.setCurrentIndex(index)

        form = QFormLayout()
        form.addRow("Username:", self.username)
        form.addRow("Password:", self.password)
        form.addRow("Organization:", self.organization)

        note = QLabel(
            "Credentials are stored locally in Windows Credential Manager. "
            "They are not written into the program files."
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def values(self):
        return (
            self.username.text().strip(),
            self.password.text(),
            self.organization.currentData(),
        )
