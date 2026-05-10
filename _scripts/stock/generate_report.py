#!/usr/bin/env python3
"""
stock_news/generate_report.py

Generates a self-contained HTML stock digest for a personal watchlist.

Reads tickers from watchlist.txt (one per line), fetches today's price
performance, recent news, upcoming calendar events, and previous earnings
results via yfinance. Uses the Gemini API to write a synthesized prose
summary for the overall digest and for each individual ticker. Writes the
final report to output/report.html.

Usage:
    python generate_report.py

Environment:
    GROQ_API_KEY — required for AI summaries; load from .env or shell.
    If unset, the report is generated without prose summaries.
"""

from __future__ import annotations

import html
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import re

from groq import Groq
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

WATCHLIST_FILE = Path(__file__).parent / "watchlist.txt"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "stock-report"
SUMMARY_MODEL = "llama-3.3-70b-versatile"

# News items whose title contains any of these terms (case-insensitive) are dropped.
_NEWS_TITLE_BLOCKLIST = frozenset(["cramer"])
# News items from any of these publishers (case-insensitive) are dropped.
_NEWS_PUBLISHER_BLOCKLIST = frozenset(["thestreet"])

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f5f7fa;
    color: #1a1a2e;
    font-size: 15px;
    line-height: 1.5;
}

header {
    background: #0d1117;
    color: #e6edf3;
    padding: 28px 40px;
    position: sticky;
    top: 0;
    z-index: 10;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}

