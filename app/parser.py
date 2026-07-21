import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

import pdfplumber

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

from PySide6.QtWidgets import QMessageBox

from app.constants import IGNORED_PREFIXES, PART_PATTERN
from app.formatting import canonical_duplicate_key, normalize_part_number
from app.models import PartInput, ReviewCandidate


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

TESSERACT_PATH = configure_tesseract()

def run_local_ocr(path: Path, parent=None) -> list[ReviewCandidate]:
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

def read_attachment_candidates(path: Path, parent=None) -> list[ReviewCandidate]:
    """Read a supported attachment and return candidates for review."""
    suffix = path.suffix.lower()
    candidates: list[ReviewCandidate] = []

    if suffix == ".pdf":
        page_texts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                page_texts.append(page_text)
                candidates.extend(
                    extract_typed_quote_candidates(
                        page_text,
                        f"{path.name} - page {page_number}",
                    )
                )

        all_text = "\n".join(page_texts)
        if not candidates and not all_text.strip():
            return run_local_ocr(path, parent)
        if not candidates:
            return [
                ReviewCandidate(
                    part_number=part.display_number,
                    source=part.source,
                    confidence="Review",
                )
                for part in extract_parts_from_text(all_text, path.name)
            ]
        return candidates

    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        return run_local_ocr(path, parent)

    if suffix == ".txt":
        text = path.read_text(encoding="utf-8", errors="ignore")
        return [
            ReviewCandidate(
                part_number=part.display_number,
                source=part.source,
                confidence="High",
            )
            for part in extract_parts_from_text(text, path.name)
        ]

    raise ValueError(f"{path.name} is not supported.")
