"""Regression tests for database credential encoding (audit finding #1).

The defect
----------
`encode_db_url()` used a heuristic ("does the credential contain a %XX
escape?") to decide whether a password was already percent-encoded. That
heuristic is undecidable: the raw password `pa%41ss` and the encoded form
of `paAss` are the SAME string. Guessing wrong silently corrupts the
credential and the server then rejects the login with a misleading
"password authentication failed".

The fix
-------
The encoding state comes from outside the URL:
  * `DB_USER`/`DB_PASSWORD` (+ `DB_PASSWORD_ENCODED`) — the password is the
    literal value and is encoded exactly once.
  * `DATABASE_URL` — the embedded password is treated as already encoded and
    is passed through byte-for-byte (never re-encoded).

These tests pin both guarantees: no double encoding, no wrong decoding.
"""
import os

import pytest

from app.config import Settings, encode_db_url, effective_db_password


def _settings(**overrides):
    """Build a Settings instance with an explicit, hermetic environment."""
    base = {
        "ENV": "dev",
        "DATABASE_URL": "sqlite:///./data/radar.db",
        "DB_HOST": "",
        "DB_PORT": 5432,
        "DB_NAME": "",
        "DB_USER": "",
        "DB_PASSWORD": "",
        "DB_PASSWORD_ENCODED": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _split(url):
    """Split a URL into (user, password, host) the way a driver would see it."""
    from urllib.parse import unquote, urlsplit
    p = urlsplit(url)
    netloc = p.netloc.rsplit("@", 1)[0]
    user, _, pwd = netloc.partition(":")
    return unquote(user), unquote(pwd), p.hostname


# --------------------------------------------------------------------------
# 1. Raw credentials via the explicit DB_* parts
# --------------------------------------------------------------------------
class TestRawCredentials:
    def test_simple_password_is_unchanged(self):
        s = _settings(DB_USER="radar", DB_PASSWORD="SimplePass123",
                      DB_HOST="db", DB_NAME="radar")
        url = encode_db_url(s)
        user, pwd, host = _split(url)
        assert user == "radar"
        assert pwd == "SimplePass123"
        assert host == "db"

    @pytest.mark.parametrize("raw", [
        "p@ssw0rd",        # @ — the character that ends the userinfo
        "pa:ss",           # :
        "pa/ss",           # / — would end the path
        "pa%ss",           # % — looks like an escape but is literal
        "pa+ss",           # + — decodes to a SPACE in some parsers
        "pa#ss",           # # — would start the fragment
        "pa ss",           # literal space
        "pa%41ss",         # literal %41 — the ambiguous case
        "p%40ss",          # another ambiguous literal
        "100%25pure",      # percent sign in a percentage
        "a@b:c/d%e+f#g h", # everything at once
    ])
    def test_special_characters_round_trip_exactly(self, raw):
        """The password the server sees must equal the password that was set."""
        s = _settings(DB_USER="radar", DB_PASSWORD=raw,
                      DB_HOST="db", DB_NAME="radar")
        url = encode_db_url(s)
        user, pwd, host = _split(url)
        assert pwd == raw, f"password corrupted: {raw!r} -> {pwd!r}"
        assert user == "radar"
        assert host == "db"

    def test_username_with_special_characters_round_trips(self):
        s = _settings(DB_USER="user@corp", DB_PASSWORD="pw",
                      DB_HOST="db", DB_NAME="radar")
        user, pwd, host = _split(encode_db_url(s))
        assert user == "user@corp"
        assert pwd == "pw"

    def test_port_is_included(self):
        s = _settings(DB_USER="radar", DB_PASSWORD="pw",
                      DB_HOST="db", DB_PORT=6543, DB_NAME="radar")
        assert ":6543/" in encode_db_url(s)

    def test_driver_prefix_is_always_psycopg(self):
        s = _settings(DB_USER="radar", DB_PASSWORD="pw",
                      DB_HOST="db", DB_NAME="radar")
        assert encode_db_url(s).startswith("postgresql+psycopg://")

    def test_password_is_encoded_exactly_once(self):
        """A literal '@' must become %40 — never %2540 (double encoding)."""
        s = _settings(DB_USER="radar", DB_PASSWORD="p@ss",
                      DB_HOST="db", DB_NAME="radar")
        url = encode_db_url(s)
        assert "p%40ss" in url
        assert "%2540" not in url, "double encoding detected"
        assert url.count("%40") == 1


# --------------------------------------------------------------------------
# 2. The ambiguous case: raw '%41' must NOT be decoded
# --------------------------------------------------------------------------
class TestNoWrongDecoding:
    def test_raw_percent_41_is_not_treated_as_encoded(self):
        """The exact defect: raw 'pa%41ss' must survive as 'pa%41ss'.

        The old heuristic saw '%41' and assumed the value was already
        encoded, leaving it untouched; psycopg then decoded it to 'paAss'
        and the login failed with a misleading error.
        """
        s = _settings(DB_USER="radar", DB_PASSWORD="pa%41ss",
                      DB_HOST="db", DB_NAME="radar")
        url = encode_db_url(s)
        assert "%2541" in url, "raw %41 must be escaped to %2541"
        _, pwd, _ = _split(url)
        assert pwd == "pa%41ss"

    def test_literal_percent_hex_sequences_survive(self):
        for raw in ["pa%41ss", "p%40ss", "%25", "%2F", "a%00b", "%e4%b8%ad"]:
            s = _settings(DB_USER="radar", DB_PASSWORD=raw,
                          DB_HOST="db", DB_NAME="radar")
            _, pwd, _ = _split(encode_db_url(s))
            assert pwd == raw, f"{raw!r} was corrupted into {pwd!r}"

    def test_encoded_flag_passes_value_through_untouched(self):
        """An operator who deliberately stores an encoded value is honoured."""
        s = _settings(DB_USER="radar", DB_PASSWORD="p%40ss",
                      DB_HOST="db", DB_NAME="radar",
                      DB_PASSWORD_ENCODED=True)
        url = encode_db_url(s)
        assert "%2540" not in url, "already-encoded value must not be re-encoded"
        _, pwd, _ = _split(url)
        assert pwd == "p@ss", "encoded input must decode back to the original"


# --------------------------------------------------------------------------
# 3. DATABASE_URL path: never re-encode
# --------------------------------------------------------------------------
class TestDatabaseUrlPath:
    def test_already_encoded_url_is_passed_through(self):
        s = _settings(DATABASE_URL="postgresql+psycopg://radar:p%40ss@db:5432/radar")
        url = encode_db_url(s)
        assert "%2540" not in url, "an encoded DATABASE_URL must not be re-encoded"
        _, pwd, _ = _split(url)
        assert pwd == "p@ss"

    def test_plain_database_url_is_passed_through(self):
        s = _settings(DATABASE_URL="postgresql+psycopg://radar:SimplePass123@db:5432/radar")
        url = encode_db_url(s)
        assert "SimplePass123" in url
        _, pwd, _ = _split(url)
        assert pwd == "SimplePass123"

    @pytest.mark.parametrize("raw_url,expected_driver", [
        ("postgres://radar:pw@db:5432/radar", "postgresql+psycopg://"),
        ("postgresql://radar:pw@db:5432/radar", "postgresql+psycopg://"),
        ("postgresql+psycopg://radar:pw@db:5432/radar", "postgresql+psycopg://"),
    ])
    def test_driver_is_normalised(self, raw_url, expected_driver):
        s = _settings(DATABASE_URL=raw_url)
        assert encode_db_url(s).startswith(expected_driver)

    def test_sqlite_passes_through_untouched(self):
        s = _settings(DATABASE_URL="sqlite:///./data/radar.db")
        assert encode_db_url(s) == "sqlite:///./data/radar.db"

    def test_raw_at_in_database_url_password(self):
        """A raw '@' in the DATABASE_URL password: the LAST '@' separates it."""
        s = _settings(DATABASE_URL="postgresql+psycopg://radar:p@ss@db:5432/radar")
        url = encode_db_url(s)
        assert url.endswith("@db:5432/radar")
        _, pwd, host = _split(url)
        assert host == "db"
        assert pwd == "p@ss"


# --------------------------------------------------------------------------
# 4. Precedence: explicit parts win over DATABASE_URL
# --------------------------------------------------------------------------
class TestPrecedence:
    def test_explicit_parts_take_precedence(self):
        s = _settings(DATABASE_URL="postgresql+psycopg://old:oldpw@old:5432/old",
                      DB_USER="radar", DB_PASSWORD="NewPw123",
                      DB_HOST="db", DB_NAME="radar")
        url = encode_db_url(s)
        assert "NewPw123" in url
        assert "old" not in url
        assert _split(url)[2] == "db"

    def test_incomplete_parts_fall_back_to_database_url(self):
        """Without all of DB_USER/DB_HOST/DB_NAME the DATABASE_URL is used."""
        s = _settings(DATABASE_URL="postgresql+psycopg://radar:pw@db:5432/radar",
                      DB_USER="radar", DB_HOST="db", DB_NAME="")
        assert "radar:pw@db:5432" in encode_db_url(s)


# --------------------------------------------------------------------------
# 5. effective_db_password (used for strength validation)
# --------------------------------------------------------------------------
class TestEffectivePassword:
    def test_returns_literal_password_from_db_password(self):
        s = _settings(DB_USER="radar", DB_PASSWORD="pa%41ss",
                      DB_HOST="db", DB_NAME="radar")
        assert effective_db_password(s) == "pa%41ss"

    def test_decodes_when_flagged_encoded(self):
        s = _settings(DB_USER="radar", DB_PASSWORD="p%40ss",
                      DB_HOST="db", DB_NAME="radar", DB_PASSWORD_ENCODED=True)
        assert effective_db_password(s) == "p@ss"

    def test_reads_from_database_url(self):
        s = _settings(DATABASE_URL="postgresql+psycopg://radar:p%40ss@db:5432/radar")
        assert effective_db_password(s) == "p@ss"


# --------------------------------------------------------------------------
# 6. Production validation must accept the explicit DB_* form
# --------------------------------------------------------------------------
class TestProductionValidation:
    _ok = {
        "ENV": "production",
        "JWT_SECRET": "x" * 40,
        "ADMIN_PASSWORD": "Str0ngAdm1n!Pass",
        "ALLOWED_ORIGINS": "https://radar.example.com",
        "ROLE": "api",
    }

    def test_explicit_parts_pass(self):
        from app.config import validate_production_config
        s = _settings(**self._ok, DB_USER="radar", DB_PASSWORD="Sup3rS3cretP@ssw0rd",
                      DB_HOST="db", DB_NAME="radar")
        validate_production_config(s)  # must not raise

    def test_special_character_password_passes(self):
        from app.config import validate_production_config
        # >= 16 chars, non-numeric, and containing every awkward character
        s = _settings(**self._ok, DB_USER="radar",
                      DB_PASSWORD="a@b:c/d%e+f#g hjkl",
                      DB_HOST="db", DB_NAME="radar")
        validate_production_config(s)

    def test_short_password_is_rejected(self):
        from app.config import validate_production_config
        s = _settings(**self._ok, DB_USER="radar", DB_PASSWORD="short",
                      DB_HOST="db", DB_NAME="radar")
        with pytest.raises(SystemExit) as e:
            validate_production_config(s)
        assert "DB_PASSWORD" in str(e.value)

    def test_numeric_password_is_rejected(self):
        from app.config import validate_production_config
        s = _settings(**self._ok, DB_USER="radar", DB_PASSWORD="1234567890123456",
                      DB_HOST="db", DB_NAME="radar")
        with pytest.raises(SystemExit):
            validate_production_config(s)

    def test_sqlite_still_rejected_without_explicit_parts(self):
        from app.config import validate_production_config
        s = _settings(**self._ok)
        with pytest.raises(SystemExit) as e:
            validate_production_config(s)
        assert "PostgreSQL" in str(e.value)