header h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; }
.subtitle { color: #8b949e; font-size: 0.85rem; margin-top: 4px; }

main { max-width: 1000px; margin: 0 auto; padding: 36px 24px; }

.digest-card {
    background: white;
    border-radius: 10px;
    padding: 24px 28px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    margin-bottom: 32px;
    border-left: 4px solid #0969da;
}

.digest-text p {
    font-size: 0.95rem;
    line-height: 1.75;
    color: #24292f;
    margin-bottom: 12px;
}

.digest-text p:last-child { margin-bottom: 0; }

.digest-text ul { list-style: none; padding: 0; margin: 0; }
.digest-text li {
    font-size: 0.95rem;
    line-height: 1.7;
    color: #24292f;
    padding: 10px 0 10px 24px;
    position: relative;
    border-bottom: 1px solid #f0f0f0;
}
.digest-text li:last-child { border-bottom: none; padding-bottom: 0; }
.digest-text li::before {
    content: "→";
    position: absolute;
    left: 0;
    color: #0969da;
    font-weight: 700;
}

.summary-card, .ticker-card {
    background: white;
    border-radius: 10px;
    padding: 24px 28px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
}

.summary-card { margin-bottom: 40px; }
.ticker-card { margin-bottom: 20px; scroll-margin-top: 90px; }

.card-title, .section-label {
    font-size: 0.72rem;
    font-weight: 700;
    color: #57606a;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 14px;
}

table { width: 100%; border-collapse: collapse; }

th {
    text-align: left;
    font-size: 0.72rem;
    font-weight: 700;
    color: #57606a;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 8px 12px;
    border-bottom: 2px solid #e1e4e8;
}

th.r { text-align: right; }
td { padding: 13px 12px; border-bottom: 1px solid #f0f0f0; vertical-align: middle; }
td.r { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: #f6f8fa; cursor: pointer; }

.ticker-link { font-weight: 700; font-size: 0.95rem; color: #0969da; text-decoration: none; }
.ticker-link:hover { text-decoration: underline; }

.pos { color: #1a7f37; font-weight: 500; }
.neg { color: #cf222e; font-weight: 500; }
.neu { color: #57606a; }

.ticker-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 18px;
    border-bottom: 1px solid #e1e4e8;
    margin-bottom: 20px;
}

.ticker-head h2 { font-size: 1.4rem; font-weight: 700; color: #0d1117; }
.co-name { color: #57606a; font-size: 0.9rem; margin-top: 3px; }

.price-block { text-align: right; flex-shrink: 0; }
.price-block .price { font-size: 1.5rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.price-block .change { font-size: 0.88rem; margin-top: 3px; }

.ticker-summary {
    background: #f6f8fa;
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 20px;
}

.ticker-summary p {
    font-size: 0.92rem;
    line-height: 1.7;
    color: #24292f;
    margin-bottom: 10px;
}

.ticker-summary p:last-child { margin-bottom: 0; }

.events { margin-bottom: 20px; }

.event-chips { display: flex; gap: 10px; flex-wrap: wrap; }

.chip {
    background: #f6f8fa;
    border: 1px solid #e1e4e8;
    border-radius: 8px;
    padding: 8px 14px;
}

.chip.chip-past {
    background: #f0f0ff;
    border-color: #c8c8f0;
}

.chip-label { font-size: 0.68rem; color: #57606a; text-transform: uppercase; letter-spacing: 0.05em; }
.chip-value { font-weight: 600; font-size: 0.88rem; margin-top: 2px; }
.chip-eps { font-size: 0.78rem; margin-top: 3px; }

.news-item { padding: 12px 0; border-bottom: 1px solid #f0f0f0; }
.news-item:last-child { border-bottom: none; padding-bottom: 0; }

.news-item a {
    color: #0d1117;
    text-decoration: none;
    font-size: 0.95rem;
    font-weight: 500;
    line-height: 1.45;
    display: block;
}

.news-item a:hover { color: #0969da; text-decoration: underline; }
.news-meta { font-size: 0.76rem; color: #8b949e; margin-top: 3px; }

.error-tag {
    display: inline-block;
    background: #ffebe9;
    color: #cf222e;
    border: 1px solid #ffcecb;
    border-radius: 5px;
    padding: 3px 8px;
    font-size: 0.78rem;
    font-weight: 500;
}

.no-data { color: #8b949e; font-size: 0.88rem; font-style: italic; }

.back-top { display: inline-block; margin-top: 18px; font-size: 0.8rem; color: #0969da; text-decoration: none; }
.back-top:hover { text-decoration: underline; }

@media (max-width: 640px) {
    header { padding: 18px 20px; }
    header h1 { font-size: 1.2rem; }
    main { padding: 20px 12px; }
    .summary-card, .ticker-card, .digest-card { padding: 16px; overflow-x: auto; }
    .ticker-head { flex-direction: column; gap: 8px; }
    .price-block { text-align: left; }
    .price-block .price { font-size: 1.2rem; }
    td, th { padding: 10px 8px; font-size: 0.82rem; }
    .event-chips { gap: 8px; }
}
"""


def load_watchlist() -> list[str]:
    """
    Reads ticker symbols from watchlist.txt.

    Intent:
        Parses a plain-text watchlist, skipping blank lines and # comments,
        and returns normalized uppercase symbols.

    Returns:
        list[str]: Uppercase ticker symbols (e.g. ['AAPL', 'MSFT']).
    """
    if not WATCHLIST_FILE.exists():
        sys.exit(f"watchlist.txt not found at {WATCHLIST_FILE}")

    return [
        line.strip().upper()
        for line in WATCHLIST_FILE.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def fetch_ticker_data(symbol: str) -> dict:
    """
    Fetches price, news, calendar, and previous earnings data for a ticker.

    Intent:
        Aggregates everything needed for one ticker's section of the HTML report.
        Uses a 2-day price history window to compute today's dollar and
        percentage change from yesterday's close.

    Parameters:
        symbol (str): Ticker symbol, e.g. 'AAPL'.

    Returns:
        dict with keys:
            symbol (str): Ticker symbol.
            name (str): Company display name, falls back to symbol on error.
            price (float | None): Latest closing price in USD.
            change (float): Dollar change vs. previous close.
            change_pct (float): Percentage change vs. previous close.
            news (list[dict]): Up to 8 news items (title, link, publisher, published).
            events (list[dict]): Upcoming calendar events (label, value).
            prev_earnings (dict | None): Most recent past earnings result.
            error (str | None): Error message if fetch failed, else None.
    """
    try:
        ticker = yf.Ticker(symbol)

        hist = ticker.history(period="2d")
        if hist.empty:
            return _error_result(symbol, "No price history available")

        current_price = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current_price
        change = current_price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0

        try:
            info = ticker.info
            name = info.get("shortName") or info.get("longName") or symbol
        except Exception:
            name = symbol

        return {
            "symbol": symbol,
            "name": name,
            "price": current_price,
            "change": change,
            "change_pct": change_pct,
            "news": _parse_news(ticker.news or []),
            "events": _parse_calendar(ticker.calendar),
            "prev_earnings": _parse_previous_earnings(ticker),
            "error": None,
        }

    except Exception as exc:
        return _error_result(symbol, str(exc))


def _error_result(symbol: str, message: str) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "price": None,
        "change": 0.0,
        "change_pct": 0.0,
        "news": [],
        "events": [],
        "prev_earnings": None,
        "error": message,
    }


def _parse_news(raw_news: list) -> list[dict]:
    """
    Normalizes yfinance news items into a consistent structure.

    Intent:
        yfinance has shipped two different news payload shapes. Older versions
        return a flat dict per item; newer versions (>=0.2.37) wrap fields under
        a 'content' key. This function handles both transparently.

    Parameters:
        raw_news (list): Raw list returned by ticker.news.

    Returns:
        list[dict]: Up to 8 items, each with:
            title (str): Headline text.
            link (str): URL to the full article.
            publisher (str): Name of the news source.
            published (str): Formatted UTC publication time.
    """
    items = []
    for item in raw_news[:8]:
        if not isinstance(item, dict):
            continue

        content = item.get("content")
        if isinstance(content, dict):
            title = content.get("title", "")
            canonical = content.get("canonicalUrl") or {}
            clickthrough = content.get("clickThroughUrl") or {}
            link = canonical.get("url") or clickthrough.get("url") or ""
            publisher = (content.get("provider") or {}).get("displayName", "")
            pub_raw = content.get("pubDate", "")
            try:
                pub_str = datetime.fromisoformat(pub_raw.replace("Z", "+00:00")).strftime(
                    "%b %d, %Y %H:%M UTC"
                )
            except Exception:
                pub_str = pub_raw
        else:
            title = item.get("title", "")
            link = item.get("link", "")
            publisher = item.get("publisher", "")
            ts = item.get("providerPublishTime")
            pub_str = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y %H:%M UTC")
                if ts
                else ""
            )

        if not title or not link:
            continue
        if any(term in title.lower() for term in _NEWS_TITLE_BLOCKLIST):
            continue
        if any(term in publisher.lower() for term in _NEWS_PUBLISHER_BLOCKLIST):
            continue
        items.append(
            {"title": title, "link": link, "publisher": publisher, "published": pub_str}
        )

    return items


def _parse_calendar(calendar) -> list[dict]:
    """
    Extracts upcoming events from yfinance's calendar data.

    Intent:
        yfinance may return calendar as a dict or DataFrame depending on version
        and data availability. This normalizes both into label/value pairs and
        formats Timestamp objects into human-readable date strings.

    Parameters:
        calendar: Value returned by ticker.calendar.

    Returns:
        list[dict]: Each item has label (str) and value (str).
    """
    if not calendar:
        return []

    if hasattr(calendar, "to_dict"):
        calendar = calendar.to_dict()

    if not isinstance(calendar, dict):
        return []

    events = []
    for key in ("Earnings Date", "Ex-Dividend Date", "Dividend Date"):
        value = calendar.get(key)
        if value is None:
            continue
        if hasattr(value, "__iter__") and not isinstance(value, str):
            dates = [_format_date(d) for d in value if d is not None]
            if dates:
                events.append({"label": key, "value": ", ".join(dates)})
        else:
            events.append({"label": key, "value": _format_date(value)})

    return events


def _parse_previous_earnings(ticker) -> dict | None:
    """
    Fetches the most recent completed earnings date and EPS result for a ticker.

    Intent:
        Queries yfinance's earnings_dates DataFrame, filters to past dates only,
        and returns the most recent row's EPS reported vs. estimated figures.
        Handles both timezone-aware and timezone-naive DataFrame indexes.

    Parameters:
        ticker: yfinance Ticker object.

    Returns:
        dict with keys date (str), reported_eps (float | None),
        estimated_eps (float | None), surprise_pct (float | None);
        or None if no past earnings data is available.
    """
    try:
        import pandas as pd

        earnings = ticker.earnings_dates
        if earnings is None or earnings.empty:
            return None

        idx = earnings.index
        try:
            now_cmp = pd.Timestamp.now(tz="UTC")
            past = earnings[idx < now_cmp]
        except TypeError:
            now_cmp = pd.Timestamp.now()
            past = earnings[idx.tz_localize(None) < now_cmp]

        if past.empty:
            return None

        row = past.iloc[0]

        def safe_float(val) -> float | None:
            try:
                f = float(val)
                return None if math.isnan(f) else f
            except (TypeError, ValueError):
                return None

        date_val = row.name
        date_str = date_val.strftime("%b %d, %Y") if hasattr(date_val, "strftime") else str(date_val)

        return {
            "date": date_str,
            "reported_eps": safe_float(row.get("Reported EPS")),
            "estimated_eps": safe_float(row.get("EPS Estimate")),
            "surprise_pct": safe_float(row.get("Surprise(%)")),
        }

    except Exception:
        return None


def _format_date(value) -> str:
    """
    Formats a yfinance date value as a human-readable string.

    Parameters:
        value: A pandas Timestamp, datetime, date, or string.

    Returns:
        str: Formatted as 'Mon DD, YYYY', or str(value) if formatting fails.
    """
    try:
        if hasattr(value, "strftime"):
            return value.strftime("%b %d, %Y")
        return str(value)
    except Exception:
        return str(value)


def generate_summaries(stocks: list[dict]) -> dict:
    """
    Uses the Gemini API to generate a market overview and per-ticker summaries.

    Intent:
        Makes a single API call with all stock data (prices, news headlines,
        previous earnings) and asks Gemini to write synthesized prose summaries.
        Gracefully returns an empty dict if the API key is missing or the call
        fails, so the report still renders without summaries.

    Parameters:
        stocks (list[dict]): List of results from fetch_ticker_data().

    Returns:
        dict with optional keys:
            overview (str): 1-2 paragraph overall market summary.
            summaries (dict[str, str]): Symbol -> 1-2 paragraph per-ticker summary.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("  Note: GROQ_API_KEY not set — skipping AI summaries")
        return {}

    client = Groq(api_key=api_key)
    system_instruction = (
        "You are a financial analyst writing a concise daily stock digest.\n\n"
        "Given stock performance data and recent news headlines, produce a JSON response with:\n"
        '1. "overview": a JSON array of exactly 2-3 strings. Each string is one standalone bullet '
        "highlighting the single most noteworthy company-specific event of the day across all stocks — "
        "earnings reports, major product launches, acquisitions, regulatory decisions, leadership changes, "
        "or other significant announcements. Each bullet must name the company and describe the event "
        "concretely. Do NOT describe price movement or generic market conditions — those are already "
        "visible in the table. Prioritize events with real business impact over routine analyst notes.\n"
        '2. "summaries": an object mapping each ticker symbol to 1-2 paragraphs of plain '
        "prose covering that stock's performance, news drivers, and what investors should watch.\n\n"
        "Write in clear, professional financial prose. Use no markdown formatting.\n\n"
        "Return ONLY valid JSON. No backticks, no explanation outside the JSON object."
    )

    today = datetime.now(tz=timezone.utc).strftime("%A, %B %d, %Y")
    ticker_blocks = []
    for stock in stocks:
        if stock["error"]:
            ticker_blocks.append(f"{stock['symbol']}: data unavailable ({stock['error']})")
            continue

        sign = "+" if stock["change_pct"] >= 0 else ""
        lines = [
            f"{stock['symbol']} ({stock['name']}): "
            f"${stock['price']:.2f}, {sign}{stock['change_pct']:.2f}% today"
        ]

        prev = stock.get("prev_earnings")
        if prev:
            eps_line = f"Previous earnings ({prev['date']})"
            if prev.get("reported_eps") is not None:
                eps_line += f": EPS ${prev['reported_eps']:.2f}"
                if prev.get("estimated_eps") is not None:
                    diff = prev["reported_eps"] - prev["estimated_eps"]
                    if diff > 0.005:
                        eps_line += f", beat estimate of ${prev['estimated_eps']:.2f} by ${diff:.2f}"
                    elif diff < -0.005:
                        eps_line += f", missed estimate of ${prev['estimated_eps']:.2f} by ${abs(diff):.2f}"
                    else:
                        eps_line += f", met estimate of ${prev['estimated_eps']:.2f}"
            lines.append(eps_line)

        headlines = [f"  - {n['title']} ({n['publisher']})" for n in stock["news"][:5]]
        if headlines:
            lines.append("Recent headlines:")
            lines.extend(headlines)
        else:
            lines.append("No recent news available.")

        ticker_blocks.append("\n".join(lines))

    user_content = f"Today is {today}.\n\n" + "\n\n---\n\n".join(ticker_blocks)

    try:
        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content},
            ],
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content
        # Strip markdown code fences if the model wraps the JSON
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1)
        return json.loads(text.strip())
    except Exception as exc:
        print(f"  Warning: summary generation failed — {exc}")
        return {}


def _render_paragraphs(text: str) -> str:
    """
    Converts a plain-text multi-paragraph string into HTML paragraph tags.

    Parameters:
        text (str): Text with paragraphs separated by one or more blank lines.

    Returns:
        str: HTML string of <p> elements.
    """
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paras:
        paras = [text.strip()]
    return "".join(f"<p>{html.escape(p)}</p>" for p in paras)


def _render_bullets(items) -> str:
    if isinstance(items, str):
        lines = [l.strip().lstrip("•-* ") for l in items.split("\n") if l.strip()]
    else:
        lines = [str(i).strip().lstrip("•-* ") for i in items if str(i).strip()]
    if not lines:
        return ""
    lis = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    return f"<ul>{lis}</ul>"


def build_html(stocks: list[dict], generated_at: datetime, summaries: dict) -> str:
    """
    Renders the complete HTML report as a string.

    Intent:
        Produces a self-contained, styled HTML file with a sticky header,
        an optional AI-generated digest summary, a clickable overview table,
        and per-ticker sections containing a prose summary, events (including
        previous earnings), and news articles.

    Parameters:
        stocks (list[dict]): List of results from fetch_ticker_data().
        generated_at (datetime): UTC timestamp of report generation.
        summaries (dict): Output from generate_summaries(); may be empty.

    Returns:
        str: Complete HTML document, ready to write to disk.
    """
    date_str = generated_at.strftime("%A, %B %d, %Y")
    time_str = generated_at.strftime("%H:%M UTC")

    overview_html = ""
    if summaries.get("overview"):
        overview_html = (
            f'<div class="digest-card">'
            f'<div class="card-title">Today\'s Digest</div>'
            f'<div class="digest-text">{_render_bullets(summaries["overview"])}</div>'
            f'</div>'
        )

    summary_rows = "\n        ".join(_summary_row(s) for s in stocks)
    ticker_summaries = summaries.get("summaries", {})
    detail_sections = "\n  ".join(_ticker_section(s, ticker_summaries.get(s["symbol"], "")) for s in stocks)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Stock Digest &mdash; {date_str}</title>
  <style>{_CSS}</style>
</head>
<body>
<header id="top">
  <a href="/" style="display:block;color:rgba(255,255,255,0.7);font-size:0.8rem;text-decoration:none;margin-bottom:6px;">← Home</a>
  <h1>Stock Digest</h1>
  <p class="subtitle">{date_str} &nbsp;&middot;&nbsp; Generated {time_str}</p>
</header>
<main>
  {overview_html}
  <div class="summary-card">
    <div class="card-title">Overview</div>
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Company</th>
          <th class="r">Price</th>
          <th class="r">Change</th>
          <th class="r">Change %</th>
        </tr>
      </thead>
      <tbody>
        {summary_rows}
      </tbody>
    </table>
  </div>
  {detail_sections}
</main>
</body>
</html>"""


def _summary_row(stock: dict) -> str:
    symbol = html.escape(stock["symbol"])
    name = html.escape(stock["name"])

    if stock["error"]:
        return (
            f'<tr>'
            f'<td><a class="ticker-link" href="#{symbol}">{symbol}</a></td>'
            f'<td>{name}</td>'
            f'<td colspan="3" class="r"><span class="error-tag">Fetch error</span></td>'
            f'</tr>'
        )

    price_str = f"${stock['price']:,.2f}"
    change = stock["change"]
    change_pct = stock["change_pct"]
    css = "pos" if change > 0 else ("neg" if change < 0 else "neu")

    if change > 0:
        change_str, pct_str = f"+${change:.2f}", f"+{change_pct:.2f}%"
    elif change < 0:
        change_str, pct_str = f"-${abs(change):.2f}", f"{change_pct:.2f}%"
    else:
        change_str, pct_str = "$0.00", "0.00%"

    return (
        f'<tr onclick="location.href=\'#{symbol}\'">'
        f'<td><a class="ticker-link" href="#{symbol}">{symbol}</a></td>'
        f'<td>{name}</td>'
        f'<td class="r">{price_str}</td>'
        f'<td class="r {css}">{change_str}</td>'
        f'<td class="r {css}">{pct_str}</td>'
        f'</tr>'
    )


def _prev_earnings_chip(prev: dict) -> str:
    """
    Renders the previous earnings result as an HTML chip.

    Parameters:
        prev (dict): Output from _parse_previous_earnings().

    Returns:
        str: HTML for a single chip element.
    """
    date_str = html.escape(prev["date"])
    chip = (
        f'<div class="chip chip-past">'
        f'<div class="chip-label">Prev Earnings</div>'
        f'<div class="chip-value">{date_str}</div>'
    )

    reported = prev.get("reported_eps")
    estimated = prev.get("estimated_eps")

    if reported is not None:
        eps_str = f"EPS ${reported:.2f}"
        eps_css = "neu"
        if estimated is not None:
            diff = reported - estimated
            if diff > 0.005:
                eps_str += f" · beat by ${diff:.2f}"
                eps_css = "pos"
            elif diff < -0.005:
                eps_str += f" · missed by ${abs(diff):.2f}"
                eps_css = "neg"
            else:
                eps_str += " · met est."
        chip += f'<div class="chip-eps {eps_css}">{html.escape(eps_str)}</div>'

    chip += "</div>"
    return chip


def _ticker_section(stock: dict, summary: str) -> str:
    symbol = html.escape(stock["symbol"])
    name = html.escape(stock["name"])

    if stock["price"] is not None:
        change = stock["change"]
        change_pct = stock["change_pct"]
        css = "pos" if change > 0 else ("neg" if change < 0 else "neu")
        if change > 0:
            change_label = f"+${change:.2f} (+{change_pct:.2f}%)"
        elif change < 0:
            change_label = f"-${abs(change):.2f} ({change_pct:.2f}%)"
        else:
            change_label = "$0.00 (0.00%)"
        price_html = (
            f'<div class="price-block">'
            f'<div class="price">${stock["price"]:,.2f}</div>'
            f'<div class="change {css}">{change_label}</div>'
            f'</div>'
        )
    else:
        price_html = '<div class="price-block"><span class="error-tag">No data</span></div>'

    summary_html = ""
    if summary:
        summary_html = (
            f'<div class="ticker-summary">{_render_paragraphs(summary)}</div>'
        )

    # Events: upcoming calendar entries + previous earnings
    all_chips = ""
    if stock.get("prev_earnings"):
        all_chips += _prev_earnings_chip(stock["prev_earnings"])
    for e in stock.get("events", []):
        all_chips += (
            f'<div class="chip">'
            f'<div class="chip-label">{html.escape(e["label"])}</div>'
            f'<div class="chip-value">{html.escape(e["value"])}</div>'
            f'</div>'
        )

    events_html = ""
    if all_chips:
        events_html = (
            f'<div class="events">'
            f'<div class="section-label">Events</div>'
            f'<div class="event-chips">{all_chips}</div>'
            f'</div>'
        )

    if stock["news"]:
        news_items = "".join(
            f'<div class="news-item">'
            f'<a href="{html.escape(n["link"])}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(n["title"])}</a>'
            f'<div class="news-meta">'
            f'{html.escape(n["publisher"])}'
            f'{" &middot; " + html.escape(n["published"]) if n["published"] else ""}'
            f'</div>'
            f'</div>'
            for n in stock["news"]
        )
        news_html = (
            f'<div class="news-list">'
            f'<div class="section-label">Recent News</div>'
            f'{news_items}'
            f'</div>'
        )
    elif stock["error"]:
        news_html = f'<p class="no-data">Could not fetch data: {html.escape(stock["error"])}</p>'
    else:
        news_html = '<p class="no-data">No recent news available.</p>'

    return (
        f'<div class="ticker-card" id="{symbol}">'
        f'<div class="ticker-head">'
        f'<div>'
        f'<h2>{symbol}</h2>'
        f'<div class="co-name">{name}</div>'
        f'</div>'
        f'{price_html}'
        f'</div>'
        f'{summary_html}'
        f'{events_html}'
        f'{news_html}'
        f'<a class="back-top" href="#top">&uarr; Back to overview</a>'
        f'</div>'
    )


def main() -> None:
    """
    Entry point. Loads the watchlist, fetches data for each ticker, generates
    AI summaries, and writes the HTML report to output/report.html.
    """
    symbols = load_watchlist()
    if not symbols:
        sys.exit("watchlist.txt is empty")

    print(f"Fetching data for {len(symbols)} ticker(s)...")
    stocks = []
    for symbol in symbols:
        print(f"  {symbol}...", end=" ", flush=True)
        data = fetch_ticker_data(symbol)
        stocks.append(data)
        print("error" if data["error"] else "ok")

    print("\nGenerating AI summaries...")
    summaries = generate_summaries(stocks)

    generated_at = datetime.now(tz=timezone.utc)
    report_html = build_html(stocks, generated_at, summaries)

    output_file = OUTPUT_DIR / "index.html"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file.write_text(report_html, encoding="utf-8")
    print(f"\nReport written to {output_file}")


if __name__ == "__main__":
    main()
