import pytest

from movietech.cloud import run


class FakeS3:
    """An in-memory bucket with the three calls cloud.py uses."""

    def __init__(self, objects):
        self.objects = dict(objects)

    def get_paginator(self, _name):
        return self

    def paginate(self, Bucket, Prefix):
        return [{"Contents": [{"Key": k} for k in self.objects if k.startswith(Prefix)]}]

    def download_file(self, bucket, key, filename):
        with open(filename, "w", encoding="utf-8") as f:
            f.write(self.objects[key])

    def upload_file(self, filename, bucket, key):
        with open(filename, encoding="utf-8") as f:
            self.objects[key] = f.read()


def test_downloads_runs_and_uploads(tmp_path):
    s3 = FakeS3({"output/run1/costs.jsonl": "{}", "output/run1/plan/tasks.md": "T01",
                 "output/run2/other.txt": "x", "output/run1/../../escape.txt": "no"})
    seen = {}

    def main():
        import sys
        seen["argv"] = sys.argv
        seen["files"] = sorted(p.name for p in (tmp_path / "output/run1").rglob("*") if p.is_file())
        (tmp_path / "output/run1/PLAN.md").write_text("approved")

    run(["output/run1/", "costs", "/data/output/run1"], s3=s3, root=tmp_path, bucket="b",
        main=main)
    assert seen["argv"] == ["movietech", "costs", "/data/output/run1"]
    assert seen["files"] == ["costs.jsonl", "tasks.md"]
    assert s3.objects["output/run1/PLAN.md"] == "approved"
    assert not (tmp_path.parent / "escape.txt").exists()


def test_uploads_even_when_the_command_fails(tmp_path):
    s3 = FakeS3({"output/run1/costs.jsonl": "{}"})

    def main():
        (tmp_path / "output/run1/partial.json").write_text("half")
        raise RuntimeError("cap reached")

    with pytest.raises(RuntimeError):
        run(["output/run1", "warroom", "/data/output/run1"], s3=s3, root=tmp_path, bucket="b",
            main=main)
    assert s3.objects["output/run1/partial.json"] == "half"
