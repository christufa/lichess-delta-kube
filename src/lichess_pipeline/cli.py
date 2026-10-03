"""Installed wheel entry points; initialize logging before stage imports."""
from importlib import import_module
from .common import run_task


def _run(stage, function):
    def handler(args, spark, root):
        module = import_module(f"lichess_pipeline.{stage}")
        getattr(module, function)(args, spark, root)
    run_task(handler, stage)


def download():
    _run("download", "download_archives")


def extract():
    _run("extract", "extract_archives")


def insert():
    _run("insert", "ingest")


def upload():
    _run("upload", "upload")
