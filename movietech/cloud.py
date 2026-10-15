"""Run one pipeline command as a Google Cloud Run job.

The container has no disk that outlives it, so the run folder lives in Cloud Storage. This copies
gs://<bucket>/<prefix>/ into /data/<prefix>/, runs the command, then copies the folder back,
even when the command fails, so the cost ledger and any finished stage are kept.

    python -m movietech.cloud output/tony_bruce costs /data/output/tony_bruce
"""

import os
import sys
from pathlib import Path

DATA = Path("/data")


def download(gcs, bucket: str, prefix: str, root: Path) -> int:
    """Copy every object under prefix/ into root, keeping the object name as the relative path."""
    count = 0
    for blob in gcs.list_blobs(bucket, prefix=prefix.rstrip("/") + "/"):
        target = (root / blob.name).resolve()
        if blob.name.endswith("/") or not target.is_relative_to(root.resolve()):
            continue  # folder markers, and names that would escape /data
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))
        count += 1
    return count


def upload(gcs, bucket: str, prefix: str, root: Path) -> int:
    """Copy every file under root/prefix back to the same object names."""
    count = 0
    target = gcs.bucket(bucket)
    for path in sorted((root / prefix).rglob("*")):
        if path.is_file():
            target.blob(path.relative_to(root).as_posix()).upload_from_filename(str(path))
            count += 1
    return count


def run(argv: list[str], gcs=None, root: Path = DATA, bucket: str | None = None,
        main=None) -> None:
    if len(argv) < 2:
        raise SystemExit("usage: python -m movietech.cloud <gcs prefix> <command> [args...]")
    prefix, command = argv[0].strip("/"), argv[1:]
    bucket = bucket or os.environ["MOVIETECH_BUCKET"]  # set by the Cloud Run job
    if gcs is None:
        from google.cloud import storage  # only needed in the cloud

        gcs = storage.Client()  # signs in as the job's service account
    if main is None:
        from movietech.cli import main

    print(f"Downloaded {download(gcs, bucket, prefix, root)} files from gs://{bucket}/{prefix}/")
    sys.argv = ["movietech", *command]
    try:
        main()
    finally:
        print(f"Uploaded {upload(gcs, bucket, prefix, root)} files to gs://{bucket}/{prefix}/")


if __name__ == "__main__":
    run(sys.argv[1:])
