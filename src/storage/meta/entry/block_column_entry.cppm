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

export module block_column_entry;

import stl;
import buffer_obj;
import data_type;
import third_party;
import buffer_manager;
import column_vector;
import vector_buffer;
import txn;
import internal_types;
import base_entry;
import column_def;
import value;
import cleanup_scanner;
import snapshot_info;
import meta_info;

namespace infinity {

struct BlockEntry;
struct TableEntry;
struct SegmentEntry;

export struct BlockColumnEntry final : public BaseEntry {
public:
    friend struct BlockEntry;

    static Vector<std::string_view> DecodeIndex(std::string_view encode);

    static String EncodeIndex(const ColumnID column_id, const BlockEntry *block_entry);

public:
    explicit BlockColumnEntry(const BlockEntry *block_entry, ColumnID column_id);

    ~BlockColumnEntry() override;

private:
    BlockColumnEntry(const BlockColumnEntry &other);

public:
    UniquePtr<BlockColumnEntry> Clone(BlockEntry *block_entry) const;

    static UniquePtr<BlockColumnEntry> NewBlockColumnEntry(const BlockEntry *block_entry, ColumnID column_id, Txn *txn);

    static UniquePtr<BlockColumnEntry> NewReplayBlockColumnEntry(const BlockEntry *block_entry,
                                                                 ColumnID column_id,
                                                                 BufferManager *buffer_manager,
                                                                 const u32 next_outline_idx,
                                                                 const u64 last_chunk_offset,
                                                                 const TxnTimeStamp commit_ts);

    static UniquePtr<BlockColumnEntry> ApplyBlockColumnSnapshot(BlockEntry *block_entry,
                                                                BlockColumnSnapshotInfo *block_column_snapshot_info,
                                                                TransactionID txn_id,
                                                                TxnTimeStamp begin_ts);

    SharedPtr<BlockColumnInfo> GetColumnInfo() const;

    SharedPtr<BlockColumnSnapshotInfo> GetSnapshotInfo() const;

    nlohmann::json Serialize();

    static UniquePtr<BlockColumnEntry> Deserialize(const nlohmann::json &column_data_json, BlockEntry *block_entry, BufferManager *buffer_mgr);

    void CommitColumn(TransactionID txn_id, TxnTimeStamp commit_ts);

public:
    // Getter
    inline const SharedPtr<DataType> &column_type() const { return column_type_; }
    inline BufferObj *buffer() const { return buffer_; }
    inline u64 column_id() const { return column_id_; }
    inline const SharedPtr<String> &filename() const { return filename_; }
    inline const BlockEntry *block_entry() { return block_entry_; }

    SharedPtr<String> OutlineFilename(SizeT file_idx) const { return MakeShared<String>(fmt::format("col_{}_out_{}", column_id_, file_idx)); }

    // Relative to `data_dir` config item
    String FilePath() const;

    // Relative to `data_dir` config item
    SharedPtr<String> FileDir() const;

    Vector<String> FilePaths() const;

public:
    ColumnVector GetColumnVector(BufferManager *buffer_mgr, SizeT row_count);

    ColumnVector GetConstColumnVector(BufferManager *buffer_mgr, SizeT row_count);

private:
    ColumnVector GetColumnVectorInner(BufferManager *buffer_mgr, const ColumnVectorTipe tipe, SizeT row_count);

public:
    void AppendOutlineBuffer(BufferObj *buffer) {
        std::unique_lock lock(mutex_);
        outline_buffers_.emplace_back(buffer);
        buffer->AddObjRc();
    }

    BufferObj *GetOutlineBuffer(SizeT idx) const {
        std::shared_lock lock(mutex_);
        return outline_buffers_.empty() ? nullptr : outline_buffers_[idx];
    }

    SizeT OutlineBufferCount() const {
        std::shared_lock lock(mutex_);
        return outline_buffers_.size();
    }

    u64 LastChunkOff() const { return last_chunk_offset_; }

    void SetLastChunkOff(u64 offset) { last_chunk_offset_ = offset; }

public:
    static void Flush(BlockColumnEntry *block_column_entry, SizeT start_row_count, SizeT checkpoint_row_count);

    void FlushColumnCheck(TxnTimeStamp checkpoint_ts);

    void FlushColumn(TxnTimeStamp checkpoint_ts);

    void Cleanup(CleanupInfoTracer *info_tracer = nullptr, bool dropped = true) override;

    Vector<String> GetFilePath(Txn* txn) const final;

    void DropColumn();

    void FillWithDefaultValue(SizeT row_count, const Value *default_value, BufferManager *buffer_mgr);

    SizeT GetStorageSize() const;

    void ToMmap();

private:
    const BlockEntry *block_entry_{nullptr};
    ColumnID column_id_{};
    SharedPtr<DataType> column_type_{};
    BufferObj *buffer_{};

    SharedPtr<String> filename_{};

    mutable std::shared_mutex mutex_{};
    Vector<BufferObj *> outline_buffers_;
    u64 last_chunk_offset_{};
};

} // namespace infinity
