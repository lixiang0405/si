"""Generate a reusable local CA and a TLS certificate for the LAN address."""

import ipaddress
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def write(path, contents):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as file:
        file.write(contents)
    temporary.replace(path)


def key_bytes(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def certificate(subject, issuer, key, now, days):
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=days)))


def has_extension(cert, extension):
    try:
        cert.extensions.get_extension_for_class(extension)
    except x509.ExtensionNotFound:
        return False
    return True


def local_certificate(directory: Path, host: str):
    ca = directory / "Si-Game-Room-CA.pem"
    root_key = directory / "ca-key.pem"
    now = datetime.now(UTC)
    root = x509.load_pem_x509_certificate(ca.read_bytes()) if ca.exists() else None
    rotate = root is None or not root_key.exists() or not has_extension(root, x509.SubjectKeyIdentifier)
    if not rotate:
        signing_key = serialization.load_pem_private_key(root_key.read_bytes(), password=None)
        if root.not_valid_after_utc < now + timedelta(days=30):
            raise ValueError("试玩证书已接近过期，请在新的本地数据目录生成证书。")
    else:
        signing_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Si Game Room LAN Test CA")])
        root = (certificate(name, name, signing_key, now, 730)
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(signing_key.public_key()), critical=False)
                .sign(signing_key, hashes.SHA256()))
        write(root_key, key_bytes(signing_key))
        write(ca, root.public_bytes(serialization.Encoding.PEM))
    phone_install = directory / "Si-Game-Room-CA.cer"
    if rotate or not phone_install.exists():
        write(phone_install, root.public_bytes(serialization.Encoding.DER))
    cert = directory / "server.pem"
    key = directory / "server-key.pem"
    address = ipaddress.IPv4Address(host)
    if not rotate and cert.exists() and key.exists():
        saved = x509.load_pem_x509_certificate(cert.read_bytes())
        names = saved.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if (address in names.get_values_for_type(x509.IPAddress) and saved.not_valid_after_utc > now + timedelta(days=1)
                and has_extension(saved, x509.AuthorityKeyIdentifier) and has_extension(saved, x509.KeyUsage)):
            return ca, cert, key
    server_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    leaf = (certificate(name, root.subject, server_key, now, 30)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signing_key.public_key()), critical=False)
            .sign(signing_key, hashes.SHA256()))
    write(key, key_bytes(server_key))
    write(cert, leaf.public_bytes(serialization.Encoding.PEM))
    return ca, cert, key
