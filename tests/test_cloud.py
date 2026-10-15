import pytest

from movietech.cloud import run


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def download_to_filename(self, filename):
        with open(filename, "w", encoding="utf-8") as f:
            f.write(self.store[self.name])

    def upload_from_filename(self, filename):
        with open(filename, encoding="utf-8") as f:
            self.store[self.name] = f.read()


class FakeGCS:
    """An in-memory bucket with the calls cloud.py uses."""

    def __init__(self, objects):
        self.objects = dict(objects)

    def list_blobs(self, bucket, prefix):
        return [FakeBlob(self.objects, k) for k in list(self.objects) if k.startswith(prefix)]

    def bucket(self, _name):
        return self

    def blob(self, name):
        return FakeBlob(self.objects, name)


def test_downloads_runs_and_uploads(tmp_path):
    gcs = FakeGCS({"output/run1/costs.jsonl": "{}", "output/run1/plan/tasks.md": "T01",
                 "output/run2/other.txt": "x", "output/run1/../../escape.txt": "no"})
    seen = {}

    def main():
        import sys
        seen["argv"] = sys.argv
        seen["files"] = sorted(p.name for p in (tmp_path / "output/run1").rglob("*") if p.is_file())
        (tmp_path / "output/run1/PLAN.md").write_text("approved")

    run(["output/run1/", "costs", "/data/output/run1"], gcs=gcs, root=tmp_path, bucket="b",
        main=main)
    assert seen["argv"] == ["movietech", "costs", "/data/output/run1"]
    assert seen["files"] == ["costs.jsonl", "tasks.md"]
    assert gcs.objects["output/run1/PLAN.md"] == "approved"
    assert not (tmp_path.parent / "escape.txt").exists()


def test_uploads_even_when_the_command_fails(tmp_path):
    gcs = FakeGCS({"output/run1/costs.jsonl": "{}"})

    def main():
        (tmp_path / "output/run1/partial.json").write_text("half")
        raise RuntimeError("cap reached")

    with pytest.raises(RuntimeError):
        run(["output/run1", "warroom", "/data/output/run1"], gcs=gcs, root=tmp_path, bucket="b",
            main=main)
    assert gcs.objects["output/run1/partial.json"] == "half"
