"""Language-independent v4 wire representation; never decode application payloads."""

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.capabilities import ByteStability, Capabilities, HistoryAccess, Management
from asterstore.metadata.declarations import Declaration
from asterstore.metadata.membership import FileSet, Locator, Member, Object

from ._json import array, dump, integer, load, mapping, record, text
from ._models import FORMAT_VERSION, DeclarationRecord, ManagedRequest, ObjectRecord, StoreRecord


def marker_version(data: bytes) -> int:
    try:
        return integer(load(data).get("format_version"))
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid repository marker: {exc}") from exc


def encode_store(value: StoreRecord) -> bytes:
    payload: dict[str, object] = {
        "format_version": FORMAT_VERSION,
        "kind": "repository",
        "store_id": value.store_id,
        "resource_ids": value.resource_ids,
        "required_features": ["registered"],
    }
    if value.managed_resource_id is not None:
        payload["required_features"] = ["registered", "managed"]
        payload["managed_resource_id"] = value.managed_resource_id
    if value.lifecycle:
        payload["required_features"] = (
            ["registered", "managed", "lifecycle"]
            if value.managed_resource_id is not None
            else ["registered", "lifecycle"]
        )
    return dump(payload)


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
        value = record(data, "repository", fields)
        return StoreRecord(
            text(value["store_id"]),
            tuple(text(x) for x in array(value["resource_ids"])),
            text(value["managed_resource_id"]) if managed else None,
            "lifecycle" in features,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid v4 store: {exc}") from exc


def locator_payload(value: Locator) -> dict[str, object]:
    return {"resource_id": value.resource_id, "relative_path": value.relative_path}


def locator(value: object) -> Locator:
    item = mapping(value, {"resource_id", "relative_path"})
    return Locator(text(item["resource_id"]), text(item["relative_path"]))


def declaration_payload(value: Declaration) -> dict[str, object]:
    caps = value.capabilities
    return {
        "dataset_id": value.dataset_id,
        "publication_id": value.publication_id,
        "capabilities": {
            "management": caps.management.value,
            "byte_stability": caps.byte_stability.value,
            "history": caps.history.value,
        },
        "objects": [
            {"object_id": obj.object_id, "locator": locator_payload(obj.locator)}
            for obj in value.files.objects
        ],
        "members": [
            {"key": member.key, "object_id": member.object_id} for member in value.files.members
        ],
    }


def declaration(value: object) -> Declaration:
    data = mapping(value, {"dataset_id", "publication_id", "capabilities", "objects", "members"})
    caps = mapping(data["capabilities"], {"management", "byte_stability", "history"})
    objects = []
    for item in array(data["objects"]):
        obj = mapping(item, {"object_id", "locator"})
        objects.append(Object(text(obj["object_id"]), locator(obj["locator"])))
    members = []
    for item in array(data["members"]):
        member = mapping(item, {"key", "object_id"})
        members.append(Member(text(member["key"]), text(member["object_id"])))
    return Declaration(
        text(data["dataset_id"]),
        text(data["publication_id"]),
        FileSet(objects, members),
        Capabilities(
            Management(text(caps["management"])),
            ByteStability(text(caps["byte_stability"])),
            HistoryAccess(text(caps["history"])),
        ),
    )


def encode_declaration_record(value: DeclarationRecord) -> bytes:
    return dump(
        {
            "format_version": FORMAT_VERSION,
            "kind": "publication",
            "store_id": value.store_id,
            "operation_id": value.operation_id,
            "expected_generation": value.expected_generation,
            "generation": value.generation,
            "declaration": declaration_payload(value.declaration),
        }
    )


def decode_declaration_record(data: bytes) -> DeclarationRecord:
    try:
        value = record(
            data,
            "publication",
            {"store_id", "operation_id", "expected_generation", "generation", "declaration"},
        )
        result = DeclarationRecord(
            text(value["store_id"]),
            text(value["operation_id"]),
            integer(value["expected_generation"]),
            declaration(value["declaration"]),
        )
        if integer(value["generation"]) != result.generation:
            raise ValueError("generation does not follow expected_generation")
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid v4 publication: {exc}") from exc


def encode_object_record(value: ObjectRecord) -> bytes:
    payload: dict[str, object] = {
        "format_version": FORMAT_VERSION,
        "kind": "object",
        "store_id": value.store_id,
        "object_id": value.object_id,
        "management": value.management.value,
        "locator": locator_payload(value.locator),
        "byte_stability": value.byte_stability.value,
    }
    if value.creator_operation_id is not None:
        payload["creator_operation_id"] = value.creator_operation_id
    return dump(payload)


def decode_object_record(data: bytes) -> ObjectRecord:
    try:
        managed = load(data).get("management") == "managed"
        fields = {"store_id", "object_id", "management", "locator", "byte_stability"}
        if managed:
            fields.add("creator_operation_id")
        value = record(data, "object", fields)
        result = ObjectRecord(
            text(value["store_id"]),
            text(value["object_id"]),
            locator(value["locator"]),
            ByteStability(text(value["byte_stability"])),
            text(value["creator_operation_id"]) if managed else None,
        )
        if result.management.value != value["management"]:
            raise ValueError("inconsistent object management")
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid v4 object: {exc}") from exc


def encode_managed_request(value: ManagedRequest) -> bytes:
    return dump(
        {
            "format_version": FORMAT_VERSION,
            "kind": "managed_request",
            "store_id": value.store_id,
            "operation_id": value.operation_id,
            "dataset_id": value.dataset_id,
            "publication_id": value.publication_id,
            "expected_generation": value.expected_generation,
            "history": value.history.value,
        }
    )


def decode_managed_request(data: bytes) -> ManagedRequest:
    try:
        value = record(
            data,
            "managed_request",
            {
                "store_id",
                "operation_id",
                "dataset_id",
                "publication_id",
                "expected_generation",
                "history",
            },
        )
        return ManagedRequest(
            text(value["store_id"]),
            text(value["operation_id"]),
            text(value["dataset_id"]),
            text(value["publication_id"]),
            integer(value["expected_generation"]),
            HistoryAccess(text(value["history"])),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid v4 managed request: {exc}") from exc
