from types import SimpleNamespace

from app.metadata_schema import (
    CanonicalChunkMetadata,
    SectionMetadata,
    SourceNodePayload,
    build_auto_merged_metadata,
    build_hierarchy_node_metadata,
    build_parent_context_metadata,
)


def test_canonical_metadata_uses_source_path_basename_as_file_name():
    metadata = {
        "doc_id": "doc_api_gateway_billing_v3",
        "source_path": "data/docs/doc_api_gateway_billing_v3.txt",
        "chunk_id": "chunk-1",
        "chunk_role": "leaf",
    }

    result = CanonicalChunkMetadata.from_raw(metadata)

    assert result.doc_id == "doc_api_gateway_billing_v3"
    assert result.file_name == "doc_api_gateway_billing_v3.txt"
    assert result.source_path == "data/docs/doc_api_gateway_billing_v3.txt"
    assert result.chunk_role == "leaf"


def test_source_payload_supports_auto_merged_parent_metadata():
    node = SimpleNamespace(
        text="parent context",
        score=0.5547,
        metadata={
            "doc_id": "doc_sec_compliance_2026_full",
            "source_path": "data/docs/doc_sec_compliance_2026_full.txt",
            "chunk_id": "parent-1",
            "parent_id": "parent-1",
            "section_title": "数据安全分级",
            "page_range": "3-4",
            "retrieval_mode": "auto_merging",
            "auto_merged": True,
            "merge_ratio": "0.75",
            "merged_child_count": "3",
            "total_child_count": "4",
        },
    )

    payload = SourceNodePayload.from_node(node).to_api_dict()

    assert payload["file_name"] == "doc_sec_compliance_2026_full.txt"
    assert payload["source_path"] == "data/docs/doc_sec_compliance_2026_full.txt"
    assert payload["parent_source_path"] == "data/docs/doc_sec_compliance_2026_full.txt"
    assert payload["parent_section_title"] == "数据安全分级"
    assert payload["parent_page_range"] == "3-4"
    assert payload["auto_merged"] is True
    assert payload["merge_ratio"] == 0.75
    assert payload["merged_child_count"] == 3
    assert payload["total_child_count"] == 4


def test_source_payload_falls_back_to_doc_id_when_path_missing():
    node = SimpleNamespace(
        text="unknown path text",
        score=None,
        metadata={"doc_id": "fallback_doc", "chunk_id": "chunk-2"},
    )

    payload = SourceNodePayload.from_node(node).to_api_dict()

    assert payload["file_name"] == "fallback_doc"
    assert payload["doc_id"] == "fallback_doc"
    assert payload["chunk_id"] == "chunk-2"


def test_build_hierarchy_node_metadata_keeps_section_separate_from_parent():
    metadata = build_hierarchy_node_metadata(
        base_metadata={
            "doc_id": "doc_api_gateway_billing_v3",
            "source_path": "data/docs/doc_api_gateway_billing_v3.txt",
            "file_type": ".txt",
            "page_range": "1-2",
            "section_id": "section-should-not-leak",
            "section_title": "网关升级",
        },
        node_id="leaf-1",
        parent_id="parent-1",
        role="leaf",
        source_section_count=3,
    )

    assert metadata["doc_id"] == "doc_api_gateway_billing_v3"
    assert metadata["file_name"] == "doc_api_gateway_billing_v3.txt"
    assert metadata["chunk_role"] == "leaf"
    assert metadata["chunk_id"] == "leaf-1"
    assert metadata["parent_id"] == "parent-1"
    assert metadata["source_section_count"] == 3
    assert "section_id" not in metadata
    assert "section_title" not in metadata


def test_section_metadata_is_preprocessing_only():
    section = SectionMetadata.from_raw(
        {
            "doc_id": "doc-1",
            "source_path": "data/docs/doc-1.md",
            "section_id": "section-1",
            "section_index": "2",
            "section_title": "标题",
            "page_range": "5",
            "block_count": "8",
        }
    )

    assert section.section_id == "section-1"
    assert section.section_index == 2
    assert section.block_count == 8
    assert not hasattr(section, "parent_id")


def test_build_parent_context_metadata_uses_schema_contract():
    metadata = build_parent_context_metadata(
        child_metadata={
            "doc_id": "doc-1",
            "source_path": "data/docs/doc-1.txt",
            "chunk_id": "leaf-1",
            "parent_id": "parent-1",
        },
        parent_row={
            "chunk_id": "parent-1",
            "parent_id": "root-1",
            "doc_id": "doc-1",
            "source_path": "data/docs/doc-1.txt",
            "page_range": "2-3",
            "metadata_json": {
                "chunk_id": "parent-1",
                "chunk_role": "parent",
                "section_title": "检索架构",
            },
        },
        child_text="leaf text",
    )

    assert metadata["retrieval_mode"] == "parent_child"
    assert metadata["doc_id"] == "doc-1"
    assert metadata["chunk_id"] == "parent-1"
    assert metadata["chunk_role"] == "parent"
    assert metadata["parent_id"] == "root-1"
    assert metadata["parent_section_title"] == "检索架构"
    assert metadata["child_chunk_id"] == "leaf-1"


def test_build_auto_merged_metadata_uses_schema_contract():
    metadata = build_auto_merged_metadata(
        parent_row={
            "chunk_id": "parent-1",
            "parent_id": "root-1",
            "doc_id": "doc-1",
            "source_path": "data/docs/doc-1.txt",
            "page_range": "2-3",
            "metadata_json": {
                "chunk_id": "parent-1",
                "chunk_role": "parent",
                "section_title": "自动合并",
            },
        },
        child_metadatas=[
            {"doc_id": "doc-1", "chunk_id": "leaf-1", "parent_id": "parent-1"},
            {"doc_id": "doc-1", "chunk_id": "leaf-2", "parent_id": "parent-1"},
        ],
        merge_threshold=0.5,
        total_child_count=4,
    )

    assert metadata["retrieval_mode"] == "auto_merging"
    assert metadata["auto_merged"] is True
    assert metadata["chunk_id"] == "parent-1"
    assert metadata["parent_id"] == "root-1"
    assert metadata["merged_child_count"] == 2
    assert metadata["total_child_count"] == 4
    assert metadata["merge_ratio"] == 0.5
    assert metadata["merged_child_ids"] == ["leaf-1", "leaf-2"]
