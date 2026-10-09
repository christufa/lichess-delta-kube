from .cli import entrypoint

if __name__ == "__main__":  # required for multiprocessing "spawn" on Windows
    raise SystemExit(entrypoint())
