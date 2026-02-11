#!/usr/bin/env python

# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import ipaddress
import logging
import socket
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SSL_DIR = Path.home() / ".cache" / "lerobot" / "ssl"
DEFAULT_CERT_PATH = DEFAULT_SSL_DIR / "vr_cert.pem"
DEFAULT_KEY_PATH = DEFAULT_SSL_DIR / "vr_key.pem"


def _get_local_ips() -> list[str]:
    """Collect local IP addresses for SAN entries."""
    ips = ["127.0.0.1"]
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addr = info[4][0]
            if addr not in ips:
                ips.append(addr)
    except socket.gaierror:
        pass
    # Also try the common "connect to external" trick to find the primary LAN IP
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            addr = s.getsockname()[0]
            if addr not in ips:
                ips.append(addr)
    except OSError:
        pass
    return ips


def _cert_is_valid(cert_path: Path) -> bool:
    """Check if an existing certificate is still valid (not expired)."""
    try:
        from cryptography import x509

        pem_data = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(pem_data)
        now = datetime.datetime.now(datetime.timezone.utc)
        return cert.not_valid_after_utc > now
    except Exception:
        return False


def get_or_create_ssl_context(
    cert_path: str | None = None,
    key_path: str | None = None,
) -> tuple[str, str]:
    """
    Return paths to an SSL cert and key, generating a self-signed pair if needed.

    If the caller supplies explicit paths and both files exist and the cert is
    still valid, they are returned as-is. Otherwise a new RSA 2048-bit
    self-signed X.509 certificate is generated with local IPs in the SAN so a
    Quest headset on the same LAN can connect by IP.

    Returns:
        (cert_path, key_path) as strings.
    """
    cert_p = Path(cert_path) if cert_path else DEFAULT_CERT_PATH
    key_p = Path(key_path) if key_path else DEFAULT_KEY_PATH

    if cert_p.is_file() and key_p.is_file() and _cert_is_valid(cert_p):
        logger.info(f"Reusing existing SSL certificate: {cert_p}")
        return str(cert_p), str(key_p)

    logger.info("Generating new self-signed SSL certificate for VR teleop...")

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_p.parent.mkdir(parents=True, exist_ok=True)

    # Generate RSA key pair
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build SAN with local IPs
    local_ips = _get_local_ips()
    san_entries: list[x509.GeneralName] = [x509.DNSName("localhost")]
    for ip in local_ips:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "LeRobot VR Teleop"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LeRobot"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_p.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    logger.info(f"SSL certificate written to {cert_p}")
    logger.info(f"SAN IPs: {local_ips}")
    return str(cert_p), str(key_p)
