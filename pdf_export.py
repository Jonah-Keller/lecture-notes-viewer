"""Server-side HTML→PDF rendering via headless Chromium (Playwright).

Loads a URL on the local Flask server, waits for KaTeX math + slide images
to settle, and returns PDF bytes with the print stylesheet applied.

A module-level lock serializes Playwright invocations because the sync API
is not safe to use from multiple threads at once.
"""

from __future__ import annotations

import threading
from typing import Optional


_LOCK = threading.Lock()


def render_url_to_pdf(
    url: str,
    *,
    wait_ms: int = 1500,
    timeout_ms: int = 90_000,
) -> bytes:
    """Open `url` in headless Chromium, wait for content/math to render, and
    return PDF bytes (A4, print backgrounds enabled, narrow margins)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "playwright not installed. Run:\n"
            "    .venv/bin/pip install playwright\n"
            "    .venv/bin/playwright install chromium"
        ) from e

    with _LOCK:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                # Give KaTeX auto-render a moment to finish replacing math
                # spans after networkidle (it runs onload-deferred).
                page.wait_for_timeout(wait_ms)
                pdf_bytes = page.pdf(
                    format="A4",
                    print_background=True,
                    margin={
                        "top": "1.4cm",
                        "bottom": "1.4cm",
                        "left": "1.2cm",
                        "right": "1.2cm",
                    },
                )
            finally:
                browser.close()
    return pdf_bytes
