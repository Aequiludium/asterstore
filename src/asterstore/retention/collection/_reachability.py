"""Pure metadata reachability; object identity is the complete managed key."""

from collections import defaultdict

from asterstore.metadata import CollectionPolicy, ObjectRef
from asterstore.storage import ControlInventory

from ._models import CollectionPreview, ObjectDecision, PublicationDecision


def calculate(inventory: ControlInventory, policy: CollectionPolicy) -> CollectionPreview:
    current = set(inventory.current)
    by_dataset = defaultdict(list)
    for record in inventory.publications:
        by_dataset[record.publication.dataset.dataset_id].append(record)
    recent = set()
    for did, records in by_dataset.items():
        for record in sorted(records, key=lambda item: item.generation, reverse=True)[
            : policy.keep_last
        ]:
            recent.add((did, record.publication.publication_id))
    references: dict[tuple[str, str], list[str]] = defaultdict(list)
    for reference_record in inventory.references:
        if reference_record.state == "active":
            ref = reference_record.reference
            references[(ref.dataset_id, ref.publication_id)].append(ref.name)
    decisions = []
    owners: dict[str, set[str]] = defaultdict(set)
    retiring_objects: set[str] = set()
    committed = {
        record.candidate_id for record in inventory.publications + inventory.retired_publications
    }
    collected = {
        key
        for state in inventory.progress
        if state.state == "complete"
        for key in state.deleted + state.missing
    }
    for record in inventory.retired_publications:
        if record.publication.dataset.dataset_id in policy.datasets:
            retiring_objects.update(
                obj.key for obj in record.publication.objects if obj.key not in collected
            )
    for record in sorted(
        inventory.publications,
        key=lambda item: (item.publication.dataset.dataset_id, item.generation),
    ):
        pub = record.publication
        key = (pub.dataset.dataset_id, pub.publication_id)
        reasons = []
        if key in current:
            reasons.append("current")
        if key in recent:
            reasons.append("keep_last")
        reasons.extend(f"reference:{name}" for name in sorted(references[key]))
        if key[0] not in policy.datasets:
            reasons.append("outside_scope")
        retire = not reasons
        decisions.append(
            PublicationDecision(
                pub,
                record.generation,
                retire,
                tuple(reasons) if reasons else ("unprotected_history",),
            )
        )
        for obj in pub.objects:
            if retire:
                retiring_objects.add(obj.key)
            else:
                owners[obj.key].add(f"publication:{key[0]}/{key[1]}")
    for manifest in inventory.candidates:
        if manifest.candidate_id not in committed and manifest.keys is not None:
            for obj in manifest.publication().objects:
                owners[obj.key].add(f"candidate:{manifest.candidate_id}")
    objects = tuple(
        ObjectDecision(
            ObjectRef(key),
            not owners.get(key),
            tuple(sorted(owners[key])) if owners.get(key) else ("unreachable_after_retirement",),
        )
        for key in sorted(retiring_objects | owners.keys())
    )
    return CollectionPreview(policy, "complete", tuple(decisions), objects)
