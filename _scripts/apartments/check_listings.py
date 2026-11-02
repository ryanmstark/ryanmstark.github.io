#!/usr/bin/env python3
"""
Re-check every posting link in apartments/listings.json.

Marks a listing "removed" when its page is gone (HTTP 404/410), says it was
deleted/expired/rented, or (Craigslist) is past its "unavailable_after" date.
Anything we can't read (blocked, timeout) is left as-is so a flaky check never
hides a live listing. Updates checked_at, and writes the file only on change.

Standard library only; no API keys needed.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "apartments" / "listings.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

GONE_PHRASES = [
    "this posting has been deleted by its author",
    "this posting has expired",
    "this posting has been flagged for removal",
    "this listing is no longer available",
    "this listing has been removed",
    "this property is no longer available",
    "no longer accepting applications",
    "listing is off market",
    "this listing is off the market",
]

UNAVAILABLE_AFTER = re.compile(r"unavailable_after:\s*([0-9TZ:\-\.+]+)", re.I)


def check(url: str) -> str:
    """Return 'removed', 'active', or 'unknown' for one posting URL."""
    try:
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=20) as resp:
            html = resp.read(800_000).decode("utf-8", "ignore")
    except HTTPError as exc:
        return "removed" if exc.code in (404, 410) else "unknown"
    except (URLError, TimeoutError, OSError):
        return "unknown"

    low = html.lower()
    if any(p in low for p in GONE_PHRASES):
        return "removed"

    m = UNAVAILABLE_AFTER.search(html)
    if m:
        try:
            cutoff = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if cutoff < datetime.now(timezone.utc):
                return "removed"
        except ValueError:
            pass
    return "active"


def main() -> None:
    data = json.loads(DATA.read_text())
    before = json.dumps(data, sort_keys=True)

    for section in ("unique", "complexes"):
        for item in data.get(section, []):
            if item.get("status") == "removed":
                continue
            result = check(item["url"])
            print(f"{result:8} {item.get('id', item['url'])}")
            if result == "removed":
                item["status"] = "removed"
                item["removed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    changed = json.dumps(data, sort_keys=True) != before
    data["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    DATA.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print("changes:", "yes" if changed else "no (timestamp only)")


if __name__ == "__main__":
    main()
