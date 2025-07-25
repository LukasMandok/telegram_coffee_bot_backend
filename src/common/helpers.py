import bcrypt
import re

### Password hashing and checking functions ###

BCRYPT_HASH_REGEX = re.compile(r"^\$(2[aby])\$(\d{2})\$[./A-Za-z0-9]{53}$")

def is_valid_hash(s: str) -> bool:
    """
    Return True if s looks like a bcrypt hash string.
    """
    return bool(BCRYPT_HASH_REGEX.match(s))

def hash_password(password: str) -> bytes:
    """
    Hash a password using bcrypt.
    Args:
        password: The plaintext password to hash.
    Returns:
        The hashed password as bytes.
    """
    return bcrypt.hashpw(password.encode('utf-8'), salt = bcrypt.gensalt())

def compare_password(password: str, hashed_password: bytes) -> bool:
    """
    Compare a plaintext password with a hashed password.
    Args:
        password: The plaintext password to check.
        hashed_password: The hashed password to compare against.
    Returns:
        True if the password matches the hash, False otherwise.
    """
    print(f"Comparing password '{password}' with saved hash: {hashed_password}")
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password)


