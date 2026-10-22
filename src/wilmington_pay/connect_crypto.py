"""Decryption of Amazon Connect "Store customer input" encrypted values.

Connect encrypts each DTMF entry with the public key you upload to the
instance (AWS Encryption SDK, RSA-OAEP-SHA512). Only this Lambda holds the
private key (Secrets Manager, KMS-encrypted), so card digits are ciphertext
everywhere else: in contact attributes, in flow logs and in transit.

Ref: AWS docs "Encrypt customer input" + sample amazon-connect-secure-input.
Verify key/algorithm settings against the Connect console during build.
"""
from __future__ import annotations

import base64
from typing import Protocol

from .config import Settings


class InputDecryptor(Protocol):
    def decrypt(self, value: str) -> str: ...


class PlaintextDecryptor:
    """Local/test only - Settings refuses this mode when APP_ENV=prod."""

    def decrypt(self, value: str) -> str:
        return value


class ConnectDecryptor:
    def __init__(self, private_key_pem: bytes, certificate_id: str) -> None:
        import aws_encryption_sdk
        from aws_encryption_sdk.identifiers import CommitmentPolicy, EncryptionKeyType, WrappingAlgorithm
        from aws_encryption_sdk.internal.crypto.wrapping_keys import WrappingKey
        from aws_encryption_sdk.key_providers.raw import RawMasterKeyProvider

        wrapping_key = WrappingKey(
            wrapping_algorithm=WrappingAlgorithm.RSA_OAEP_SHA512_MGF1,
            wrapping_key=private_key_pem,
            wrapping_key_type=EncryptionKeyType.PRIVATE,
        )

        class _ConnectKeyProvider(RawMasterKeyProvider):
            provider_id = "AmazonConnect"

            def _get_raw_key(self, key_id):
                return wrapping_key

        self._provider = _ConnectKeyProvider()
        self._provider.add_master_key(certificate_id)
        self._client = aws_encryption_sdk.EncryptionSDKClient(
            commitment_policy=CommitmentPolicy.REQUIRE_ENCRYPT_ALLOW_DECRYPT)

    def decrypt(self, value: str) -> str:
        plaintext, _header = self._client.decrypt(source=base64.b64decode(value), key_provider=self._provider)
        return plaintext.decode()


_decryptor: InputDecryptor | None = None


def build_decryptor(settings: Settings) -> InputDecryptor:
    global _decryptor
    if settings.input_decryption == "plaintext":
        return PlaintextDecryptor()
    if _decryptor is None:  # private key is loaded once per warm container
        from .secret_store import get_secret_string

        pem = get_secret_string(settings.connect_private_key_secret_arn).encode()
        _decryptor = ConnectDecryptor(pem, settings.connect_certificate_id)
    return _decryptor
