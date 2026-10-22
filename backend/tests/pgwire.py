"""Server side of the PostgreSQL v3 wire protocol, for credential testing.

The defect under test is *what bytes the driver sends as the password*.
PostgreSQL cannot be started on this host (both the EDB 16.4 and the zonky
16.15 Windows builds abort with STATUS_STACK_BUFFER_OVERRUN / 0xc0000409
inside ucrtbase.dll during initdb, before a server process is ever created —
see docs/TEST_REPORT.md), so the assertion is made at the wire level:

this module implements the server side of the PostgreSQL v3 frontend/backend
protocol far enough to complete an authentication handshake with the REAL
psycopg driver, and it records the exact credential the driver transmitted.

That is a stronger assertion than "the connection succeeded": it proves the
driver decoded the URL into the intended literal password.

Protocol implemented
--------------------
  startup  -> AuthenticationCleartextPassword (3)  -> PasswordMessage
  startup  -> AuthenticationMD5Password (5)          -> PasswordMessage('md5...')
  startup  -> AuthenticationSASL (10)                 -> SASLInitialResponse /
                                                          SASLResponse(SCRAM-SHA-256)

The SCRAM path also verifies the client's proof, so the server *proves* it
knows the expected literal password without the client ever sending it in
the clear.
"""
import base64
import hashlib
import hmac
import os
import socket
import threading

# --- PostgreSQL v3 protocol constants -------------------------------------
PROTOCOL_VERSION = 196608          # 3.0
AUTH_OK = 0
AUTH_CLEARTEXT = 3
AUTH_MD5 = 5
AUTH_SASL = 10
AUTH_SASL_CONTINUE = 11

# PostgreSQL's magic md5 salt appended by the server
MD5_SALT = b"\x01\x02\x03\x04"
SCRAM_ITERATIONS = 4096


def _msg(tag: bytes, body: bytes) -> bytes:
    return tag + (len(body) + 4).to_bytes(4, "big") + body


def _cstr(s: str) -> bytes:
    return s.encode("utf-8") + b"\x00"


def _parse_attrs(text: str) -> dict:
    out = {}
    for part in text.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


