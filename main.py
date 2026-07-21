
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

import pdfplumber
import keyring

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
    Image = None


def configure_tesseract() -> Optional[Path]:
    """Find Tesseract automatically and configure pytesseract.

    Checks the Windows PATH, standard installer folders, and a bundled
    Tesseract-OCR folder beside the application for future portable builds.
    """
    if pytesseract is None:
        return None

    candidates: list[Path] = []

    path_match = shutil.which("tesseract")
    if path_match:
        candidates.append(Path(path_match))

    app_directory = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )

    candidates.extend(
        [
            app_directory / "Tesseract-OCR" / "tesseract.exe",
            app_directory / "tesseract.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Tesseract-OCR"
            / "tesseract.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Tesseract-OCR"
            / "tesseract.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs"
            / "Tesseract-OCR"
            / "tesseract.exe",
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        candidate_text = str(candidate)
        if not candidate_text or candidate_text in seen:
            continue
        seen.add(candidate_text)

        if candidate.is_file():
            pytesseract.pytesseract.tesseract_cmd = candidate_text
            return candidate

    return None


TESSERACT_PATH = configure_tesseract()

from PySide6.QtCore import QObject, QSettings, QThread, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
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
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from app.constants import (
    CREDENTIAL_SERVICE,
    CREDENTIAL_USERNAME_KEY,
    DESCRIPTION,
    IGNORED_PREFIXES,
    INVENTORY_TABLE,
    ITEM_ID,
    LEAD_TIME,
    LOGIN_BUTTON,
    LOGIN_ERROR,
    LOGIN_ORGANIZATION,
    LOGIN_PASSWORD,
    LOGIN_USERNAME,
    PART_PATTERN,
    SEARCH_BOX,
    SEARCH_BUTTON,
    SETTINGS_ORGANIZATION_KEY,
    STOCK_STATUS,
    VIP_URL,
)
from app.formatting import (
    canonical_duplicate_key,
    format_hobart_part_number,
    normalize_part_number,
)
from app.models import (
    AlternateStockLocation,
    PartInput,
    PartResult,
    ReviewCandidate,
)
from app.dialogs import (
    AlternateStockDialog,
    AttachmentReviewDialog,
    LoginDialog,
)



def extract_typed_quote_candidates(text: str, source: str) -> list[ReviewCandidate]:
    """Extract only line items from typed Hobart-style quote tables."""
    candidates: list[ReviewCandidate] = []
    seen: set[str] = set()

    line_pattern = re.compile(
        r"^\s*(\d+(?:\.\d+)?)\s+"
        r"([A-Z0-9]+(?:-[A-Z0-9]+)*)\s+"
        r"(.+?)\s+"
        r"\d[\d,]*\.\d{2}\s+"
        r"\d[\d,]*\.\d{2}\s*$",
        re.IGNORECASE,
    )

    for line in text.splitlines():
        match = line_pattern.match(line)
        if not match:
            continue

        quantity, part_number, description = match.groups()
        upper_part = part_number.upper()

        if any(upper_part.startswith(prefix) for prefix in IGNORED_PREFIXES):
            continue
        if upper_part in {"SUB", "TAX", "TOTAL", "QUOTE"}:
            continue

        key = canonical_duplicate_key(part_number)
        digits = re.sub(r"\D", "", part_number)
        if not key or len(digits) < 5 or key in seen:
            continue

        seen.add(key)
        candidates.append(
            ReviewCandidate(
                part_number=part_number.upper(),
                source=source,
                description=description.strip(),
                quantity=quantity,
                confidence="High",
            )
        )

    return candidates


def extract_ocr_candidates(text: str, source: str) -> list[ReviewCandidate]:
    candidates: list[ReviewCandidate] = []
    seen: set[str] = set()

    for line in text.splitlines():
        cleaned_line = line.strip()
        if not cleaned_line:
            continue

        matches = PART_PATTERN.findall(cleaned_line.upper())
        for raw in matches:
            digits = re.sub(r"\D", "", raw)
            if len(digits) < 5:
                continue

            key = canonical_duplicate_key(raw)
            if not key or key in seen:
                continue

            seen.add(key)
            candidates.append(
                ReviewCandidate(
                    part_number=raw.upper(),
                    source=source,
                    description=cleaned_line,
                    confidence="Review",
                )
            )

    return candidates


def extract_parts_from_text(text: str, source: str) -> list[PartInput]:
    found: list[PartInput] = []
    seen: set[str] = set()

    for raw in PART_PATTERN.findall(text.upper()):
        candidate = raw.strip()
        if any(candidate.startswith(prefix) for prefix in IGNORED_PREFIXES):
            continue

        # Avoid dates and small ordinary numbers.
        digits = re.sub(r"\D", "", candidate)
        if len(digits) < 5:
            continue

        key = canonical_duplicate_key(candidate)
        if not key or key in seen:
            continue

        seen.add(key)
        found.append(
            PartInput(
                display_number=candidate,
                normalized_number=normalize_part_number(candidate),
                source=source,
            )
        )

    return found


class EPLBatchWorker(QObject):
    status = Signal(str)
    progress = Signal(int, int)
    result_ready = Signal(object)
    finished = Signal()

    def __init__(self, parts: list[str], organization: str):
        super().__init__()
        self.parts = parts
        self.organization = organization

    def run(self) -> None:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                context = browser.new_context()
                page = context.new_page()
                page.goto(VIP_URL, wait_until="domcontentloaded")

                if page.locator(LOGIN_USERNAME).count():
                    if self._auto_login(page):
                        self.status.emit("Signed into VIP. Opening Enterprise Parts Locator...")
                    else:
                        self.status.emit(
                            "No saved login was found. Sign in manually; the program will continue afterward."
                        )

                # Wait until either EPL is already open or the banner link is available.
                try:
                    page.locator(SEARCH_BOX).wait_for(state="visible", timeout=5_000)
                except PlaywrightTimeoutError:
                    self._open_epl_after_login(page)

                page.locator(SEARCH_BOX).wait_for(state="visible", timeout=300_000)

                total = len(self.parts)
                for index, part in enumerate(self.parts, start=1):
                    self.status.emit(f"Searching {part} ({index} of {total})...")
                    result = self.lookup_part(page, part)
                    self.result_ready.emit(result)
                    self.progress.emit(index, total)

                browser.close()

        except Exception as exc:
            self.result_ready.emit(
                PartResult(requested_part="", error=f"Browser session failed: {exc}")
            )
        finally:
            self.finished.emit()

    def _auto_login(self, page) -> bool:
        username = keyring.get_password(CREDENTIAL_SERVICE, CREDENTIAL_USERNAME_KEY)
        if not username:
            return False

        password = keyring.get_password(CREDENTIAL_SERVICE, username)
        if not password:
            return False

        page.locator(LOGIN_USERNAME).fill(username)
        page.locator(LOGIN_PASSWORD).fill(password)
        page.locator(LOGIN_ORGANIZATION).select_option(self.organization or "FEGNA")
        page.locator(LOGIN_BUTTON).click()

        try:
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except PlaywrightTimeoutError:
            pass

        # A visible login error means credentials or organization were rejected.
        if page.locator(LOGIN_ERROR).count():
            error_text = page.locator(LOGIN_ERROR).first.inner_text().strip()
            if error_text:
                raise RuntimeError(f"VIP sign-in failed: {error_text}")

        return True

    def _open_epl_after_login(self, page) -> None:
        # Give the user time to complete login, then click the EPL banner.
        try:
            link = page.get_by_text("Enterprise Parts Locator", exact=True)
            link.wait_for(state="visible", timeout=300_000)
            link.click()
        except PlaywrightTimeoutError:
            # Manual navigation remains a fallback.
            self.status.emit(
                "After logging in, click Enterprise Parts Locator in the top banner."
            )

    def lookup_part(self, page, part: str) -> PartResult:
        result = PartResult(requested_part=part)

        try:
            search_box = page.locator(SEARCH_BOX)
            search_box.fill(part)

            old_item = ""
            if page.locator(ITEM_ID).count():
                old_item = page.locator(ITEM_ID).first.inner_text().strip()

            page.locator(SEARCH_BUTTON).click()

            # ASP.NET may perform a full postback or update the current DOM.
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
            except PlaywrightTimeoutError:
                pass

            page.locator(ITEM_ID).wait_for(state="visible", timeout=60_000)

            # Wait for the displayed item to change when possible.
            if old_item:
                try:
                    page.wait_for_function(
                        """({selector, oldValue}) => {
                            const el = document.querySelector(selector);
                            return el && el.textContent.trim() !== oldValue;
                        }""",
                        arg={"selector": ITEM_ID, "oldValue": old_item},
                        timeout=15_000,
                    )
                except PlaywrightTimeoutError:
                    pass

            result.item_id = self.safe_text(page, ITEM_ID)
            result.description = self.safe_text(page, DESCRIPTION)
            result.stock_status = self.safe_text(page, STOCK_STATUS)
            result.lead_time = self.safe_text(page, LEAD_TIME)

            # Read the whole inventory table in one browser-to-Python transfer.
            # The previous version made several Playwright calls for every row,
            # which became very slow on large EPL inventory tables.
            inventory_rows = page.locator(INVENTORY_TABLE).evaluate_all(
                """rows => rows.map(row => {
                    const cells = Array.from(row.querySelectorAll('td'));
                    if (cells.length < 8) return null;

                    const colorElement = cells[0] || row;
                    const rowColor = getComputedStyle(colorElement).backgroundColor || '';

                    return {
                        site_code: cells[0].innerText.trim(),
                        site_name: cells[1].innerText.trim(),
                        warehouse: cells[2].innerText.trim(),
                        available_text: cells[3].innerText.trim(),
                        open_po_text: cells[7].innerText.trim(),
                        row_color: rowColor
                    };
                }).filter(Boolean)"""
            )

            for inventory_row in inventory_rows:
                if inventory_row["site_name"].lower() == "piqua":
                    result.piqua_available = inventory_row["available_text"]
                    result.piqua_open_po = inventory_row["open_po_text"]
                    break

            piqua_quantity = self.parse_inventory_quantity(result.piqua_available)
            if piqua_quantity <= 0:
                for inventory_row in inventory_rows:
                    site_name = inventory_row["site_name"]
                    if site_name.lower() == "piqua":
                        continue

                    available = self.parse_inventory_quantity(
                        inventory_row["available_text"]
                    )
                    if available <= 0:
                        continue

                    location_type = self.classify_inventory_row(
                        inventory_row["row_color"]
                    )
                    if not location_type:
                        continue

                    result.alternate_stock.append(
                        AlternateStockLocation(
                            location_type=location_type,
                            site_code=inventory_row["site_code"],
                            site_name=site_name,
                            warehouse=inventory_row["warehouse"],
                            available=available,
                        )
                    )

                result.alternate_stock.sort(
                    key=lambda location: (
                        0 if location.location_type == "Hobart Branch" else 1,
                        -location.available,
                        location.site_name,
                    )
                )

        except Exception as exc:
            result.error = str(exc)

        return result

    @staticmethod
    def parse_inventory_quantity(value: str) -> int:
        match = re.search(r"-?\d+", (value or "").replace(",", ""))
        return int(match.group(0)) if match else 0

    @staticmethod
    def classify_inventory_row(color: str) -> str:
        """Classify EPL's colored inventory rows.

        Red rows represent Hobart branches. Dark-gray rows represent service
        contractors. Light-gray warehouse detail rows are intentionally ignored.
        """
        numbers = [int(value) for value in re.findall(r"\d+", color or "")[:3]]
        if len(numbers) != 3:
            return ""

        red, green, blue = numbers

        if red >= 90 and red > green * 1.35 and red > blue * 1.35:
            return "Hobart Branch"

        brightness = (red + green + blue) / 3
        channel_spread = max(numbers) - min(numbers)
        if brightness <= 125 and channel_spread <= 45:
            return "Service Contractor"

        return ""

    @staticmethod
    def safe_text(page, selector: str) -> str:
        locator = page.locator(selector)
        if locator.count() == 0:
            return ""
        return locator.first.inner_text().strip()










class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EPL Inventory Checker v0.5.3")
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

        self.results_table = QTableWidget(0, 8)
        self.results_table.setHorizontalHeaderLabels(
            [
                "Requested Part",
                "EPL Item ID",
                "Description",
                "Stock Status",
                "Lead Time",
                "Piqua Available",
                "Piqua Open PO",
                "Alternate Stock",
            ]
        )
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
                candidates: list[ReviewCandidate] = []
                suffix = path.suffix.lower()

                if suffix == ".pdf":
                    page_texts: list[str] = []
                    with pdfplumber.open(path) as pdf:
                        for page_number, page in enumerate(pdf.pages, start=1):
                            page_text = page.extract_text() or ""
                            page_texts.append(page_text)

                            typed = extract_typed_quote_candidates(
                                page_text,
                                f"{path.name} - page {page_number}",
                            )
                            candidates.extend(typed)

                    if not candidates and not "\n".join(page_texts).strip():
                        candidates = self.run_local_ocr(path)
                    elif not candidates:
                        # Nonstandard typed PDF fallback, still reviewed before import.
                        candidates = [
                            ReviewCandidate(
                                part_number=p.display_number,
                                source=p.source,
                                confidence="Review",
                            )
                            for p in extract_parts_from_text(
                                "\n".join(page_texts),
                                path.name,
                            )
                        ]

                elif suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
                    candidates = self.run_local_ocr(path)

                elif suffix == ".txt":
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    candidates = [
                        ReviewCandidate(
                            part_number=p.display_number,
                            source=p.source,
                            confidence="High",
                        )
                        for p in extract_parts_from_text(text, path.name)
                    ]

                else:
                    QMessageBox.warning(
                        self,
                        "Unsupported File",
                        f"{path.name} is not supported.",
                    )
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

    def run_local_ocr(self, path: Path) -> list[ReviewCandidate]:
        global TESSERACT_PATH

        if pytesseract is None or Image is None or fitz is None:
            QMessageBox.warning(
                self,
                "OCR Components Missing",
                (
                    "This file appears to be a scan or image.\n\n"
                    "Install the Python OCR packages with:\n"
                    "pip install pytesseract pillow pymupdf\n\n"
                    "You must also install the free Tesseract OCR program for Windows. "
                    "The review viewer will still open so you can enter or correct parts manually."
                ),
            )
            return []

        # Retry detection in case Tesseract was installed after the app started.
        TESSERACT_PATH = configure_tesseract()
        if TESSERACT_PATH is None:
            QMessageBox.warning(
                self,
                "Tesseract OCR Not Found",
                (
                    "The Python OCR packages are installed, but the Windows "
                    "Tesseract OCR engine could not be found.\n\n"
                    "Install Tesseract in its normal location:\n"
                    "C:\\Program Files\\Tesseract-OCR\\tesseract.exe\n\n"
                    "You do not need to add it to PATH. After installation, "
                    "restart the app and try again. The review window will still "
                    "open so parts can be entered manually."
                ),
            )
            return []

        images: list[tuple[object, str]] = []
        suffix = path.suffix.lower()

        try:
            if suffix == ".pdf":
                document = fitz.open(str(path))
                for page_number in range(document.page_count):
                    page = document.load_page(page_number)
                    pix = page.get_pixmap(matrix=fitz.Matrix(2.6, 2.6), alpha=False)
                    mode = "RGB"
                    image = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
                    images.append((image, f"{path.name} - page {page_number + 1}"))
                document.close()
            else:
                images.append((Image.open(path).convert("RGB"), path.name))

            found: list[ReviewCandidate] = []
            seen: set[str] = set()

            for image, source in images:
                # PSM 6 works well for a single structured sheet while still preserving lines.
                text = pytesseract.image_to_string(image, config="--psm 6")
                for candidate in extract_ocr_candidates(text, source):
                    key = canonical_duplicate_key(candidate.part_number)
                    if key and key not in seen:
                        seen.add(key)
                        found.append(candidate)

            return found

        except pytesseract.pytesseract.TesseractNotFoundError:
            QMessageBox.warning(
                self,
                "Tesseract Not Installed",
                (
                    "The Python OCR package is installed, but the Tesseract OCR program "
                    "was not found on Windows.\n\n"
                    "Install Tesseract in C:\\Program Files\\Tesseract-OCR, "
                    "restart the app, and try again. You do not need to add it "
                    "to PATH. The review viewer will still open for manual entry."
                ),
            )
            return []

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

        values = [
            result.requested_part,
            result.item_id,
            result.description,
            result.stock_status,
            result.lead_time,
            result.piqua_available,
            result.piqua_open_po,
        ]

        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if result.error and column == 2:
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
            self.results_table.setCellWidget(row, 7, button)

        elif piqua_quantity <= 0 and not result.error:
            out_of_stock_item = QTableWidgetItem("OUT OF STOCK")
            out_of_stock_item.setToolTip(
                "Piqua has zero or negative stock and no other qualifying "
                "location shows positive Available Physical inventory."
            )
            self.results_table.setItem(row, 7, out_of_stock_item)

            out_of_stock_background = QColor(255, 205, 205)
            for column in range(self.results_table.columnCount()):
                item = self.results_table.item(row, column)
                if item is None:
                    item = QTableWidgetItem("")
                    self.results_table.setItem(row, column, item)
                item.setBackground(out_of_stock_background)

        else:
            self.results_table.setItem(row, 7, QTableWidgetItem("Piqua stocked"))

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


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
