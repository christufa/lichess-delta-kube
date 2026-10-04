"""Installed entry point; initialize logging before importing the sync task."""
from .common import run_task


def sync():
    def handler(args, spark, root):
        from .sync import sync_dataset
        sync_dataset(args, spark, root)
    run_task(handler, "sync")
