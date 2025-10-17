"""
PayPal utility functions.

This module contains PayPal-related utilities that can be used across the application
without causing circular imports.
"""


def create_paypal_link(paypal_input: str) -> str:
    """
    Create a normalized PayPal.me link from username or existing PayPal link.

    - Accepts: username, @username, paypal.me/username, https://paypal.me/username, https://www.paypal.me/username
    - Normalizes to: https://paypal.me/<username>
    - Strips extra path segments and trailing slashes
    """
    from urllib.parse import urlparse

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

    return f"https://paypal.me/{username}" if username else ""


def validate_paypal_link(paypal_link: str) -> bool:
    """
    Validate a PayPal.me link by checking if it exists.
    
    Args:
        paypal_link: Full PayPal.me URL to validate
        
    Returns:
        True if the link is valid (returns 2xx/3xx), False otherwise
    """
    import requests
    link = (paypal_link or "").strip()
    if not link:
        return False

    try:
        username = link.split("paypal.me/")[-1].split("/")[0]
        if not username:
            return False
        url = f"https://www.paypal.com/paypalme/{username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        r = requests.get(url, headers=headers, timeout=8.0, allow_redirects=True)
        if r.status_code in (404, 410):
            return False
        if 200 <= r.status_code < 400:
            return True
        return False
    except Exception:
        return False

