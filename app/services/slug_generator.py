"""
Slug Generator — Base62 Short Code Generation

ALGORITHM DESIGN:

  We use Base62 encoding (a-z, A-Z, 0-9) to generate compact short codes.
  With 7 characters, we get 62^7 = ~3.5 TRILLION possible slugs — enough
  for virtually any scale.

  HOW IT WORKS:
  1. Generate a random 64-bit integer
  2. Encode it in Base62 to get a compact alphanumeric string
  3. Truncate to desired length (default: 7 characters)

  COLLISION HANDLING STRATEGY:
  - On collision (duplicate short_code in DB), we retry with a new random seed
  - Max retries = 5 before raising SlugCollisionException
  - At 10M URLs with 62^7 keyspace, collision probability per attempt is ~0.0000003%
  - Expected retries before collision at 50% keyspace: still negligible

  WHY NOT HASH-BASED (e.g., MD5/SHA256 of URL)?
  - Same URL always produces same hash → good for deduplication
  - BUT hash collisions are harder to handle (need salt strategy)
  - AND you lose the ability to give different short codes to the same URL
  - We use random generation for simplicity + append-only uniqueness

  INTERVIEW TALKING POINT — ALTERNATIVES:
  - Counter-based (auto-increment ID → Base62): simplest, but exposes creation order
  - Snowflake IDs: distributed unique IDs, used by Twitter
  - Pre-generated pool: generate slugs in advance, pop from queue (ZooKeeper-backed)
  - Hash + salt: deterministic but with collision retry
"""

import random
import string

from app.core.logging import get_logger

logger = get_logger(__name__)

# Base62 character set: digits + lowercase + uppercase
BASE62_CHARS: str = string.digits + string.ascii_lowercase + string.ascii_uppercase
BASE: int = len(BASE62_CHARS)  # 62


def encode_base62(number: int) -> str:
    """
    Encode a non-negative integer into a Base62 string.

    Base62 uses characters [0-9, a-z, A-Z] to represent numbers compactly.
    This is the same concept as hexadecimal (Base16) but with more characters,
    resulting in shorter strings.

    Args:
        number: Non-negative integer to encode.

    Returns:
        Base62-encoded string.

    Examples:
        >>> encode_base62(0)
        '0'
        >>> encode_base62(61)
        'Z'
        >>> encode_base62(62)
        '10'
    """
    if number < 0:
        raise ValueError("Cannot encode negative numbers in Base62")

    if number == 0:
        return BASE62_CHARS[0]

    result: list[str] = []
    while number > 0:
        remainder = number % BASE
        result.append(BASE62_CHARS[remainder])
        number //= BASE

    return "".join(reversed(result))


def decode_base62(encoded: str) -> int:
    """
    Decode a Base62 string back to an integer.

    Useful for analytics or debugging — convert a short code back
    to its numeric representation.

    Args:
        encoded: Base62-encoded string.

    Returns:
        Decoded integer value.

    Raises:
        ValueError: If string contains invalid characters.
    """
    if not encoded:
        raise ValueError("Cannot decode empty string")

    number = 0
    for char in encoded:
        index = BASE62_CHARS.find(char)
        if index == -1:
            raise ValueError(f"Invalid Base62 character: '{char}'")
        number = number * BASE + index

    return number


def generate_short_code(length: int = 7) -> str:
    """
    Generate a random Base62 short code of specified length.

    WHY RANDOM (not sequential)?
      Sequential codes (abc001, abc002...) expose:
      - How many URLs exist (competitive intelligence)
      - Creation order (privacy concern)
      - Easy enumeration (security risk — scrape all URLs)
      Random codes prevent all three.

    Args:
        length: Desired length of the short code (default: 7).
                7 chars = 62^7 = ~3.5 trillion possibilities.

    Returns:
        Random Base62 string of specified length.
    """
    # Generate a random number in range [62^(length-1), 62^length - 1]
    # This ensures the result always has exactly `length` characters
    min_value = BASE ** (length - 1)
    max_value = BASE**length - 1
    random_number = random.randint(min_value, max_value)

    return encode_base62(random_number)


def generate_short_code_with_retry(
    existing_codes: set[str],
    length: int = 7,
    max_retries: int = 5,
) -> str:
    """
    Generate a unique short code, retrying on collision.

    This is the function called by the URL service. It takes a set of
    existing codes (from the DB query) and retries if a collision occurs.

    In a distributed system, you'd replace the `existing_codes` set with
    an atomic DB insert + unique constraint check. The set-based approach
    here is for unit testability.

    Args:
        existing_codes: Set of short codes already in use.
        length: Desired slug length.
        max_retries: Maximum attempts before giving up.

    Returns:
        A unique short code not present in existing_codes.

    Raises:
        SlugCollisionException: If all retries fail (keyspace exhaustion).
    """
    from app.exceptions import SlugCollisionException

    for attempt in range(max_retries):
        code = generate_short_code(length)

        if code not in existing_codes:
            if attempt > 0:
                logger.info(
                    f"Slug generated after {attempt + 1} attempts",
                    extra={"extra_data": {"short_code": code, "attempts": attempt + 1}},
                )
            return code

        logger.warning(
            f"Slug collision on attempt {attempt + 1}",
            extra={"extra_data": {"collided_code": code, "attempt": attempt + 1}},
        )

    raise SlugCollisionException()
