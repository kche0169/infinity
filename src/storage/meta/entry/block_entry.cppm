// Copyright(C) 2023 InfiniFlow, Inc. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

module;

#include "type/complex/row_id.h"

export module block_entry;

import stl;
import default_values;
import third_party;
import data_type;
import virtual_store;
import column_vector;
import roaring_bitmap;
import internal_types;
import base_entry;
import block_column_entry;
import fast_rough_filter;
import value;
import buffer_obj;
import column_def;
import txn_store;
import cleanup_scanner;
import snapshot_info;
import meta_info;
import txn;
import status;

namespace infinity {

class BufferManager;
struct SegmentEntry;
struct TableEntry;
class DataBlock;
struct WalBlockInfo;

/// class BlockEntry
export struct BlockEntry final : public BaseEntry {
public:
    friend struct TableEntry;
    friend struct SegmentEntry;
    friend struct WalBlockInfo;

    static Vector<std::string_view> DecodeIndex(std::string_view encode);

    static String EncodeIndex(const BlockID block_id, const SegmentEntry *segment_entry);

public:
    // for iterator unit test
    explicit BlockEntry() : BaseEntry(EntryType::kBlock, false, "") {};

    ~BlockEntry() override;

private:
    BlockEntry(const BlockEntry &other);

public:
    UniquePtr<BlockEntry> Clone(SegmentEntry *segment_entry) const;

    // Normal Constructor
    explicit BlockEntry(const SegmentEntry *segment_entry, BlockID block_id, TxnTimeStamp checkpoint_ts);

    static UniquePtr<BlockEntry>
    NewBlockEntry(const SegmentEntry *segment_entry, BlockID block_id, TxnTimeStamp checkpoint_ts, u64 column_count, Txn *txn);

    static UniquePtr<BlockEntry> NewReplayBlockEntry(const SegmentEntry *segment_entry,
                                                     BlockID block_id,
                                                     u16 row_count,
                                                     u16 row_capacity,
                                                     TxnTimeStamp min_row_ts,
                                                     TxnTimeStamp max_row_ts,
                                                     TxnTimeStamp commit_ts,
                                                     TxnTimeStamp check_point_ts,
                                                     u16 checkpoint_row_count,
                                                     BufferManager *buffer_mgr,
                                                     TransactionID txn_id);

    static SharedPtr<BlockEntry>
    ApplyBlockSnapshot(SegmentEntry *segment_entry, BlockSnapshotInfo *block_snapshot_info, TransactionID txn_id, TxnTimeStamp begin_ts);

    void UpdateBlockReplay(SharedPtr<BlockEntry> block_entry, String block_filter_binary_data);

public:
    nlohmann::json Serialize(TxnTimeStamp max_commit_ts);

    static UniquePtr<BlockEntry> Deserialize(const nlohmann::json &table_entry_json, SegmentEntry *table_entry, BufferManager *buffer_mgr);

    void AddColumnReplay(UniquePtr<BlockColumnEntry> column_entry, ColumnID column_id);

    void DropColumnReplay(ColumnID column_id);

    void AppendBlock(const Vector<ColumnVector> &column_vectors, SizeT row_begin, SizeT read_size, BufferManager *buffer_mgr);

    void Cleanup(CleanupInfoTracer *info_tracer = nullptr, bool dropped = true) override;

    Vector<String> GetFilePath(Txn *txn) const final;

    void Flush(TxnTimeStamp checkpoint_ts);

    void FlushForImport();

    void LoadFilterBinaryData(const String &block_filter_data);

protected:
    u16
    AppendData(TransactionID txn_id, TxnTimeStamp commit_ts, DataBlock *input_data_block, BlockOffset, u16 append_rows, BufferManager *buffer_mgr);

    SizeT DeleteData(TransactionID txn_id, TxnTimeStamp commit_ts, const Vector<BlockOffset> &rows);

    void CommitFlushed(TxnTimeStamp commit_ts, WalBlockInfo *block_info);

    void CommitBlock(TransactionID txn_id, TxnTimeStamp commit_ts);

    static SharedPtr<String> DetermineDir(const String &parent_dir, BlockID block_id);

public:
    // Getter
    inline const SegmentEntry *GetSegmentEntry() const { return segment_entry_; }

    inline SizeT row_count() const {
        std::shared_lock lock(rw_locker_);
        return block_row_count_;
    }

    inline SizeT row_capacity() const { return row_capacity_; }

    inline TxnTimeStamp min_row_ts() const { return min_row_ts_; }

    inline TxnTimeStamp max_row_ts() const { return max_row_ts_; }

