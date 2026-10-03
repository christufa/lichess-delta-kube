"""Export committed Delta snapshots to Hugging Face."""
import uuid

from common import read_json, write_json, partition_filter, run_task, checkpoint, record_completion
from schema import COLUMNS

def upload(args, spark, root):
    archives = [archive for archive in read_json(root / "delta.json")
                if not checkpoint(root, args, archive, "upload")]
    if not archives:
        print("No pending uploads.", flush=True)
        return
    from huggingface_hub import HfApi
    from pyspark.dbutils import DBUtils
    token = DBUtils(spark).secrets.get(scope=args.secret_scope, key=args.secret_key)
    api = HfApi(token=token)
    api.create_repo(args.repo, repo_type="dataset", exist_ok=True, private=True)
    for archive in archives:
        output = root / "exports" / uuid.uuid4().hex
        snapshot = (spark.read.option("versionAsOf", archive["version"]).table(args.full_table)
                    .where(partition_filter(archive)).select(*COLUMNS))
        (snapshot.write.mode("overwrite").option("compression", "zstd")
         .option("maxRecordsPerFile", args.shard_size).parquet(str(output)))
        shards = sorted(output.glob("*.parquet"))
        if not shards:
            raise ValueError("No Parquet shards exported")
        marker = {"table": args.full_table, "delta_version": archive["version"],
                  "games": archive["games"], "shards": len(shards)}
        write_json(output / ".done", marker)
        # Pinned hub 0.36 uses one commit for this folder, including the marker.
        # Remove only this month's old shards so a smaller rerun leaves no stale rows.
        commit = api.upload_folder(repo_id=args.repo, repo_type="dataset", folder_path=str(output),
                          path_in_repo=f"data/{archive['variant']}/{archive['month']}",
                          allow_patterns=["*.parquet", ".done"],
                          delete_patterns=["*.parquet", ".done"],
                          commit_message=f"Export {archive['variant']}/{archive['month']} from Delta v{archive['version']}")
        record_completion(root, args, {**archive, "hf_commit": commit.oid,
                                      "shards": len(shards)}, "upload")



if __name__ == "__main__":
    run_task(upload, "upload")
