"""Parse page range text such as "1-3, 5, 8-" into groups of zero-based indexes."""

from __future__ import annotations

import re

from .errors import ViewerError

_PART = re.compile(r"^\s*(\d*)\s*(?:(-|–|—)\s*(\d*))?\s*$")


def parse_ranges(text: str, page_count: int) -> list[list[int]]:
    """Return one list of zero-based indexes per comma-separated range."""
    groups: list[list[int]] = []
    for chunk in re.split(r"[,;，；]", text):
        if not chunk.strip():
            continue
        match = _PART.match(chunk)
        if not match or (not match.group(1) and not match.group(3) and not match.group(2)):
            raise ViewerError(f'"{chunk.strip()}" is not a valid range. Use a form like 1-3, 5, 8-12.')
        first_text, dash, last_text = match.groups()
        first = int(first_text) if first_text else 1
        if dash:
            last = int(last_text) if last_text else page_count
        else:
            last = first
        if first < 1 or last < 1 or first > page_count or last > page_count:
            raise ViewerError(
                f'"{chunk.strip()}" is outside this document, which has {page_count} page(s).'
            )
        if first > last:
            raise ViewerError(f'"{chunk.strip()}" runs backwards. Write the lower number first.')
        groups.append(list(range(first - 1, last)))
    if not groups:
        raise ViewerError("Enter at least one page or range, for example 1-3, 5.")
    return groups


def format_ranges(indexes: list[int]) -> str:
    """Turn zero-based indexes into compact one-based range text."""
    if not indexes:
        return ""
    ordered = sorted(set(indexes))
    pieces = []
    start = previous = ordered[0]
    for value in ordered[1:] + [None]:
        if value is not None and value == previous + 1:
            previous = value
            continue
        pieces.append(f"{start + 1}" if start == previous else f"{start + 1}-{previous + 1}")
        if value is not None:
            start = previous = value
    return ", ".join(pieces)