    inline TxnTimeStamp checkpoint_ts() const { return checkpoint_ts_; }

    inline TransactionID using_txn_id() const { return using_txn_id_; }

    inline u16 checkpoint_row_count() const { return checkpoint_row_count_; }

    inline u16 block_id() const { return block_id_; }

    u32 segment_id() const;

    u32 segment_offset() const { return u32(block_id() * row_capacity()); }

    RowID base_row_id() const { return RowID(segment_id(), segment_offset()); }

    // Relative to the `data_dir` config item
    const SharedPtr<String> &block_dir() const { return block_dir_; }

    BlockColumnEntry *GetColumnBlockEntry(SizeT column_idx) const;

    ColumnVector GetColumnVector(BufferManager *buffer_mgr, ColumnID column_id) const;

    ColumnVector GetConstColumnVector(BufferManager *buffer_mgr, ColumnID column_id) const;

    FastRoughFilter *GetFastRoughFilter() { return fast_rough_filter_.get(); }

    const FastRoughFilter *GetFastRoughFilter() const { return fast_rough_filter_.get(); }

    SizeT row_count(TxnTimeStamp check_ts) const;

    // Get visible range of the BlockEntry since the given row number for a txn
    Pair<BlockOffset, BlockOffset> GetVisibleRange(TxnTimeStamp begin_ts, BlockOffset block_offset_begin = 0) const;

    bool CheckRowVisible(BlockOffset block_offset, TxnTimeStamp check_ts, bool check_append) const;

    void CheckRowsVisible(Vector<u32> &segment_offsets, TxnTimeStamp check_ts) const;

    void CheckRowsVisible(Bitmask &segment_offsets, TxnTimeStamp check_ts) const;

    bool CheckDeleteConflict(const Vector<BlockOffset> &block_offsets, TxnTimeStamp commit_ts) const;

    void SetDeleteBitmask(TxnTimeStamp query_ts, Bitmask &bitmask) const;

    i32 GetAvailableCapacity();

    String VersionFilePath();

    const SharedPtr<DataType> GetColumnType(u64 column_id) const;

    Vector<UniquePtr<BlockColumnEntry>> &columns() { return columns_; }

    ColumnVector GetCreateTSVector(BufferManager *buffer_mgr, SizeT offset, SizeT size) const;

    ColumnVector GetDeleteTSVector(BufferManager *buffer_mgr, SizeT offset, SizeT size) const;

    void AddColumns(const Vector<Pair<ColumnID, const Value *>> &columns, TxnTableStore *table_store);

    void DropColumns(const Vector<ColumnID> &column_ids, TxnTableStore *table_store);

    SharedPtr<BlockSnapshotInfo> GetSnapshotInfo() const;

    SharedPtr<BlockInfo> GetBlockInfo(Txn *txn) const;

    Tuple<SharedPtr<BlockColumnInfo>, Status> GetBlockColumnInfo(ColumnID column_id) const;

public:
    // Setter, Used in import, segment append block, and block append block in compact
    inline void IncreaseRowCount(SizeT increased_row_count) { block_row_count_ += increased_row_count; }

    SizeT GetStorageSize() const;

    void CheckFlush(TxnTimeStamp ts, bool &flush_column, bool &flush_version, bool check_commit, bool need_lock) const;

    bool TryToMmap();

private:
    void FlushDataNoLock(SizeT start_row_count, SizeT checkpoint_row_count);

    bool FlushVersionNoLock(TxnTimeStamp checkpoint_ts, bool check_commit = true);

protected:
    mutable std::shared_mutex rw_locker_{};
    const SegmentEntry *segment_entry_{};

    BlockID block_id_{};
    SharedPtr<String> block_dir_{};

    u16 block_row_count_{};
    u16 row_capacity_{};

    BufferObj *version_buffer_object_{};

    // check if a value must not exist in the block
    SharedPtr<FastRoughFilter> fast_rough_filter_ = MakeShared<FastRoughFilter>();

    TxnTimeStamp min_row_ts_{};     // Indicate the commit_ts last append
    TxnTimeStamp max_row_ts_{};     // Indicate the commit_ts last append/update/delete
    TxnTimeStamp checkpoint_ts_{0}; // replay not set

    TransactionID using_txn_id_{0}; // Temporarily used to lock the modification to block entry.

    // checkpoint state
    u16 checkpoint_row_count_{0};

    // Column data
    Vector<UniquePtr<BlockColumnEntry>> columns_{};
    Vector<UniquePtr<BlockColumnEntry>> dropped_columns_{};
};
} // namespace infinity
