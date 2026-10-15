import json
import subprocess
from types import SimpleNamespace

import pytest

from movietech.issues import export
from tests.test_plan import acc, make_plan, make_tests, task


class FakeGh:
    """Records gh calls and answers `issue create` with the next issue URL."""

    def __init__(self, first=10):
        self.calls, self.next = [], first

    def __call__(self, args, input=None, **kwargs):
        self.calls.append((args, input))
        out = ""
        if args[1:3] == ["issue", "create"]:
            out = f"Creating issue\nhttps://github.com/o/r/issues/{self.next}\n"
            self.next += 1
        return SimpleNamespace(stdout=out)


@pytest.fixture
def run_dir(tmp_path):
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "tasks.json").write_text(make_plan().model_dump_json())
    (plan / "tests.json").write_text(make_tests([acc("AT01", task_ids=["T02"])]).model_dump_json())
    (plan / "approval.json").write_text("{}")
    return tmp_path


def test_creates_one_issue_per_task_with_label_and_links(run_dir):
    gh = FakeGh()
    created = export(run_dir, "o/r", run=gh)
    assert created == ["https://github.com/o/r/issues/10", "https://github.com/o/r/issues/11"]
    assert gh.calls[0][0][:3] == ["gh", "label", "create"]
    args, body = gh.calls[2]
    assert "T02: t" in args and "--label" in args and args[-2:] == ["--repo", "o/r"]
    assert "**Depends on:** #10 (T01)" in body
    assert "AT01 (end_to_end): caption within 2 s" in body
    record = json.loads((run_dir / "plan" / "issues.json").read_text())
    assert record == {"T01": created[0], "T02": created[1]}


def test_rerun_skips_tasks_that_already_have_an_issue(run_dir):
    (run_dir / "plan" / "issues.json").write_text(json.dumps({"T01": "https://x/issues/3"}))
    gh = FakeGh()
    assert export(run_dir, "o/r", run=gh) == ["https://github.com/o/r/issues/10"]
    assert "#3 (T01)" in gh.calls[-1][1]


def test_dry_run_calls_nothing(run_dir, capsys):
    gh = FakeGh()
    assert export(run_dir, "o/r", run=gh, dry_run=True) == []
    assert gh.calls == []
    assert "would create  T01: t  [M1]" in capsys.readouterr().out


def test_needs_approval_first(run_dir):
    (run_dir / "plan" / "approval.json").unlink()
    with pytest.raises(FileNotFoundError, match="approve"):
        export(run_dir, "o/r", run=FakeGh())


def test_stops_on_gh_error_and_keeps_what_was_made(run_dir):
    gh = FakeGh()

    def failing(args, input=None, **kwargs):
        if args[1:3] == ["issue", "create"] and gh.next == 11:
            raise subprocess.CalledProcessError(1, args)
        return gh(args, input)

    with pytest.raises(subprocess.CalledProcessError):
        export(run_dir, "o/r", run=failing)
    assert list(json.loads((run_dir / "plan" / "issues.json").read_text())) == ["T01"]


def test_paid_resource_and_no_deps(run_dir):
    plan = make_plan([task("T01")])
    plan.milestones[0].tasks[0].paid_resource = "Firestore, $0"
    (run_dir / "plan" / "tasks.json").write_text(plan.model_dump_json())
    gh = FakeGh()
    export(run_dir, "o/r", run=gh)
    body = gh.calls[-1][1]
    assert "**Paid resource:** Firestore, $0" in body and "Depends on" not in body
