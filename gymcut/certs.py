"""Self-signed TLS certificate for LAN access (phones need HTTPS for camera access)."""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import socket

from .config import CERT_DIR


def lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packets are sent for UDP connect
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ok = []
    for ip in ips:
        a = ipaddress.ip_address(ip)
        if not (a.is_loopback or a.is_link_local):
            ok.append(ip)
    # private ranges first (typical home Wi-Fi)
    return sorted(ok, key=lambda x: (not ipaddress.ip_address(x).is_private, x))


def ensure_cert() -> tuple[str, str]:
    cert, key, meta = CERT_DIR / "cert.pem", CERT_DIR / "key.pem", CERT_DIR / "cert.json"
    names = sorted(set(["localhost", "127.0.0.1", socket.gethostname()] + lan_ips()))
    if cert.exists() and key.exists() and meta.exists():
        try:
            m = json.loads(meta.read_text())
            if set(names) <= set(m.get("names", [])) and m.get("expires", 0) > dt.datetime.now().timestamp() + 86400 * 7:
                return str(cert), str(key)
        except Exception:
            pass
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "GymCut local"),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "GymCut")])
    sans = []
    for n in names:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            sans.append(x509.DNSName(n))
    now = dt.datetime.now(dt.timezone.utc)
    expires = now + dt.timedelta(days=800)  # iOS rejects > 825 days
    c = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
         .not_valid_after(expires)
         .add_extension(x509.SubjectAlternativeName(sans), critical=False)
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
         .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    meta.write_text(json.dumps({"names": names, "expires": expires.timestamp()}))
    return str(cert), str(key)
