"""
Slug Generator Tests — 12 Tests

Tests the Base62 encoding/decoding, random slug generation,
collision handling, and edge cases.

TESTING STRATEGY:
  These are pure unit tests — no I/O, no mocks needed.
  The slug generator is a pure function (input → output),
  making it the easiest component to test thoroughly.
"""

import pytest

from app.services.slug_generator import (
    BASE62_CHARS,
    decode_base62,
    encode_base62,
    generate_short_code,
    generate_short_code_with_retry,
)


class TestBase62Encoding:
    """Tests for the Base62 encode/decode functions."""

    def test_encode_zero(self) -> None:
        """Zero should encode to the first character in the alphabet."""
        assert encode_base62(0) == "0"

    def test_encode_single_digit(self) -> None:
        """Numbers 0-61 should encode to single characters."""
        assert encode_base62(9) == "9"
        assert encode_base62(10) == "a"
        assert encode_base62(35) == "z"
        assert encode_base62(36) == "A"
        assert encode_base62(61) == "Z"

    def test_encode_multi_digit(self) -> None:
        """Numbers >= 62 should encode to multiple characters."""
        assert encode_base62(62) == "10"
        assert encode_base62(124) == "20"

    def test_encode_large_number(self) -> None:
        """Large numbers should produce longer strings."""
        result = encode_base62(1_000_000)
        assert len(result) > 1
        assert all(c in BASE62_CHARS for c in result)

    def test_encode_negative_raises_error(self) -> None:
        """Negative numbers should raise ValueError."""
        with pytest.raises(ValueError, match="Cannot encode negative"):
            encode_base62(-1)

    def test_decode_single_char(self) -> None:
        """Single characters should decode to their index."""
        assert decode_base62("0") == 0
        assert decode_base62("Z") == 61

    def test_decode_multi_char(self) -> None:
        """Multi-character strings should decode correctly."""
        assert decode_base62("10") == 62
        assert decode_base62("20") == 124

    def test_encode_decode_roundtrip(self) -> None:
        """Encoding then decoding should return the original number."""
        for num in [0, 1, 42, 62, 1000, 999_999, 1_000_000_000]:
            assert decode_base62(encode_base62(num)) == num

    def test_decode_empty_string_raises_error(self) -> None:
        """Empty string should raise ValueError."""
        with pytest.raises(ValueError, match="Cannot decode empty"):
            decode_base62("")

    def test_decode_invalid_char_raises_error(self) -> None:
        """Invalid characters should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid Base62 character"):
            decode_base62("abc!")


class TestShortCodeGeneration:
    """Tests for random short code generation."""

    def test_default_length(self) -> None:
        """Default generation should produce 7-character codes."""
        code = generate_short_code()
        assert len(code) == 7

    def test_custom_length(self) -> None:
        """Custom length should be respected."""
        for length in [4, 5, 6, 8, 10]:
            code = generate_short_code(length=length)
            assert len(code) == length

    def test_only_base62_characters(self) -> None:
        """Generated codes should only contain Base62 characters."""
        for _ in range(100):
            code = generate_short_code()
            assert all(c in BASE62_CHARS for c in code)

    def test_uniqueness(self) -> None:
        """
        100 generated codes should all be unique.

        With 62^7 keyspace, duplicate probability in 100 tries
        is astronomically low (~10^-10).
        """
        codes = {generate_short_code() for _ in range(100)}
        assert len(codes) == 100

    def test_randomness(self) -> None:
        """
        Two consecutive generations should produce different codes.

        This verifies the random seed is actually changing.
        """
        code1 = generate_short_code()
        code2 = generate_short_code()
        assert code1 != code2


class TestCollisionHandling:
    """Tests for slug generation with collision retry."""

    def test_no_collision(self) -> None:
        """Should succeed on first try when no collisions exist."""
        code = generate_short_code_with_retry(existing_codes=set())
        assert len(code) == 7

    def test_retry_on_collision(self) -> None:
        """
        Should retry and succeed when existing codes cause collision.

        We pre-fill the set with codes, but since the keyspace is huge,
        the generator should find a non-colliding code quickly.
        """
        existing = {"aBcDeFg", "HiJkLmN"}
        code = generate_short_code_with_retry(existing_codes=existing)
        assert code not in existing

    def test_max_retries_exhausted(self) -> None:
        """
        Should raise SlugCollisionException when all retries fail.

        We mock this by creating a set that contains ALL possible codes
        (impossible in reality, but tests the error path).
        """
        from unittest.mock import patch
        from app.exceptions import SlugCollisionException

        # Mock generate_short_code to always return the same colliding code
        with patch(
            "app.services.slug_generator.generate_short_code",
            return_value="COLLIDE",
        ):
            with pytest.raises(SlugCollisionException):
                generate_short_code_with_retry(
                    existing_codes={"COLLIDE"},
                    max_retries=3,
                )

    def test_custom_length_with_retry(self) -> None:
        """Should respect custom length even with retry logic."""
        code = generate_short_code_with_retry(
            existing_codes=set(), length=5
        )
        assert len(code) == 5
