import os
import hashlib

import pytest
        await client.admin.command("ping")
    except Exception as exc:
        await client.close()
        pytest.skip(f"MongoDB not reachable for integration test: {exc}")
    db = client[test_db_name]

    await init_beanie(
        db,
        document_models=[
            models.AppSettings,
            models.SnapshotMeta,
            models.SnapshotHistory,
            models.SnapshotDataChunk,
        ],
    )

    try:
        yield db
    finally:
        try:
            await client.drop_database(test_db_name)
        finally:
            await client.close()


async def _fetch_all_sorted_by_id(collection) -> list[dict]:
    cursor = collection.find({}).sort([("_id", 1)])
    return [doc async for doc in cursor]


@pytest.mark.asyncio
async def test_snapshot_history_and_restore_roundtrip(mongo_db):
    manager = SnapshotManager(mongo_db)

    # Ensure a clean slate.
    await manager.clear_all_snapshots()

    collection_name = "pytest_snapshot_items"
    collection = mongo_db.get_collection(collection_name)
    await collection.delete_many({})

    # State A
    await collection.insert_many(
        [
            {"_id": 1, "value": "first"},
            {"_id": 2, "value": "second"},
        ]
    )

    snapshot_a_id = await manager.create_snapshot(
        reason="pytest snapshot A",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )
    meta_a = await manager.get_snapshot_meta(str(snapshot_a_id))
    assert meta_a is not None
    assert meta_a.snapshot_number == 1

    # State B
    await collection.delete_many({})
    await collection.insert_many(
        [
            {"_id": 1, "value": "changed"},
            {"_id": 3, "value": "third"},
        ]
    )

    snapshot_b_id = await manager.create_snapshot(
        reason="pytest snapshot B",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )
    meta_b = await manager.get_snapshot_meta(str(snapshot_b_id))
    assert meta_b is not None
    assert meta_b.snapshot_number == 2

    history_before = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history_before is not None
    assert history_before.snapshot_numbers == [1, 2]
    assert history_before.last_snapshot_number == 2

    # Restore to A (should create and append an obsolete pre-restore snapshot)
    await manager.restore_snapshot(
        str(snapshot_a_id),
        collections=(collection_name,),
        loaded_by_user_id=123,
        capture_pre_restore_snapshot=True,
    )

    restored_docs = await _fetch_all_sorted_by_id(collection)
    assert [doc["_id"] for doc in restored_docs] == [1, 2]
    assert [doc["value"] for doc in restored_docs] == ["first", "second"]

    history_after = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history_after is not None

    # Expected: initial [1,2] then restore point [3] then target [1]
    assert history_after.snapshot_numbers == [1, 2, 3, 1]
    assert history_after.last_snapshot_number == 3

    meta_a_doc = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 1)
    assert meta_a_doc is not None
    assert meta_a_doc.loaded_at is not None
    assert meta_a_doc.loaded_by_user_id == 123
    assert meta_a_doc.obsolete is False

    meta_b_doc = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 2)
    assert meta_b_doc is not None
    assert meta_b_doc.obsolete is True
    assert meta_b_doc.obsoleted_by_snapshot_number == 1

    pre_restore_doc = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 3)
    assert pre_restore_doc is not None
    assert pre_restore_doc.obsolete is True
    assert pre_restore_doc.pre_restore_for_snapshot_number == 1


