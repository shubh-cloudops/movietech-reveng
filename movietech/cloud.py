"""Run one pipeline command on AWS Fargate.

The container has no disk that outlives it, so the run folder lives in S3. This copies
s3://<bucket>/<prefix>/ into /data/<prefix>/, runs the command, then copies the folder back,
even when the command fails, so the cost ledger and any finished stage are kept.

    python -m movietech.cloud output/tony_bruce costs /data/output/tony_bruce
"""

import os
import sys
from pathlib import Path

DATA = Path("/data")


def download(s3, bucket: str, prefix: str, root: Path) -> int:
    """Copy every object under prefix/ into root, keeping the key as the relative path."""
    count = 0
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket,
                                                        Prefix=prefix.rstrip("/") + "/")
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            target = (root / key).resolve()
            if key.endswith("/") or not target.is_relative_to(root.resolve()):
                continue  # folder markers, and keys that would escape /data
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(target))
            count += 1
    return count


def upload(s3, bucket: str, prefix: str, root: Path) -> int:
    """Copy every file under root/prefix back to the same keys."""
    count = 0
    for path in sorted((root / prefix).rglob("*")):
        if path.is_file():
            s3.upload_file(str(path), bucket, path.relative_to(root).as_posix())
            count += 1
    return count


def run(argv: list[str], s3=None, root: Path = DATA, bucket: str | None = None,
        main=None) -> None:
    if len(argv) < 2:
        raise SystemExit("usage: python -m movietech.cloud <s3 prefix> <command> [args...]")
    prefix, command = argv[0].strip("/"), argv[1:]
    bucket = bucket or os.environ["MOVIETECH_BUCKET"]  # set by the ECS task definition
    if s3 is None:
        import boto3  # only needed in the cloud

        s3 = boto3.client("s3")
    if main is None:
        from movietech.cli import main

    print(f"Downloaded {download(s3, bucket, prefix, root)} files from s3://{bucket}/{prefix}/")
    sys.argv = ["movietech", *command]
    try:
        main()
    finally:
        print(f"Uploaded {upload(s3, bucket, prefix, root)} files to s3://{bucket}/{prefix}/")


if __name__ == "__main__":
    run(sys.argv[1:])
