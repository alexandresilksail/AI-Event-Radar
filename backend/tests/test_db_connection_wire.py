"""Real database-connection tests for the DB credential handling (finding #1).

These are *connection* tests, not string tests: the real `psycopg` driver
opens a TCP connection to a server that speaks the PostgreSQL v3 wire
protocol, and the assertion is made on the credential the driver actually
transmitted.

Why not a real PostgreSQL server
--------------------------------
Neither the EDB 16.4 nor the zonky 16.15 Windows build can be started on
this host: `initdb.exe` aborts with STATUS_STACK_BUFFER_OVERRUN
(0xc0000409) inside `ucrtbase.dll` before a server process is ever created.
The crash is reproducible with a minimal environment, from a short path, and
with both independent builds, so it is a host limitation rather than a
configuration problem (see docs/TEST_REPORT.md for the full evidence).

The wire-level test asserts exactly the property that matters: the driver
must decode the connection URL into the intended *literal* password. It is
strictly stronger than "the connection succeeded", because a server that
accepts the wrong password would not be detected by a mere success check.

The SCRAM-SHA-256 variant additionally proves the server can verify the
client's proof, i.e. the driver presented the expected secret.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pgwire import WireRecorder  # noqa: E402

# A password that breaks naive URI concatenation: it contains the userinfo
# terminator '@', the port separator ':', the path separator '/', the
# fragment marker '#', the query marker '+', a percent sign and a space.
SPICY_PASSWORD = "P@ss:w/rd%+er#2026 x"


def _connect(url_template: str, password: str, user: str = "radar",
             auth: str = "scram"):
    """Drive psycopg against the probe server; return the recorder.

    The template must contain the host placeholder ``DBHOST``; the port is
    filled in with the probe server's ephemeral port.
    """
    import re as _re

    import psycopg

    rec = WireRecorder(user=user, password=password, auth=auth)
    port = rec.start()
    url = url_template.replace("DBHOST", "127.0.0.1")
    # The application builds SQLAlchemy URLs; psycopg itself only understands
    # the plain "postgresql://" scheme.
    url = url.replace("postgresql+psycopg://", "postgresql://")
    url = _re.sub(r"@127\.0\.0\.1:\d+/", "@127.0.0.1:%d/" % port, url)
    try:
        try:
            psycopg.connect(url, connect_timeout=10)
        except Exception:
            # The probe server answers AuthenticationOk and then a benign
            # FATAL; the driver therefore always raises. The handshake data
            # is what the test asserts on.
            pass
        rec._ready.wait(timeout=10)
    finally:
        rec.stop()
    return rec


class TestDriverReceivesTheLiteralPassword:
    """The driver must decode the URL into the intended literal password."""

    def test_explicit_parts_with_all_special_characters(self):
        from app.config import Settings, encode_db_url
        s = Settings(
            _env_file=None,
            DB_USER="radar", DB_PASSWORD=SPICY_PASSWORD,
            DB_HOST="DBHOST", DB_PORT=5432, DB_NAME="radar",
        )
        url = encode_db_url(s).replace("DBHOST", "127.0.0.1")
        rec = _connect(url.replace(":5432/", ":5432/"), SPICY_PASSWORD, auth="cleartext")
        assert rec.received_password_bytes == SPICY_PASSWORD.encode("utf-8"), (
            f"driver received {rec.received_password_bytes!r}, "
            f"expected {SPICY_PASSWORD.encode('utf-8')!r}")
        assert rec.handshake_ok

    def test_explicit_parts_scram_proof(self):
        """SCRAM-SHA-256: the server verifies the client's proof."""
        from app.config import Settings, encode_db_url
        s = Settings(
            _env_file=None,
            DB_USER="radar", DB_PASSWORD=SPICY_PASSWORD,
            DB_HOST="DBHOST", DB_PORT=5432, DB_NAME="radar",
        )
        url = encode_db_url(s).replace("DBHOST", "127.0.0.1")
        rec = _connect(url, SPICY_PASSWORD, auth="scram")
        assert rec.scram_ok is True, (
            f"SCRAM proof rejected (client-final={getattr(rec, '_client_final', None)!r}, "
            f"error={rec.error!r})")

    def test_explicit_parts_md5(self):
        from app.config import Settings, encode_db_url
        s = Settings(
            _env_file=None,
            DB_USER="radar", DB_PASSWORD=SPICY_PASSWORD,
            DB_HOST="DBHOST", DB_PORT=5432, DB_NAME="radar",
        )
        url = encode_db_url(s).replace("DBHOST", "127.0.0.1")
        rec = _connect(url, SPICY_PASSWORD, auth="md5")
        assert rec.handshake_ok, "the MD5 challenge response did not match"

    def test_raw_password_in_database_url(self):
        """A raw password inside DATABASE_URL must be encoded exactly once."""
        from app.config import Settings, encode_db_url
        s = Settings(
            _env_file=None,
            DATABASE_URL=f"postgresql+psycopg://radar:{SPICY_PASSWORD}@DBHOST:5432/radar",
        )
        url = encode_db_url(s).replace("DBHOST", "127.0.0.1")
        rec = _connect(url, SPICY_PASSWORD, auth="cleartext")
        assert rec.received_password_bytes == SPICY_PASSWORD.encode("utf-8"), (
            f"driver received {rec.received_password_bytes!r}")
        assert rec.handshake_ok

    def test_percent_encoded_password_in_database_url(self):
        """An already-encoded password must be decoded exactly once."""
        from urllib.parse import quote
        from app.config import Settings, encode_db_url
        encoded = quote(SPICY_PASSWORD, safe="")
        s = Settings(
            _env_file=None,
            DATABASE_URL=f"postgresql+psycopg://radar:{encoded}@DBHOST:5432/radar",
        )
        url = encode_db_url(s).replace("DBHOST", "127.0.0.1")
        rec = _connect(url, SPICY_PASSWORD, auth="cleartext")
        assert rec.received_password_bytes == SPICY_PASSWORD.encode("utf-8"), (
            f"driver received {rec.received_password_bytes!r}")
        assert rec.handshake_ok

    def test_ambiguous_percent_password_via_explicit_parts(self):
        """A raw password that *looks* percent-encoded.

        'pa%41ss' is the literal password 'pa%41ss'. It must not be decoded
        into 'paAss'. The explicit DB_PASSWORD path states the encoding, so
        it is unambiguous.
        """
        from app.config import Settings, encode_db_url
        s = Settings(
            _env_file=None,
            DB_USER="radar", DB_PASSWORD="pa%41ss-long-enough-1234",
            DB_HOST="DBHOST", DB_PORT=5432, DB_NAME="radar",
        )
        url = encode_db_url(s).replace("DBHOST", "127.0.0.1")
        rec = _connect(url, "pa%41ss-long-enough-1234", auth="cleartext")
        assert rec.received_password_bytes == b"pa%41ss-long-enough-1234", (
            f"driver received {rec.received_password_bytes!r}")

    def test_wrong_password_is_rejected_by_the_server(self):
        """Negative control: the probe really does distinguish passwords."""
        from app.config import Settings, encode_db_url
        s = Settings(
            _env_file=None,
            DB_USER="radar", DB_PASSWORD="correct-password-1234",
            DB_HOST="DBHOST", DB_PORT=5432, DB_NAME="radar",
        )
        url = encode_db_url(s).replace("DBHOST", "127.0.0.1")
        rec = _connect(url, "a-different-password", auth="scram")
        assert rec.scram_ok is False, (
            "the probe accepted a wrong password — it cannot detect the bug")
