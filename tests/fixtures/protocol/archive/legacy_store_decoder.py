"""Frozen 0.2.0 store decoder from commit 8cf733c; compatibility test only."""

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.protocol._json import array, load, record, text
from asterstore.metadata.protocol._models import StoreRecord


def decode_store(data: bytes) -> StoreRecord:
    try:
        header = load(data)
        features = header.get("required_features")
        if features not in (
            ["registered"],
            ["registered", "managed"],
            ["registered", "lifecycle"],
            ["registered", "managed", "lifecycle"],
        ):
            raise ValueError("unsupported required features")
        assert isinstance(features, list)
        managed = "managed" in features
        fields = {"store_id", "resource_ids", "required_features"}
        if managed:
            fields.add("managed_resource_id")
        value = record(data, "store", fields)
        return StoreRecord(
            text(value["store_id"]),
            tuple(text(x) for x in array(value["resource_ids"])),
            text(value["managed_resource_id"]) if managed else None,
            "lifecycle" in features,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid store: {exc}") from exc
