import pytest
from cryptography.fernet import InvalidToken

from bank.security.token_store import EncryptedTokenStore


def test_save_and_load_token_round_trip(tmp_path):
    key = EncryptedTokenStore.generate_key()
    store = EncryptedTokenStore(storage_path=tmp_path / "tokens.json", keys=[key])

    store.save_token("user-1", {"access_token": "abc", "refresh_token": "def"})
    loaded = store.load_token("user-1")

    assert loaded == {"access_token": "abc", "refresh_token": "def"}


def test_load_missing_user_returns_none(tmp_path):
    store = EncryptedTokenStore(storage_path=tmp_path / "tokens.json", keys=[EncryptedTokenStore.generate_key()])
    assert store.load_token("inconnu") is None


def test_tokens_are_not_stored_in_plaintext_on_disk(tmp_path):
    path = tmp_path / "tokens.json"
    store = EncryptedTokenStore(storage_path=path, keys=[EncryptedTokenStore.generate_key()])
    store.save_token("user-1", {"access_token": "un-secret-tres-sensible"})

    raw = path.read_text(encoding="utf-8")
    assert "un-secret-tres-sensible" not in raw


def test_rotate_key_allows_decrypting_old_entries_with_new_key(tmp_path):
    old_key = EncryptedTokenStore.generate_key()
    store = EncryptedTokenStore(storage_path=tmp_path / "tokens.json", keys=[old_key])
    store.save_token("user-1", {"access_token": "abc"})

    new_key = EncryptedTokenStore.generate_key()
    store.rotate_key(new_key)

    assert store.load_token("user-1") == {"access_token": "abc"}

    # Une fois la rotation confirmée, on peut retirer l'ancienne clé : le
    # store ne doit plus en avoir besoin pour déchiffrer.
    store.keys = [new_key]
    assert store.load_token("user-1") == {"access_token": "abc"}


def test_wrong_key_cannot_decrypt(tmp_path):
    store = EncryptedTokenStore(storage_path=tmp_path / "tokens.json", keys=[EncryptedTokenStore.generate_key()])
    store.save_token("user-1", {"access_token": "abc"})

    other_store = EncryptedTokenStore(storage_path=tmp_path / "tokens.json", keys=[EncryptedTokenStore.generate_key()])
    with pytest.raises(InvalidToken):
        other_store.load_token("user-1")


def test_missing_key_raises():
    with pytest.raises(ValueError):
        EncryptedTokenStore(storage_path="/tmp/whatever.json", keys=[])
