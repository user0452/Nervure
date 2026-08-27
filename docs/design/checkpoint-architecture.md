# Checkpoint Architecture

`CheckpointStore` is a deliberately small file-mutation rollback mechanism, not a Git replacement. Immediately before an approved mutation handler runs, the executor snapshots direct file write/delete targets into `.nervure/checkpoints/<session>/<checkpoint>/checkpoint.json`. The snapshot includes session, time, originating tool call, paths, and byte content for existing files.

Restore requires the caller to pass explicit confirmation. Corrupt or unavailable snapshots fail closed; directories are never recursively removed during restore. Checkpoint creation and failure are trace events, so a user can distinguish an executed edit from a demonstrated rollback point.