class WireRecorder:
    """Records what the driver sent, and what it was told to send."""

    def __init__(self, user: str, password: str, auth: str = "scram"):
        self.user = user
        self.password = password
        self.auth = auth
        self.startup_user = None
        self.received_password_bytes = None      # cleartext path
        self.received_md5 = None                 # md5 path
        self.scram_ok = None                    # scram path
        self.client_final = None
        self.error = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._server_sock = None
        self._thread = None
        self.port = None

    # -- SCRAM-SHA-256 (RFC 5802 / RFC 7677) --------------------------------
    def _scram_server(self, client_first_bare: str):
        """Run the server side of SCRAM-SHA-256.

        Returns (server_first, expected_client_final, server_final).
        """
        attrs = _parse_attrs(client_first_bare)
        client_nonce = attrs["r"]

        server_nonce = base64.b64encode(os.urandom(18)).decode("ascii")
        salt = os.urandom(16)
        salt_b64 = base64.b64encode(salt).decode("ascii")
        server_first = "r=%s%s,s=%s,i=%d" % (
            client_nonce, server_nonce, salt_b64, SCRAM_ITERATIONS)

        salted = hashlib.pbkdf2_hmac(
            "sha256", self.password.encode("utf-8"), salt, SCRAM_ITERATIONS)
        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()

        client_final_without_proof = "c=%s,r=%s%s" % (
            base64.b64encode(b"n,,").decode("ascii"), client_nonce, server_nonce)
        auth_message = "%s,%s,%s" % (
            client_first_bare, server_first, client_final_without_proof)

        client_signature = hmac.new(stored_key, auth_message.encode("utf-8"),
                                    hashlib.sha256).digest()
        client_proof = base64.b64encode(
            bytes(a ^ b for a, b in zip(client_key, client_signature))).decode("ascii")
        expected_client_final = client_final_without_proof + ",p=" + client_proof

        server_signature = hmac.new(server_key, auth_message.encode("utf-8"),
                                    hashlib.sha256).digest()
        server_final = "v=" + base64.b64encode(server_signature).decode("ascii")
        return server_first, expected_client_final, server_final

    # -- server thread ------------------------------------------------------
    def _serve(self, conn: socket.socket):
        try:
            params = self._read_startup(conn)
            if params is None:
                return
            self.startup_user = params.get("user")

            if self.auth == "scram":
                conn.sendall(_msg(b"R", (
                    AUTH_SASL.to_bytes(4, "big") +
                    _cstr("SCRAM-SHA-256") + b"\x00")))
                body = self._recv_body(conn)
                nul = body.index(b"\x00")
                mech = body[:nul].decode("ascii", "replace")
                rest = body[nul + 1:]
                ilen = int.from_bytes(rest[:4], "big")
                initial = rest[4:4 + ilen].decode("utf-8", "replace")
                assert mech == "SCRAM-SHA-256", f"unexpected mechanism {mech!r}"
                # strip the GS2 header ("n,,") -> client-first-bare
                bare = initial[3:] if initial.startswith("n,,") else initial
                server_first, expected_final, server_final = self._scram_server(bare)
                conn.sendall(_msg(b"R", AUTH_SASL_CONTINUE.to_bytes(4, "big") +
                                  server_first.encode("utf-8")))
                client_final = self._recv_body(conn).decode("utf-8", "replace")
                with self._lock:
                    self.client_final = client_final
                    self.scram_ok = client_final == expected_final
                conn.sendall(_msg(b"R", AUTH_SASL_CONTINUE.to_bytes(4, "big") +
                                  server_final.encode("utf-8")))

            elif self.auth == "md5":
                conn.sendall(_msg(b"R", AUTH_MD5.to_bytes(4, "big") + MD5_SALT))
                payload = self._recv_body(conn).decode("utf-8", "replace")
                if payload.endswith("\x00"):
                    payload = payload[:-1]
                with self._lock:
                    self.received_md5 = payload
                inner = hashlib.md5(
                    (self.password + self.user).encode("utf-8")).hexdigest()
                expected = "md5" + hashlib.md5(
                    inner.encode("ascii") + MD5_SALT).hexdigest()
                self.md5_ok = payload == expected

            elif self.auth == "cleartext":
                conn.sendall(_msg(b"R", AUTH_CLEARTEXT.to_bytes(4, "big")))
                payload = self._recv_body(conn)
                if payload.endswith(b"\x00"):
                    payload = payload[:-1]
                with self._lock:
                    self.received_password_bytes = payload
                self.cleartext_ok = payload == self.password.encode("utf-8")

            # ---- AuthenticationOk + a benign error ------------------------
            conn.sendall(_msg(b"R", AUTH_OK.to_bytes(4, "big")))
            self.server_message = "FATAL\x00C01000\x00Mprobe complete\x00\x00"
            conn.sendall(_msg(b"E", self.server_message.encode("utf-8")))
        except Exception as e:              # noqa: BLE001
            self.error = repr(e)
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self._ready.set()

    @staticmethod
    def _recv_exact(conn, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = conn.recv(n - len(buf))
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        return buf

    def _recv_body(self, conn) -> bytes:
        """Read one frontend message: 1 tag byte + int32 length + body."""
        head = self._recv_exact(conn, 5)
        if len(head) < 5:
            raise ConnectionError("client closed the connection")
        length = int.from_bytes(head[1:5], "big")
        return self._recv_exact(conn, length - 4)

    def _read_startup(self, conn):
        """Read the startup packet, declining SSL/GSS negotiation first.

        psycopg opens with SSLRequest (80877103) by default (sslmode=prefer).
        The server must answer with a single 'N' byte to decline, after which
        the client re-sends the real StartupMessage.
        """
        for _ in range(4):
            hdr = self._recv_exact(conn, 4)
            if len(hdr) < 4:
                return None
            length = int.from_bytes(hdr, "big")
            body = self._recv_exact(conn, length - 4)
            if length == 8:
                code = int.from_bytes(body, "big")
                if code in (80877103, 80877104):   # SSLRequest / GSSENCRequest
                    conn.sendall(b"N")
                    continue
                return None
            # StartupMessage: int32 protocol version, then key/value pairs
            version = int.from_bytes(body[:4], "big")
            assert version == PROTOCOL_VERSION, f"unexpected protocol {version}"
            items = body[4:].split(b"\x00")
            params = {}
            for i in range(0, len(items) - 1, 2):
                key = items[i].decode("utf-8", "replace")
                if not key:
                    break
                params[key] = items[i + 1].decode("utf-8", "replace")
            return params
        return None

    # -- public API ---------------------------------------------------------
    def start(self):
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("127.0.0.1", 0))
        self._server_sock.listen(8)
        self.port = self._server_sock.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self.port

    def _accept_loop(self):
        try:
            conn, _ = self._server_sock.accept()
        except OSError:
            return
        self._serve(conn)

    def stop(self):
        try:
            self._server_sock.close()
        except Exception:
            pass

    @property
    def handshake_ok(self):
        if self.auth == "scram":
            return bool(self.scram_ok)
        if self.auth == "md5":
            return bool(getattr(self, "md5_ok", False))
        return bool(getattr(self, "cleartext_ok", False))
