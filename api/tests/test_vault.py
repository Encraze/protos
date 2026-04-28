import os

import pytest
from cryptography.exceptions import InvalidTag

from app.security.vault import EnvVarKekProvider, Vault


@pytest.fixture
def vault() -> Vault:
    return Vault(EnvVarKekProvider(b"protos-test-master-key-32bytes!!"))


def test_round_trip(vault: Vault) -> None:
    secret = "sk-openai-XXXXXXXXXXXXXXXXXXXXXXXX"
    blob = vault.encrypt(secret)
    assert vault.decrypt(blob) == secret


def test_ciphertext_changes_per_call(vault: Vault) -> None:
    blob_a = vault.encrypt("same-secret")
    blob_b = vault.encrypt("same-secret")
    assert blob_a != blob_b
    assert vault.decrypt(blob_a) == vault.decrypt(blob_b) == "same-secret"


def test_tampered_ciphertext_is_rejected(vault: Vault) -> None:
    blob = bytearray(vault.encrypt("secret-value"))
    blob[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        vault.decrypt(bytes(blob))


def test_truncated_blob_is_rejected(vault: Vault) -> None:
    blob = vault.encrypt("secret-value")[:5]
    with pytest.raises(ValueError):
        vault.decrypt(blob)


def test_unsupported_version_is_rejected(vault: Vault) -> None:
    blob = bytearray(vault.encrypt("secret-value"))
    blob[0] = 99
    with pytest.raises(ValueError):
        vault.decrypt(bytes(blob))


def test_kek_rotation_preserves_plaintext_without_re_encrypting_secret(
    vault: Vault,
) -> None:
    new_kek = EnvVarKekProvider(os.urandom(32), key_id="rotated")
    rewrapped = vault.rewrap(vault.encrypt("rotate-me"), new_kek)
    rotated_vault = Vault(new_kek)
    assert rotated_vault.decrypt(rewrapped) == "rotate-me"


def test_master_key_wrong_length_is_rejected() -> None:
    with pytest.raises(ValueError):
        EnvVarKekProvider(b"too-short")
