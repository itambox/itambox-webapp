"""Shared CSV-export safety helpers (formula injection + header injection).

A single home so every CSV writer in the app neutralizes the same way, instead of the guard
living only in ObjectExportView. See core/tests/test_csv_utils for the contract.
"""

# Characters a spreadsheet treats as a formula/command trigger at the start of a cell.
_FORMULA_TRIGGERS = ('=', '+', '-', '@', '\t', '\r')


def csv_safe(value):
    """Neutralize CSV formula injection: a cell whose first character is one a spreadsheet
    treats as a formula trigger is prefixed with a single quote so it renders as literal
    text rather than being evaluated."""
    text = '' if value is None else str(value)
    if text and text[0] in _FORMULA_TRIGGERS:
        return "'" + text
    return text


def safe_csv_filename(name, default='export'):
    """Strip characters that could break out of a Content-Disposition ``filename`` parameter
    (CR/LF header injection, embedded quotes/backslashes)."""
    cleaned = ''.join(c for c in str(name) if c not in '\r\n"\\').strip()
    return cleaned or default
