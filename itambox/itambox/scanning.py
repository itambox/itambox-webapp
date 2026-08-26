def strip_itambox_prefix(code: str) -> str:
    """Strip standard itambox:// or itambox: prefix from the scanned code, leaving the bare tag/serial."""
    if not code:
        return ""
    # Defensive cleaning: strip spaces, quotes, BOM, zero-width spaces, and normalize colons
    raw = code.strip().replace("\ufeff", "").replace("\u200b", "")
    raw = raw.replace("：", ":")
    raw = raw.strip("\"' ")

    if raw.lower().startswith("itambox://asset/"):
        return raw

    if raw.lower().startswith("itambox://"):
        raw = raw[len("itambox://") :].strip("/ \\\"'")
    elif raw.lower().startswith("itambox:"):
        raw = raw[len("itambox:") :].strip("/ \\\"'")
    return raw
