"""
PayPal utility functions.

This module contains PayPal-related utilities that can be used across the application
without causing circular imports.
"""

import re
import httpx
from urllib.parse import urlparse
from ..common.log import Logger

logger = Logger("PayPalValidator")

def create_paypal_link(paypal_input: str) -> tuple[str, str]:
    """
    Create a normalized PayPal.me link from username or existing PayPal link.

    - Accepts: username, @username, paypal.me/username, https://paypal.me/username, https://www.paypal.me/username
    - Normalizes to: https://paypal.me/<username>
    - Strips extra path segments and trailing slashes
    """

    raw = (paypal_input or "").strip()
    if not raw:
        return ""

    # Strip leading @
    if raw.startswith("@"):  # e.g., @tttkar
        raw = raw[1:]

    # If it's a URL, extract the username path segment
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        # Only accept paypal.me domains
        if parsed.netloc.endswith("paypal.me") or parsed.netloc.endswith("www.paypal.me"):
            # Extract first non-empty path segment as username
            parts = [p for p in parsed.path.split("/") if p]
            username = parts[0] if parts else ""
        else:
            # Not a paypal.me link; treat the hostname/path blob as username fallback
            username = parsed.path.strip("/") or parsed.netloc.split(".")[0]
    else:
        # Remove common prefixes like paypal.me/username or www.paypal.me/username
        prefixes = ("paypal.me/", "www.paypal.me/")
        for pref in prefixes:
            if raw.lower().startswith(pref):
                raw = raw[len(pref):]
                break
        username = raw.strip("/")

    return f"https://paypal.me/{username}" if username else "", username



URL_PATTERN = re.compile(r'^https://(www\.)?paypal\.me/[A-Za-z0-9._-]+$', re.I)
OG_URL_PATTERN = re.compile(r'<meta property="og:url" content="([^"]+)"', re.I)

async def validate_paypal_link(url: str, username: str | None = None) -> bool:
    """
    Validate a PayPal.Me link.

    Returns:
        True if the link exists (og:url found) and username matches (if provided), False otherwise
    """
    if not URL_PATTERN.match(url):
        logger.debug(f"Invalid URL format: {url}")
        return False

    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.debug(f"{url} → HTTP {resp.status_code}")
            return False

        text = resp.text

        # Extract og:url - if it exists, the PayPal account exists
        og_url_match = OG_URL_PATTERN.search(text)
        if not og_url_match:
            logger.debug(f"No og:url found for {url} - account does not exist")
            return False

        og_url = og_url_match.group(1).lower()
        logger.debug(f"Found og:url: {og_url}")
        
        # If username was provided, check if it's contained in the og:url
        if username:
            normalized_username = username.lower()
            if normalized_username not in og_url:
                logger.debug(f"Username '{username}' not found in og:url: {og_url}")
                return False
            logger.debug(f"Username '{username}' verified in og:url")
        
        logger.debug(f"PayPal.Me link valid: {url}")
        return True