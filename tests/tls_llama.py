"""A real llama.cpp server with real TLS and a real API key, for testing the remote and cloud paths without a cloud key.

The server listens on all interfaces of this computer on a spare port (never 8080), with a certificate from a
throwaway certificate authority made for the test. It is reached three ways:

- ``https://127.0.0.1:<port>/v1``: loopback, so local;
- ``https://<this computer's LAN address>:<port>/v1``: a private-network address, so local (the "remote local
  server" path, for example a GPU box on your LAN);
- ``https://llama.localtest.me:<port>/v1``: a public DNS name (localtest.me resolves to 127.0.0.1), so Jig
  classifies it as **cloud**: HTTPS, consent and the vault key are all exercised against a real model.

Needs JIG_TEST_LLAMA_SERVER (a stock llama-server with SSL support) and JIG_TEST_SMALL_MODEL (a small GGUF).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import secrets
import socket
import ssl
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .server_helpers import free_port

LLAMA_SERVER = Path(os.environ.get("JIG_TEST_LLAMA_SERVER", ""))
SMALL_MODEL = Path(os.environ.get("JIG_TEST_SMALL_MODEL", ""))
AVAILABLE = LLAMA_SERVER.is_file() and SMALL_MODEL.is_file()
ALIAS = "jig-test-small"
CLOUD_NAME = "llama.localtest.me"


def lan_address() -> str | None:
    """This computer's private LAN address (no packet is sent: a UDP socket only picks a route)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
        except OSError:
            return None
        ip = ipaddress.ip_address(s.getsockname()[0])
    return str(ip) if ip.is_private and not ip.is_loopback else None


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def make_certificates(folder: Path, *, dns: list[str], ips: list[str]) -> tuple[Path, Path, Path]:
    """A test CA and a server certificate signed by it. Returns (ca.pem, server.pem, server-key.pem)."""
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca = (x509.CertificateBuilder().subject_name(_name("Jig test CA")).issuer_name(_name("Jig test CA"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(ca_ski, critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder().subject_name(_name(dns[0])).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski), critical=False)
            .sign(ca_key, hashes.SHA256()))
    folder.mkdir(parents=True, exist_ok=True)
    ca_path, cert_path, key_path = folder / "ca.pem", folder / "server.pem", folder / "server-key.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return ca_path, cert_path, key_path


@dataclass
class TlsLlama:
    port: int
    ca_file: Path
    api_key: str
    lan_ip: str | None
    process: subprocess.Popen[bytes]
    log: Path

    def url(self, host: str) -> str:
        return f"https://{host}:{self.port}/v1"


def start(folder: Path, *, timeout: float = 300.0) -> TlsLlama:
    lan = lan_address()
    ca, cert, key = make_certificates(folder / "tls", dns=[CLOUD_NAME, "localhost"],
                                      ips=["127.0.0.1", *([lan] if lan else [])])
    api_key = "jig-test-" + secrets.token_urlsafe(24)
    port = free_port()
    log = folder / "llama-server.log"
    cmd = [str(LLAMA_SERVER), "-m", str(SMALL_MODEL), "--host", "0.0.0.0", "--port", str(port), "--alias", ALIAS,
           "-ngl", "99", "-c", "16384", "-np", "2", "--jinja", "--ssl-key-file", str(key), "--ssl-cert-file",
           str(cert), "--api-key", api_key]
    with log.open("wb") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                cwd=str(LLAMA_SERVER.parent))
    server = TlsLlama(port, ca, api_key, lan, proc, log)
    verify = ssl.create_default_context(cafile=str(ca))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited with {proc.returncode}:\n{log.read_text(errors='replace')[-3000:]}")
        try:
            r = httpx.get(f"https://127.0.0.1:{port}/v1/models", verify=verify, timeout=3,
                          headers={"Authorization": f"Bearer {api_key}"})
            if r.status_code == 200:
                return server
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    stop(server)
    raise RuntimeError(f"llama-server on {port} was not ready within {timeout:.0f}s:\n"
                       f"{log.read_text(errors='replace')[-3000:]}")


def stop(server: TlsLlama) -> None:
    if server.process.poll() is None:
        server.process.terminate()
        try:
            server.process.wait(30)
        except subprocess.TimeoutExpired:
            server.process.kill()
            server.process.wait(30)
