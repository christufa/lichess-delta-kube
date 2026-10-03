"""Extract compressed archives into complete-game JSONL chunks for Spark."""
import io
import json
import uuid
from pathlib import Path

from common import read_json, write_json, run_task, checkpoint, record_completion

def iter_games(stream):
    """Lichess dumps delimit games with an Event header; preserve multiline PGN."""
    lines = []
    for line in stream:
        if line.startswith("[Event ") and lines:
            text = "".join(lines).strip()
            if text:
                yield text
            lines = []
        lines.append(line)
    text = "".join(lines).strip()
    if text:
        yield text


def stage_games(stream, directory, target_bytes=64 * 1024 * 1024):
    """Write whole games as JSON lines so Spark can safely split input files."""
    directory.mkdir(parents=True, exist_ok=True)
    count = size = index = 0
    output = None
    try:
        for game in iter_games(stream):
            if output is None or size >= target_bytes:
                if output:
                    output.close()
                output = (directory / f"part-{index:06d}.jsonl").open("wb")
                index += 1
                size = 0
            payload = (json.dumps({"pgn": game}, ensure_ascii=False) + "\n").encode("utf-8")
            output.write(payload)
            size += len(payload)
            count += 1
    finally:
        if output:
            output.close()
    if not count:
        raise ValueError("Archive contains no games")
    return count


def extract_archives(args, spark, root):
    import zstandard

    archives = []
    for archive in read_json(root / "downloads.json"):
        saved = (checkpoint(root, args, archive, "insert")
                 or checkpoint(root, args, archive, "extract"))
        if saved:
            archives.append(saved)
            continue
        raw = Path(archive["raw_path"])
        # A failed attempt never publishes partial files to the next task.
        directory = root / "staged" / uuid.uuid4().hex
        with raw.open("rb") as compressed:
            with zstandard.ZstdDecompressor().stream_reader(compressed) as reader:
                with io.TextIOWrapper(reader, encoding="utf-8") as stream:
                    count = stage_games(stream, directory)
        completed = {**archive, "path": str(directory), "games": count}
        record_completion(root, args, completed, "extract")
        archives.append(completed)
    write_json(root / "archives.json", archives)


if __name__ == "__main__":
    run_task(extract_archives, "extract")
