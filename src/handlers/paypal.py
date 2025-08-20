"""
PayPal utility functions.

This module contains PayPal-related utilities that can be used across the application
without causing circular imports.
"""


def create_paypal_link(paypal_input: str) -> str:
    """
    Create a PayPal payment link from username or existing PayPal link.
    
    Args:
        paypal_input: PayPal username or existing PayPal.me link
        
    Returns:
        Formatted PayPal.me link
    """
    paypal_input = paypal_input.strip()
    
    if paypal_input.startswith('paypal.me/'):
        return f"https://{paypal_input}"
    elif paypal_input.startswith('https://paypal.me/'):
        return paypal_input
    elif paypal_input.startswith('www.paypal.me/'):
        return f"https://{paypal_input}"
    else:
        # Assume it's a username
        return f"https://paypal.me/{paypal_input}"


def validate_paypal_link(paypal_link: str) -> bool:
    """
    Validate a PayPal.me link by checking if it exists (synchronous version).
    
    Args:
        paypal_link: Full PayPal.me URL to validate
        
    Returns:
        True if the link is valid (returns 200), False otherwise
    """
    import requests
    
    try:
        # Extract the path from the full URL for the request
        # e.g., "https://paypal.me/LukasMandok" -> "/paypalme/LukasMandok"
        username = paypal_link.split('paypal.me/')[-1]
        url = f"https://www.paypal.com/paypalme/{username}"
        
        response = requests.get(url, timeout=10.0, allow_redirects=True)
        return response.status_code == 200
            
    except Exception:
        return False

