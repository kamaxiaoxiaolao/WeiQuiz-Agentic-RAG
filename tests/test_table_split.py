from app.ingest.document_parser import Block, merge_cross_page_tables, split_large_tables


def test_split_large_tables_splits_by_rows_and_repeats_header():
    body = "\n".join(f"| item-{i} | value-{i} |" for i in range(12))
    blocks = merge_cross_page_tables(
        [
            Block(
                block_type="table",
                text=f"| name | value |\n| --- | --- |\n{body}",
                page_no=1,
                source_path="report.pdf",
                doc_id="report",
            )
        ]
    )

    split_blocks = split_large_tables(blocks, max_table_chars=120)

    assert len(split_blocks) > 1
    assert {block.extra_info["table_id"] for block in split_blocks} == {"report::table::0"}
    assert all(block.text.startswith("| name | value |") for block in split_blocks)
    assert all("| --- | --- |" in block.text for block in split_blocks)
    assert split_blocks[0].extra_info["table_part_index"] == 0
    assert split_blocks[-1].extra_info["table_part_count"] == len(split_blocks)
