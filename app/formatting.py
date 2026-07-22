import re


def format_hobart_part_number(value: str) -> str:
    """Return the canonical Hobart EPL search format for a part number."""
    cleaned = value.strip().upper()
    cleaned = cleaned.replace("–", "-").replace("—", "-")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = cleaned.strip(".,;:()[]{}")

    if not cleaned:
        return ""

    # Format 3:
    # XX-XXX-XX
    #
    # The prefix must be two letters, but cannot be EW.
    # Accepts both SC-048-35 and SC048-35.
    short_match = re.fullmatch(
        r"([A-Z]{2})-?(\d{3})-?(\d{2})",
        cleaned,
    )

    if short_match:
        prefix, middle, suffix = short_match.groups()

        if prefix != "EW":
            return f"{prefix}-{middle}-{suffix}"

    # Formats 1 and 2:
    # XX-XXXXXX
    # XX-XXXXXX-XXXXX
    #
    # These formats may only use 00, 01, or EW as their prefix.
    # Hyphens may be missing in manually entered values.
    long_match = re.fullmatch(
        r"(00|01|EW)-?([A-Z0-9]{6})(?:-?([A-Z0-9]{5}))?",
        cleaned,
    )

    if long_match:
        prefix, middle, suffix = long_match.groups()

        if suffix:
            return f"{prefix}-{middle}-{suffix}"

        return f"{prefix}-{middle}"

    # No prefix supplied:
    # Add the default 00 prefix.
    #
    # Examples:
    # 123456       -> 00-123456
    # 1000V8-00115 -> 00-1000V8-00115
    unprefixed_match = re.fullmatch(
        r"([A-Z0-9]{6})(?:-?([A-Z0-9]{5}))?",
        cleaned,
    )

    if unprefixed_match:
        middle, suffix = unprefixed_match.groups()

        if suffix:
            return f"00-{middle}-{suffix}"

        return f"00-{middle}"

    # Return the cleaned value unchanged when it does not match
    # one of the three recognized Hobart formats.
    return cleaned


def normalize_part_number(value: str) -> str:
    """Backward-compatible wrapper used throughout the application."""
    return format_hobart_part_number(value)


def canonical_duplicate_key(value: str) -> str:
    normalized = normalize_part_number(value)
    if normalized.startswith("00-"):
        normalized = normalized[3:]
    return re.sub(r"[^A-Z0-9]", "", normalized.upper())
