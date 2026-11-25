from types import SimpleNamespace

from modelryx.services.freecad_settings import FREECAD_WORKER_KEY, configured_url


def test_override_is_resolved_each_time_and_can_disable_environment_default():
    class Db:
        row = None

        def get(self, _model, key):
            return self.row if key == FREECAD_WORKER_KEY else None

    db = Db()
    assert configured_url(db, FREECAD_WORKER_KEY, "http://default") == "http://default"
    db.row = SimpleNamespace(value="http://standalone")
    assert configured_url(db, FREECAD_WORKER_KEY, "http://default") == "http://standalone"
    db.row.value = ""
    assert configured_url(db, FREECAD_WORKER_KEY, "http://default") is None
