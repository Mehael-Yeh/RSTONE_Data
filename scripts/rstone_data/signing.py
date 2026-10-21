"""Ed25519 only. No unsigned production fallback or downloaded trust anchors."""

import base64
import binascii
import re
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .protocol import DataError, bind_latest, require


def unbase64(text, length):
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise DataError("invalid Base64 key or signature") from exc
    require(len(raw) == length and base64.b64encode(raw).decode("ascii") == text,
            "noncanonical Base64 key or signature")
    return raw


def validate_keys(keys):
    require(isinstance(keys, dict), "invalid trusted key table")
    for key_id, public in keys.items():
        require(isinstance(key_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id),
                "invalid trusted keyId")
        require(isinstance(public, str), "invalid public key")
        unbase64(public, 32)
    return keys


@dataclass
class Signer:
    key_id: str
    private: Ed25519PrivateKey

    @classmethod
    def load(cls, key_id, private_base64, keys):
        validate_keys(keys)
        require(private_base64, "DATA_SIGNING_PRIVATE_KEY required; production cannot be unsigned")
        require(key_id in keys, "signing keyId not trusted")
        private = Ed25519PrivateKey.from_private_bytes(unbase64(private_base64, 32))
        expected = unbase64(keys[key_id], 32)
        require(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) == expected,
                "private key does not match trusted public key")
        return cls(key_id, private)

    def sign(self, raw):
        return base64.b64encode(self.private.sign(raw)) + b"\n"


def verify_release(latest, raw, signature, keys):
    validate_keys(keys)
    manifest = bind_latest(latest, raw)
    require(manifest["keyId"] in keys, "unknown signing keyId; client update required")
    require(isinstance(signature, bytes) and signature.endswith(b"\n") and b"\n" not in signature[:-1],
            "signature must be one Base64 line with LF")
    try:
        text = signature[:-1].decode("ascii")
        decoded = unbase64(text, 64)
        public = Ed25519PublicKey.from_public_bytes(unbase64(keys[manifest["keyId"]], 32))
        public.verify(decoded, raw)
    except (UnicodeError, InvalidSignature, ValueError) as exc:
        raise DataError("manifest signature verification failed") from exc
    return manifest
