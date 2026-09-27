"""Administrator-controlled cleanup settings shared by the whole installation."""

from pydantic import BaseModel, ConfigDict, Field


class StoragePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cleanup_enabled: bool = True
    cache_days: int = Field(default=28, ge=1, le=99999)
    artwork_expiry_enabled: bool = False
    artwork_days: int = Field(default=99999, ge=1, le=99999)
    response_cache_mb: int = Field(default=256, ge=0, le=1048576)
    artwork_cache_mb: int = Field(default=1024, ge=0, le=1048576)
    backup_keep: int = Field(default=0, ge=0, le=99999)

    @property
    def response_bytes(self) -> int:
        return self.response_cache_mb * 1024 * 1024

    @property
    def artwork_bytes(self) -> int:
        return self.artwork_cache_mb * 1024 * 1024


def load_storage_policy(user_store) -> StoragePolicy:
    with user_store.transaction() as connection:
        row = connection.execute(
            "SELECT setting_value FROM instance_settings WHERE setting_key = 'storage_policy'"
        ).fetchone()
    if row is None:
        return StoragePolicy()
    try:
        return StoragePolicy.model_validate_json(row[0])
    except ValueError:
        # Invalid settings must never enable deletion.
        return StoragePolicy(cleanup_enabled=False)


def save_storage_policy(connection, policy: StoragePolicy) -> None:
    connection.execute(
        """INSERT INTO instance_settings (setting_key, setting_value)
        VALUES ('storage_policy', ?) ON CONFLICT(setting_key)
        DO UPDATE SET setting_value = excluded.setting_value""",
        (policy.model_dump_json(),),
    )
