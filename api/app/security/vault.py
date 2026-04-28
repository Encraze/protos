import base64
import secrets
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings

VAULT_VERSION = 1
DEK_LEN = 32
NONCE_LEN = 12


class KekProvider(Protocol):
    @property
    def key_id(self) -> str: ...

    def wrap(self, dek: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes) -> bytes: ...


class EnvVarKekProvider:
    def __init__(self, master_key: bytes, key_id: str = "env") -> None:
        if len(master_key) != DEK_LEN:
            raise ValueError(f"master key must be {DEK_LEN} bytes, got {len(master_key)}")
        self._aesgcm = AESGCM(master_key)
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def wrap(self, dek: bytes) -> bytes:
        nonce = secrets.token_bytes(NONCE_LEN)
        ciphertext = self._aesgcm.encrypt(nonce, dek, associated_data=None)
        return nonce + ciphertext

    def unwrap(self, wrapped: bytes) -> bytes:
        nonce = wrapped[:NONCE_LEN]
        ciphertext = wrapped[NONCE_LEN:]
        return self._aesgcm.decrypt(nonce, ciphertext, associated_data=None)


class KmsKekProvider:
    def __init__(self, key_id: str, kms_client: Any) -> None:
        self._key_id = key_id
        self._client = kms_client

    @property
    def key_id(self) -> str:
        return self._key_id

    def wrap(self, dek: bytes) -> bytes:
        response = self._client.encrypt(KeyId=self._key_id, Plaintext=dek)
        return bytes(response["CiphertextBlob"])

    def unwrap(self, wrapped: bytes) -> bytes:
        response = self._client.decrypt(CiphertextBlob=wrapped, KeyId=self._key_id)
        return bytes(response["Plaintext"])


class Vault:
    def __init__(self, kek: KekProvider) -> None:
        self._kek = kek

    @property
    def kek_id(self) -> str:
        return self._kek.key_id

    def encrypt(self, plaintext: str) -> bytes:
        dek = AESGCM.generate_key(bit_length=DEK_LEN * 8)
        wrapped_dek = self._kek.wrap(dek)
        nonce = secrets.token_bytes(NONCE_LEN)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
        return _pack(wrapped_dek, nonce, ciphertext)

    def decrypt(self, blob: bytes) -> str:
        wrapped_dek, nonce, ciphertext = _unpack(blob)
        dek = self._kek.unwrap(wrapped_dek)
        plaintext = AESGCM(dek).decrypt(nonce, ciphertext, associated_data=None)
        return plaintext.decode("utf-8")

    def rewrap(self, blob: bytes, new_kek: KekProvider) -> bytes:
        wrapped_dek, nonce, ciphertext = _unpack(blob)
        dek = self._kek.unwrap(wrapped_dek)
        new_wrapped = new_kek.wrap(dek)
        return _pack(new_wrapped, nonce, ciphertext)


def _pack(wrapped_dek: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    if len(nonce) != NONCE_LEN:
        raise ValueError(f"nonce must be {NONCE_LEN} bytes")
    return (
        bytes([VAULT_VERSION])
        + len(wrapped_dek).to_bytes(2, "big")
        + wrapped_dek
        + nonce
        + ciphertext
    )


def _unpack(blob: bytes) -> tuple[bytes, bytes, bytes]:
    if len(blob) < 3 + NONCE_LEN:
        raise ValueError("vault blob too short")
    version = blob[0]
    if version != VAULT_VERSION:
        raise ValueError(f"unsupported vault version: {version}")
    wrapped_len = int.from_bytes(blob[1:3], "big")
    end_wrapped = 3 + wrapped_len
    end_nonce = end_wrapped + NONCE_LEN
    if len(blob) < end_nonce:
        raise ValueError("vault blob truncated")
    wrapped_dek = blob[3:end_wrapped]
    nonce = blob[end_wrapped:end_nonce]
    ciphertext = blob[end_nonce:]
    return wrapped_dek, nonce, ciphertext


def build_vault(settings: Settings) -> Vault:
    if settings.kek_provider == "env":
        if not settings.secrets_master_key:
            raise RuntimeError(
                "APP_SECRETS_MASTER_KEY required when APP_KEK_PROVIDER=env"
            )
        master = base64.b64decode(settings.secrets_master_key)
        return Vault(EnvVarKekProvider(master, key_id="env"))
    if settings.kek_provider == "aws-kms":
        if not settings.aws_kms_key_id:
            raise RuntimeError(
                "APP_AWS_KMS_KEY_ID required when APP_KEK_PROVIDER=aws-kms"
            )
        import boto3

        client = boto3.client("kms", region_name=settings.aws_region)
        return Vault(KmsKekProvider(settings.aws_kms_key_id, kms_client=client))
    raise ValueError(f"unknown kek provider: {settings.kek_provider}")
