from dataclasses import dataclass


@dataclass
class PartInput:
    display_number: str
    normalized_number: str
    source: str


@dataclass
class ReviewCandidate:
    part_number: str
    source: str
    description: str = ""
    quantity: str = ""
    confidence: str = "High"


@dataclass
class AlternateStockLocation:
    location_type: str
    site_code: str
    site_name: str
    warehouse: str
    available: int


@dataclass
class PartResult:
    requested_part: str
    item_id: str = ""
    description: str = ""
    stock_status: str = ""
    lead_time: str = ""
    piqua_available: str = ""
    piqua_open_po: str = ""
    alternate_stock: list[AlternateStockLocation] = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.alternate_stock is None:
            self.alternate_stock = []
