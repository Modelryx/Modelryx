import uuid
from types import SimpleNamespace

from modelryx.api import bundles


def test_historical_assembly_uses_selected_root_structure(monkeypatch):
    root, old_child, new_child = (uuid.uuid4() for _ in range(3))
    historical = uuid.uuid4()
    calls = []

    def children(_db, revision_id, snapshot_id=None):
        calls.append((revision_id, snapshot_id))
        if revision_id == root:
            return [old_child if snapshot_id == historical else new_child]
        return []

    class FakeDB:
        def get(self, _model, revision_id):
            return SimpleNamespace(id=revision_id)

    monkeypatch.setattr(bundles, "child_revisions", children)
    result = bundles.collect_revisions(FakeDB(), root, historical)
    assert [entry.id for entry in result] == [root, old_child]
    assert calls == [(root, historical), (old_child, None)]
    assert [entry.id for entry in bundles.collect_revisions(FakeDB(), root)] == [root, new_child]


def test_bundle_cache_identity_includes_root_snapshot(monkeypatch):
    revision, first, second = (uuid.uuid4() for _ in range(3))
    monkeypatch.setattr(bundles, "_cad_source_records", lambda *_args: [])
    assert bundles.cad_source_signature(None, revision, first) != bundles.cad_source_signature(
        None, revision, second
    )


def test_bundle_selection_pins_each_child_snapshot(monkeypatch):
    root, child, grandchild = (uuid.uuid4() for _ in range(3))
    root_snapshot, child_snapshot, grandchild_snapshot = (uuid.uuid4() for _ in range(3))
    seen = []

    def children(_db, revision_id, snapshot_id):
        seen.append((revision_id, snapshot_id))
        return {root: [child], child: [grandchild]}.get(revision_id, [])

    class FakeDB:
        def get(self, _model, revision_id):
            return SimpleNamespace(id=revision_id)

    monkeypatch.setattr(bundles, "child_revisions", children)
    monkeypatch.setattr(
        bundles, "latest_content_snapshot_id",
        lambda _db, revision_id: {
            child: child_snapshot, grandchild: grandchild_snapshot,
        }[revision_id],
    )
    selected = bundles.collect_revision_snapshots(FakeDB(), root, root_snapshot)
    assert selected == {
        root: root_snapshot, child: child_snapshot, grandchild: grandchild_snapshot,
    }
    assert seen == [
        (root, root_snapshot), (child, child_snapshot), (grandchild, grandchild_snapshot),
    ]
