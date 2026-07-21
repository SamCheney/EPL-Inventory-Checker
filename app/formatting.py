import re


def format_hobart_part_number(value: str) -> str:
    """Return the canonical Hobart EPL search format for a part number."""
    cleaned = value.strip().upper()
    cleaned = cleaned.replace("–", "-").replace("—", "-")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = cleaned.strip(".,;:()[]{}")

    if not cleaned:
        return ""

    if re.match(r"^\d{2}-", cleaned):
        return cleaned

    alpha_match = re.fullmatch(r"([A-Z]{2})-?(\d{2,3})-?(\d{2})", cleaned)
    if alpha_match:
        prefix, middle, suffix = alpha_match.groups()
        return f"{prefix}-{middle.zfill(3)}-{suffix}"

    if cleaned[0].isdigit():
        return f"00-{cleaned}"

    return cleaned


def normalize_part_number(value: str) -> str:
    """Backward-compatible wrapper used throughout the application."""
    return format_hobart_part_number(value)


def canonical_duplicate_key(value: str) -> str:
    normalized = normalize_part_number(value)
    if normalized.startswith("00-"):
        normalized = normalized[3:]
    return re.sub(r"[^A-Z0-9]", "", normalized.upper())