@pytest.mark.asyncio
async def test_restore_obsolete_snapshot_updates_history_and_flags(mongo_db):
    manager = SnapshotManager(mongo_db)

    await manager.clear_all_snapshots()

    collection_name = "pytest_snapshot_items_obsolete"
    collection = mongo_db.get_collection(collection_name)
    await collection.delete_many({})

    await collection.insert_many(
        [
            {"_id": 1, "value": "a"},
            {"_id": 2, "value": "b"},
        ]
    )

    snapshot_1_id = await manager.create_snapshot(
        reason="pytest snapshot 1",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    await collection.delete_many({})
    await collection.insert_many(
        [
            {"_id": 1, "value": "a2"},
            {"_id": 3, "value": "c"},
        ]
    )

    snapshot_2_id = await manager.create_snapshot(
        reason="pytest snapshot 2",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    # Restore to snapshot #1 -> snapshot #2 becomes obsolete and pre-restore #3 is appended.
    await manager.restore_snapshot(
        str(snapshot_1_id),
        collections=(collection_name,),
        loaded_by_user_id=111,
        capture_pre_restore_snapshot=True,
    )

    meta_2_after_restore_1 = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 2)
    assert meta_2_after_restore_1 is not None
    assert meta_2_after_restore_1.obsolete is True
    assert meta_2_after_restore_1.obsoleted_by_snapshot_number == 1

    history_after_restore_1 = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history_after_restore_1 is not None
    assert history_after_restore_1.snapshot_numbers == [1, 2, 3, 1]
    assert history_after_restore_1.last_snapshot_number == 3

    # Restore the now-obsolete snapshot #2.
    await manager.restore_snapshot(
        str(snapshot_2_id),
        collections=(collection_name,),
        loaded_by_user_id=222,
        capture_pre_restore_snapshot=True,
    )

    restored_docs = await _fetch_all_sorted_by_id(collection)
    assert [doc["_id"] for doc in restored_docs] == [1, 3]
    assert [doc["value"] for doc in restored_docs] == ["a2", "c"]

    history_after_restore_2 = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history_after_restore_2 is not None
    assert history_after_restore_2.snapshot_numbers == [1, 2, 3, 1, 4, 2]
    assert history_after_restore_2.last_snapshot_number == 4

    meta_2_after_restore_2 = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 2)
    assert meta_2_after_restore_2 is not None
    assert meta_2_after_restore_2.obsolete is False
    assert meta_2_after_restore_2.loaded_at is not None
    assert meta_2_after_restore_2.loaded_by_user_id == 222

    pre_restore_4 = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 4)
    assert pre_restore_4 is not None
    assert pre_restore_4.obsolete is True
    assert pre_restore_4.pre_restore_for_snapshot_number == 2


