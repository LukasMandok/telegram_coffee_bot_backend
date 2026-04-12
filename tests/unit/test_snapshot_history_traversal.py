import pytest

from src.database.snapshot_manager import SnapshotManager


def test_compute_modified_snapshots_simple_decrement_path() -> None:
    history = [1, 2, 3, 4]

    path = SnapshotManager._compute_modified_snapshots(
        history,
        current_snapshot_number=4,
        target_snapshot_number=2,
    )

    assert path == [4, 3, 2]


def test_compute_modified_snapshots_uses_restore_point_jump_for_upward_restore() -> None:
    # After restoring #1 from state #3 we append [4, 1].
    # Now we're at state #1 and want to restore #3 (target > current).
    # We can jump 1 -> 4 (restore point), then walk down 3.
    history = [1, 2, 3, 4, 1]

    path = SnapshotManager._compute_modified_snapshots(
        history,
        current_snapshot_number=1,
        target_snapshot_number=3,
    )

    assert path == [1, 4, 3]


def test_compute_modified_snapshots_uses_creation_gap_jump_points() -> None:
    # Example from the algorithm description:
    # history: [1,2,3,4,1,5,6,3,7]
    # current=7, target=2 should use the jump 7 -> 3 then down to 2.
    history = [1, 2, 3, 4, 1, 5, 6, 3, 7]

    path = SnapshotManager._compute_modified_snapshots(
        history,
        current_snapshot_number=7,
        target_snapshot_number=2,
    )

    assert path == [7, 3, 2]


def test_compute_modified_snapshots_restore_higher_after_lower() -> None:
    # Same example: if we're at 3 and want to restore 4,
    # we must go via the restore point 6: 3 -> 6 -> 5 -> 4.
    history = [1, 2, 3, 4, 1, 5, 6, 3, 7]

    path = SnapshotManager._compute_modified_snapshots(
        history,
        current_snapshot_number=3,
        target_snapshot_number=4,
    )

    assert path == [3, 6, 5, 4]


def test_compute_modified_snapshots_handles_sparse_history_when_numbers_removed() -> None:
    # Sparse histories can happen after cleanup/pruning.
    # We do NOT implicitly "skip" numbers; instead we rely on explicit full-snapshot anchors.
    #
    # Example shape: weekly/manual full snapshot #9 exists, later we restored to #2, then created #13.
    # Restoring to the full snapshot should always be reachable via the full-snapshot jump.
    # Full snapshot #9 is encoded by a consecutive duplicate in the history.
    history = [9, 9, 2, 13]

    path = SnapshotManager._compute_modified_snapshots(
        history,
        current_snapshot_number=13,
        target_snapshot_number=9,
    )

    assert path == [13, 9]


def test_compute_modified_snapshots_raises_when_target_missing_from_history() -> None:
    history = [1, 2, 3]

    with pytest.raises(ValueError, match="Target snapshot number"):
        SnapshotManager._compute_modified_snapshots(
            history,
            current_snapshot_number=3,
            target_snapshot_number=4,
        )
