import boto3
import pytest
from moto import mock_aws

from app.security.vault import KmsKekProvider, Vault


@pytest.fixture
def kms_vault():
    with mock_aws():
        client = boto3.client("kms", region_name="us-east-1")
        key = client.create_key(Description="protos-test", KeyUsage="ENCRYPT_DECRYPT")
        key_id = key["KeyMetadata"]["KeyId"]
        yield Vault(KmsKekProvider(key_id, kms_client=client)), client, key_id


def test_kms_round_trip(kms_vault) -> None:
    vault, _client, _key_id = kms_vault
    secret = "sk-anthropic-XXXXXXXXXXXXXXXXXXXXXXXX"
    blob = vault.encrypt(secret)
    assert vault.decrypt(blob) == secret


def test_kms_ciphertext_changes_per_call(kms_vault) -> None:
    vault, *_ = kms_vault
    a = vault.encrypt("k")
    b = vault.encrypt("k")
    assert a != b


def test_kms_rotation_to_a_second_kms_key(kms_vault) -> None:
    vault, client, _ = kms_vault
    new_key = client.create_key(Description="protos-rotated")["KeyMetadata"]["KeyId"]
    new_kek = KmsKekProvider(new_key, kms_client=client)
    rewrapped = vault.rewrap(vault.encrypt("rotate-me"), new_kek)
    rotated_vault = Vault(new_kek)
    assert rotated_vault.decrypt(rewrapped) == "rotate-me"
