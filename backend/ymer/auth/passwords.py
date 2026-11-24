"""Password hashing (scrypt via Werkzeug)."""

from werkzeug.security import check_password_hash, generate_password_hash

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256

# Checked against when the email is unknown, so a failed login costs the same either way
# and response timing does not reveal which emails have accounts.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if password_hash is None:
        check_password_hash(_DUMMY_HASH, password)
        return False
    return check_password_hash(password_hash, password)
