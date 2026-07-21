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
    SEARCH_BOX,
    SEARCH_BUTTON,
    STOCK_STATUS,
    VIP_URL,
)
from app.models import AlternateStockLocation, PartResult


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
        """Classify EPL inventory rows from their effective background color.

        Red rows are Hobart branches, dark-gray rows are service contractors,
        and light-gray rows are technician trucks. Transparent/unknown colors
        are ignored rather than guessed.
        """
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

        # EPL branch rows are a clearly red shade.
        if red >= 90 and red >= green * 1.5 and red >= blue * 1.5:
            return "Hobart Branch"

        # EPL service-contractor rows are neutral dark gray. Exclude near-black
        # values because those usually indicate a failed/transparent lookup.
        if 30 <= brightness <= 110 and channel_spread <= 25:
            return "Service Contractor"

        # Light-gray technician-truck rows and all unknown colors are ignored.
        return ""

    @staticmethod
    def safe_text(page, selector: str) -> str:
        locator = page.locator(selector)
        if locator.count() == 0:
            return ""
        return locator.first.inner_text().strip()
