import uuid
from types import SimpleNamespace

from modelryx.api import assemblies
from modelryx.models import Part, Revision, RevisionContentSnapshot


def test_historical_bom_limits_expansion_to_recorded_root(monkeypatch) -> None:
    item_id, revision_id, historical, current = (uuid.uuid4() for _ in range(4))
    revision = SimpleNamespace(id=revision_id, part_id=item_id)
    part = SimpleNamespace(id=item_id, part_type="ASSEMBLY")
    snapshot = SimpleNamespace(id=historical, revision_id=revision_id)

    class FakeDB:
        def get(self, model, _id):
            return {Revision: revision, Part: part, RevisionContentSnapshot: snapshot}[model]

    captured = []
    monkeypatch.setattr(assemblies, "can_read_revision", lambda *_args: True)
    monkeypatch.setattr(
        assemblies, "latest_bom", lambda *_args: SimpleNamespace(snapshot_id=current)
    )
    monkeypatch.setattr(assemblies, "build_occurrences", lambda *args: captured.append(args) or [])
    monkeypatch.setattr(assemblies.PartResponse, "model_validate", lambda value: value)
    monkeypatch.setattr(assemblies.RevisionResponse, "model_validate", lambda value: value)
    monkeypatch.setattr(assemblies, "BomResponse", lambda **values: values)

    result = assemblies.read_bom(revision_id, FakeDB(), None, 20, "engineering", historical)
    assert result["occurrences"] == []
    assert captured[0][3] == 1
    assert captured[0][5] == historical
