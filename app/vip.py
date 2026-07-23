import os

import re

import keyring

from PySide6.QtCore import QObject, Signal
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from app.constants import (
    CREDENTIAL_SERVICE,
    CREDENTIAL_USERNAME_KEY,
    DESCRIPTION,
    INVENTORY_TABLE,
    ITEM_ID,
    LEAD_TIME,
    LOGIN_BUTTON,
    LOGIN_ERROR,
    LOGIN_ORGANIZATION,
    LOGIN_PASSWORD,
    LOGIN_USERNAME,
    REPLACED_BY,
    SEARCH_BOX,
    SEARCH_BUTTON,
    STOCK_STATUS,
    VIP_URL,
)
from app.models import AlternateStockLocation, PartResult

class EPLSession:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def _start_browser(self):
        if self.page is not None:
            return

        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

        self.page.goto(
            VIP_URL,
            wait_until="domcontentloaded",
        )

class EPLBatchWorker(QObject):
    status = Signal(str)
    progress = Signal(int, int)
    result_ready = Signal(object)
    batch_complete = Signal()

    MAX_SUPERSESSION_HOPS = 10

    def __init__(self, parts: list[str], organization: str):
        super().__init__()
        self.parts = parts
        self.organization = organization
        self.session = EPLSession()

    def run(self) -> None:
        try:
            self.session._start_browser()

            self._prepare_epl_page(self.session.page)
            self._process_parts(self.session.page)

            self.session.browser.close()
            self.session.playwright.stop()

            # self.browser.close()
            # self.playwright.stop()

        except Exception as exc:
            self.result_ready.emit(
                PartResult(requested_part="", error=f"Browser session failed: {exc}")
            )
        finally:
            self.batch_complete.emit()

    def _process_parts(self, page) -> None:
        total = len(self.parts)

        for index, part in enumerate(self.parts, start=1):
            self.status.emit(f"Searching {part} ({index} of {total})...")
            result = self.lookup_part(page, part)
            self.result_ready.emit(result)
            self.progress.emit(index, total)

    def _prepare_epl_page(self, page) -> None:
        if page.locator(LOGIN_USERNAME).count():
            if not self._auto_login(page):
                raise RuntimeError(
                    "No saved VIP credentials were found. "
                    "Please save your login information before searching."
                )

            self.status.emit(
                "Signed into VIP. Opening Enterprise Parts Locator..."
            )

        try:
            page.locator(SEARCH_BOX).wait_for(
                state="visible",
                timeout=5_000,
            )
        except PlaywrightTimeoutError:
            self._open_epl_after_login(page)

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

        if page.locator(LOGIN_ERROR).count():
            error_text = page.locator(LOGIN_ERROR).first.inner_text().strip()
            if error_text:
                raise RuntimeError(f"VIP sign-in failed: {error_text}")

        return True

    def _open_epl_after_login(self, page) -> None:
        try:
            link = page.get_by_text("Enterprise Parts Locator", exact=True)
            link.wait_for(state="visible", timeout=300_000)
            link.click()
        except PlaywrightTimeoutError:
            self.status.emit(
                "After logging in, click Enterprise Parts Locator in the top banner."
            )

    def lookup_part(self, page, part: str) -> PartResult:
        result = PartResult(requested_part=part)
        current_part = part
        visited: set[str] = set()

        try:
            for hop in range(self.MAX_SUPERSESSION_HOPS + 1):
                normalized_key = self._part_key(current_part)

                if normalized_key in visited:
                    raise RuntimeError(
                        "EPL returned a circular supersession chain while checking "
                        f"{current_part}."
                    )
                visited.add(normalized_key)

                if hop > 0:
                    self.status.emit(
                        f"{part} was replaced by {current_part}. "
                        "Checking the current part..."
                    )

                self._search_part_page(page, current_part)

                displayed_item = self.safe_text(page, ITEM_ID)
                replacement = self.safe_text(page, REPLACED_BY)

                if displayed_item:
                    current_part = displayed_item

                if replacement:
                    replacement_key = self._part_key(replacement)
                    if replacement_key in visited:
                        raise RuntimeError(
                            "EPL returned a circular supersession chain involving "
                            f"{replacement}."
                        )

                    result.supersession_chain.append(replacement)
                    current_part = replacement
                    continue

                result.current_part = current_part
                result.item_id = displayed_item or current_part
                result.description = self.safe_text(page, DESCRIPTION)
                result.stock_status = self.safe_text(page, STOCK_STATUS)
                result.lead_time = self.safe_text(page, LEAD_TIME)

                self._read_inventory(page, result)
                return result

            raise RuntimeError(
                "The supersession chain exceeded "
                f"{self.MAX_SUPERSESSION_HOPS} replacements."
            )

        except Exception as exc:
            result.current_part = current_part
            result.error = str(exc)
            return result

    def _search_part_page(self, page, part: str) -> None:
        search_box = page.locator(SEARCH_BOX)

        old_item = ""
        if page.locator(ITEM_ID).count():
            old_item = page.locator(ITEM_ID).first.inner_text().strip()

        search_box.fill(part)
        page.locator(SEARCH_BUTTON).click()

        try:
            page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except PlaywrightTimeoutError:
            pass

        page.locator(ITEM_ID).wait_for(state="visible", timeout=60_000)

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

    def _read_inventory(self, page, result: PartResult) -> None:
        inventory_rows = page.locator(INVENTORY_TABLE).evaluate_all(
            """rows => rows.map(row => {
                const cells = Array.from(row.querySelectorAll('td'));
                if (cells.length < 8) return null;

                const isTransparent = color =>
                    !color ||
                    color === 'transparent' ||
                    color === 'rgba(0, 0, 0, 0)';

                const candidateElements = [
                    cells[0],
                    row,
                    row.parentElement
                ].filter(Boolean);

                let rowColor = '';
                for (const element of candidateElements) {
                    const color = getComputedStyle(element).backgroundColor || '';
                    if (!isTransparent(color)) {
                        rowColor = color;
                        break;
                    }
                }

                return {
                    site_code: cells[0].innerText.trim(),
                    site_name: cells[1].innerText.trim(),
                    warehouse: cells[2].innerText.trim(),
                    available_text: cells[3].innerText.trim(),
                    open_po_text: cells[7].innerText.trim(),
                    row_color: rowColor,
                    row_class: row.className || ''
                };
            }).filter(Boolean)"""
        )

        for inventory_row in inventory_rows:
            if inventory_row["site_name"].lower() == "piqua":
                result.piqua_available = inventory_row["available_text"]
                result.piqua_open_po = inventory_row["open_po_text"]
                break

        piqua_quantity = self.parse_inventory_quantity(result.piqua_available)
        if piqua_quantity > 0:
            return

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

    @staticmethod
    def _part_key(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", (value or "").upper())

    @staticmethod
    def parse_inventory_quantity(value: str) -> int:
        match = re.search(r"-?\d+", (value or "").replace(",", ""))
        return int(match.group(0)) if match else 0

    @staticmethod
    def classify_inventory_row(color: str) -> str:
        normalized = (color or "").strip().lower()
        if not normalized or normalized in {
            "transparent",
            "rgba(0, 0, 0, 0)",
        }:
            return ""

        numbers = [int(value) for value in re.findall(r"\d+", normalized)[:3]]
        if len(numbers) != 3:
            return ""

        red, green, blue = numbers
        brightness = (red + green + blue) / 3
        channel_spread = max(numbers) - min(numbers)

        if red >= 90 and red >= green * 1.5 and red >= blue * 1.5:
            return "Hobart Branch"

        if 30 <= brightness <= 110 and channel_spread <= 25:
            return "Service Contractor"

        return ""

    @staticmethod
    def safe_text(page, selector: str) -> str:
        locator = page.locator(selector)
        if locator.count() == 0:
            return ""
        return locator.first.inner_text().strip()
