from app.ingest.document_parser import (
    Block,
    blocks_to_llama_documents,
    merge_cross_page_tables,
    split_large_tables,
)


def test_merge_cross_page_tables_keeps_one_table_id_and_page_range():
    blocks = [
        Block(
            block_type="table",
            text="| 指标 | 数值 |\n| --- | --- |\n| A | 1 |",
            page_no=1,
            source_path="report.pdf",
            doc_id="report",
        ),
        Block(
            block_type="table",
            text="| 指标 | 数值 |\n| --- | --- |\n| B | 2 |",
            page_no=2,
            source_path="report.pdf",
            doc_id="report",
        ),
    ]

    merged = merge_cross_page_tables(blocks)

    assert len(merged) == 1
    assert merged[0].extra_info["table_id"] == "report::table::0"
    assert merged[0].extra_info["is_cross_page_table"] is True
    assert merged[0].extra_info["page_range"] == "1-2"
    assert merged[0].extra_info["source_pages"] == [1, 2]
    assert "| A | 1 |" in merged[0].text
    assert "| B | 2 |" in merged[0].text


def test_merge_cross_page_tables_does_not_merge_different_headers():
    blocks = [
        Block(
            block_type="table",
            text="| 指标 | 数值 |\n| --- | --- |\n| A | 1 |",
            page_no=1,
            source_path="report.pdf",
            doc_id="report",
        ),
        Block(
            block_type="table",
            text="| 名称 | 状态 |\n| --- | --- |\n| B | 通过 |",
            page_no=2,
            source_path="report.pdf",
            doc_id="report",
        ),
    ]

    merged = merge_cross_page_tables(blocks)

    assert len(merged) == 2
    assert merged[0].extra_info["table_id"] == "report::table::0"
    assert merged[1].extra_info["table_id"] == "report::table::1"


def test_table_section_text_includes_stable_table_marker():
    blocks = merge_cross_page_tables(
        [
            Block(
                block_type="table",
                text="| 指标 | 数值 |\n| --- | --- |\n| A | 1 |",
                page_no=1,
                source_path="report.pdf",
                doc_id="report",
            )
        ]
    )

    docs = blocks_to_llama_documents(blocks, min_section_chars=0)

    assert "[TABLE_ID: report::table::0]" in docs[0].text
    assert "[TABLE_PAGE_RANGE: 1]" in docs[0].text
    assert "[TABLE_HEADERS: 指标, 数值]" in docs[0].text
