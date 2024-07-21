import bcrypt

### Password hashing and checking functions ###

def hash_password(password: str) -> bytes:
    return bcrypt.hashpw(password.encode('utf-8'), salt = bcrypt.gensalt())

def compare_password(password: str, hashed_password: bytes) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed_password)