@pytest.mark.asyncio
async def test_restore_higher_snapshot_after_restoring_lower_uses_restoration_point(mongo_db):
    manager = SnapshotManager(mongo_db)

    await manager.clear_all_snapshots()

    collection_name = "pytest_snapshot_items_upward"
    collection = mongo_db.get_collection(collection_name)
    await collection.delete_many({})

    # Snapshot #1
    await collection.insert_many(
        [
            {"_id": 1, "value": "one"},
        ]
    )

    snapshot_1_id = await manager.create_snapshot(
        reason="pytest snapshot 1",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    # Snapshot #2
    await collection.delete_many({})
    await collection.insert_many(
        [
            {"_id": 1, "value": "two"},
        ]
    )
    await manager.create_snapshot(
        reason="pytest snapshot 2",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    # Snapshot #3
    await collection.delete_many({})
    await collection.insert_many(
        [
            {"_id": 1, "value": "three"},
        ]
    )
    snapshot_3_id = await manager.create_snapshot(
        reason="pytest snapshot 3",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    # Restore down to snapshot #1 (creates pre-restore snapshot #4 and appends [4,1]).
    await manager.restore_snapshot(
        str(snapshot_1_id),
        collections=(collection_name,),
        loaded_by_user_id=1,
        capture_pre_restore_snapshot=True,
    )

    restored_docs_1 = await _fetch_all_sorted_by_id(collection)
    assert [doc["value"] for doc in restored_docs_1] == ["one"]

    # Now restore UP to snapshot #3. This must work via the restoration point (#4).
    await manager.restore_snapshot(
        str(snapshot_3_id),
        collections=(collection_name,),
        loaded_by_user_id=2,
        capture_pre_restore_snapshot=True,
    )

    restored_docs_3 = await _fetch_all_sorted_by_id(collection)
    assert [doc["value"] for doc in restored_docs_3] == ["three"]

    history = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history is not None

    # Expected sequence:
    # - created: [1,2,3]
    # - restore #1: append [4,1]
    # - restore #3: append [5,3]
    assert history.snapshot_numbers == [1, 2, 3, 4, 1, 5, 3]
    assert history.last_snapshot_number == 5

    meta_3 = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 3)
    assert meta_3 is not None
    assert meta_3.loaded_at is not None
    assert meta_3.loaded_by_user_id == 2


@pytest.mark.asyncio
async def test_restore_restoration_point_unobsoletes_base_snapshot(mongo_db):
    manager = SnapshotManager(mongo_db)

    await manager.clear_all_snapshots()

    collection_name = "pytest_snapshot_items_restore_point"
    collection = mongo_db.get_collection(collection_name)
    await collection.delete_many({})

    # Create snapshots #1, #2, #3
    await collection.insert_many([
        {"_id": 1, "value": "a"},
    ])
    snapshot_1_id = await manager.create_snapshot(
        reason="pytest snapshot 1",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    await collection.delete_many({})
    await collection.insert_many([
        {"_id": 1, "value": "b"},
    ])
    snapshot_2_id = await manager.create_snapshot(
        reason="pytest snapshot 2",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    await collection.delete_many({})
    await collection.insert_many([
        {"_id": 1, "value": "c"},
    ])
    snapshot_3_id = await manager.create_snapshot(
        reason="pytest snapshot 3",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    # Restore snapshot #3 to create a restoration point snapshot #4 (pre_restore for #3)
    await manager.restore_snapshot(
        str(snapshot_3_id),
        collections=(collection_name,),
        loaded_by_user_id=1,
        capture_pre_restore_snapshot=True,
    )

    pre_restore_4 = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 4)
    assert pre_restore_4 is not None
    assert pre_restore_4.pre_restore_for_snapshot_number == 3
    assert pre_restore_4.obsolete is True

    # Create snapshot #5 to move forward again.
    await collection.delete_many({})
    await collection.insert_many([
        {"_id": 1, "value": "d"},
    ])
    await manager.create_snapshot(
        reason="pytest snapshot 5",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    # Restore snapshot #1: this should obsolete snapshot #3.
    await manager.restore_snapshot(
        str(snapshot_1_id),
        collections=(collection_name,),
        loaded_by_user_id=2,
        capture_pre_restore_snapshot=True,
    )

    meta_3_obsoleted = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 3)
    assert meta_3_obsoleted is not None
    assert meta_3_obsoleted.obsolete is True

    # Restoring the restoration point snapshot (#4) should un-obsolete snapshot #3.
    await manager.restore_snapshot(
        str(pre_restore_4.snapshot_id),
        collections=(collection_name,),
        loaded_by_user_id=3,
        capture_pre_restore_snapshot=True,
    )

    meta_3_after = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 3)
    assert meta_3_after is not None
    assert meta_3_after.obsolete is False


@pytest.mark.asyncio
async def test_permanent_snapshots_are_excluded_from_retention_pruning(mongo_db):
    manager = SnapshotManager(mongo_db)
    await manager.clear_all_snapshots()

    # Keep only 1 non-permanent snapshot, but permanent snapshots should remain indefinitely.
    settings = models.AppSettings(snapshots=models.SnapshotSettings(keep_last=1))
    await settings.insert()

    collection_name = "pytest_snapshot_items_permanent"
    collection = mongo_db.get_collection(collection_name)
    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "a"}])

    permanent_id = await manager.create_snapshot(
        reason="weekly",
        context="weekly_full_snapshot",
        collections=(collection_name,),
        save_in_background=False,
        permanent=True,
    )
    permanent_meta = await manager.get_snapshot_meta(str(permanent_id))
    assert permanent_meta is not None
    assert permanent_meta.snapshot_number == 1
    assert permanent_meta.permanent is True

    # Create 3 normal snapshots -> only the latest non-permanent should remain.
    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "b"}])
    await manager.create_snapshot(
        reason="n2",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "c"}])
    await manager.create_snapshot(
        reason="n3",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "d"}])
    await manager.create_snapshot(
        reason="n4",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    permanent_docs = await models.SnapshotMeta.find({"permanent": True}).to_list()
    assert len(permanent_docs) == 1
    assert permanent_docs[0].snapshot_number == 1

    non_permanent_docs = await models.SnapshotMeta.find({"status": "committed", "permanent": False}).to_list()
    assert len(non_permanent_docs) == 1
    assert non_permanent_docs[0].snapshot_number == 4

    history = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history is not None
    assert 1 in history.snapshot_numbers
    assert 4 in history.snapshot_numbers
    assert 2 not in history.snapshot_numbers
    assert 3 not in history.snapshot_numbers


@pytest.mark.asyncio
async def test_clear_obsolete_snapshots_deletes_and_removes_from_history(mongo_db):
    manager = SnapshotManager(mongo_db)
    await manager.clear_all_snapshots()

    collection_name = "pytest_snapshot_items_cleanup"
    collection = mongo_db.get_collection(collection_name)
    await collection.delete_many({})

    await collection.insert_many([{"_id": 1, "value": "a"}])
    snapshot_1_id = await manager.create_snapshot(
        reason="s1",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "b"}])
    await manager.create_snapshot(
        reason="s2",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    # Restore to snapshot #1 -> snapshot #2 becomes obsolete and pre-restore #3 is appended.
    await manager.restore_snapshot(
        str(snapshot_1_id),
        collections=(collection_name,),
        loaded_by_user_id=123,
        capture_pre_restore_snapshot=True,
    )

    history_before = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history_before is not None
    assert history_before.snapshot_numbers == [1, 2, 3, 1]

    result = await manager.clear_obsolete_snapshots()
    assert result["deleted_meta"] == 2

    meta_2 = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 2)
    assert meta_2 is None
    meta_3 = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == 3)
    assert meta_3 is None

    history_after = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history_after is not None
    assert 2 not in history_after.snapshot_numbers
    assert 3 not in history_after.snapshot_numbers


