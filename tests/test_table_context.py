from llama_index.core.schema import NodeWithScore, TextNode

from app.retrieval.table_context import TableContextPostprocessor, extract_table_id


class FakeParentStore:
    def __init__(self, nodes):
        self.nodes = nodes

    def list_chunk_nodes(self, doc_ids=None, leaf_only=True):
        doc_ids = set(doc_ids or [])
        return [
            node
            for node in self.nodes
            if not doc_ids or node.metadata.get("doc_id") in doc_ids
        ]


def test_extract_table_id_from_text_marker():
    assert extract_table_id("[TABLE_ID: doc::table::1]\n| A | B |") == "doc::table::1"


def test_table_context_postprocessor_expands_same_table_nodes():
    store_nodes = [
        TextNode(
            id_="parent-1",
            text="[TABLE_ID: report::table::0]\n[TABLE_PAGE_RANGE: 1-2]\n| 指标 | 数值 |\n| --- | --- |\n| A | 1 |",
            metadata={
                "doc_id": "report",
                "source_path": "report.pdf",
                "chunk_id": "parent-1",
                "chunk_role": "parent",
                "chunk_index": 1,
            },
        ),
        TextNode(
            id_="parent-2",
            text="[TABLE_ID: report::table::0]\n| 指标 | 数值 |\n| --- | --- |\n| B | 2 |",
            metadata={
                "doc_id": "report",
                "source_path": "report.pdf",
                "chunk_id": "parent-2",
                "chunk_role": "parent",
                "chunk_index": 2,
            },
        ),
    ]
    hit = NodeWithScore(
        node=TextNode(
            id_="leaf-1",
            text="[TABLE_ID: report::table::0]\n| B | 2 |",
            metadata={
                "doc_id": "report",
                "source_path": "report.pdf",
                "chunk_id": "leaf-1",
                "chunk_role": "leaf",
                "parent_id": "parent-2",
            },
        ),
        score=0.8,
    )
    postprocessor = TableContextPostprocessor(parent_store=FakeParentStore(store_nodes))

    result = postprocessor.postprocess_nodes([hit])

    assert len(result) == 1
    assert result[0].node.metadata["retrieval_mode"] == "table_context"
    assert result[0].node.metadata["table_id"] == "report::table::0"
    assert result[0].node.metadata["table_page_range"] == "1-2"
    assert result[0].node.metadata["child_chunk_id"] == "leaf-1"
    assert "| A | 1 |" in result[0].node.text
    assert "| B | 2 |" in result[0].node.text