@pytest.mark.asyncio
async def test_collections_are_collected_across_snapshots(mongo_db):
    manager = SnapshotManager(mongo_db)
    await manager.clear_all_snapshots()

    collection_a = mongo_db.get_collection("pytest_snapshot_collected_a")
    collection_b = mongo_db.get_collection("pytest_snapshot_collected_b")
    await collection_a.delete_many({})
    await collection_b.delete_many({})

    # Snapshot #1: both collections
    await collection_a.insert_many([
        {"_id": 1, "value": "a1"},
    ])
    await collection_b.insert_many([
        {"_id": 1, "value": "b1"},
    ])

    await manager.create_snapshot(
        reason="pytest snapshot base",
        context="pytest",
        collections=("pytest_snapshot_collected_a", "pytest_snapshot_collected_b"),
        save_in_background=False,
    )

    # Snapshot #2: only A
    await collection_a.delete_many({})
    await collection_a.insert_many([
        {"_id": 1, "value": "a2"},
    ])
    snapshot_2_id = await manager.create_snapshot(
        reason="pytest snapshot only A",
        context="pytest",
        collections=("pytest_snapshot_collected_a",),
        save_in_background=False,
    )

    # Snapshot #3: only B
    await collection_b.delete_many({})
    await collection_b.insert_many([
        {"_id": 1, "value": "b3"},
    ])
    await manager.create_snapshot(
        reason="pytest snapshot only B",
        context="pytest",
        collections=("pytest_snapshot_collected_b",),
        save_in_background=False,
    )

    # Restore snapshot #2 while requesting both collections.
    # This forces the algorithm to collect multiple snapshots to cover the collection set.
    await manager.restore_snapshot(
        str(snapshot_2_id),
        collections=("pytest_snapshot_collected_a", "pytest_snapshot_collected_b"),
        loaded_by_user_id=999,
        capture_pre_restore_snapshot=True,
    )

    docs_a = await _fetch_all_sorted_by_id(collection_a)
    docs_b = await _fetch_all_sorted_by_id(collection_b)
    assert [doc["value"] for doc in docs_a] == ["a2"]
    assert [doc["value"] for doc in docs_b] == ["b3"]

    history = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history is not None
    assert history.snapshot_numbers == [1, 2, 3, 4, 2]
    assert history.last_snapshot_number == 4


@pytest.mark.asyncio
async def test_clear_all_snapshots_deletes_permanent_and_resets_counter(mongo_db):
    manager = SnapshotManager(mongo_db)
    await manager.clear_all_snapshots()

    collection_name = "pytest_snapshot_items_clear_all"
    collection = mongo_db.get_collection(collection_name)
    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "a"}])

    permanent_id = await manager.create_snapshot(
        reason="permanent",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
        permanent=True,
    )
    permanent_meta = await manager.get_snapshot_meta(str(permanent_id))
    assert permanent_meta is not None
    assert permanent_meta.permanent is True

    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "b"}])
    await manager.create_snapshot(
        reason="normal",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    await manager.clear_all_snapshots()
    assert await models.SnapshotMeta.count() == 0
    assert await models.SnapshotDataChunk.count() == 0

    history = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
    assert history is None

    # Counter should start again from 1.
    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "c"}])
    new_id = await manager.create_snapshot(
        reason="after_clear",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )
    new_meta = await manager.get_snapshot_meta(str(new_id))
    assert new_meta is not None
    assert new_meta.snapshot_number == 1


@pytest.mark.asyncio
async def test_get_undo_last_snapshot_numbers_skips_full_snapshot_duplicates(mongo_db):
    manager = SnapshotManager(mongo_db)
    await manager.clear_all_snapshots()

    collection_name = "pytest_snapshot_items_undo"
    collection = mongo_db.get_collection(collection_name)
    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "a"}])

    await manager.create_snapshot(
        reason="full",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
        full_snapshot=True,
    )

    current, previous = await manager.get_undo_last_snapshot_numbers()
    assert current == 1
    assert previous is None

    await collection.delete_many({})
    await collection.insert_many([{"_id": 1, "value": "b"}])
    await manager.create_snapshot(
        reason="normal",
        context="pytest",
        collections=(collection_name,),
        save_in_background=False,
    )

    current, previous = await manager.get_undo_last_snapshot_numbers()
    assert current == 2
    assert previous == 1
