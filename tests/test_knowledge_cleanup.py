"""Knowledge cleanup safety regressions."""

from unittest.mock import MagicMock

from scripts.clean_knowledge import fetch_all_knowledge_chunks


def test_fetch_all_knowledge_chunks_reads_every_postgrest_page(monkeypatch):
    first_page = [{"id": index, "content": f"chunk-{index}"} for index in range(1000)]
    second_page = [{"id": 1000, "content": "chunk-1000"}]
    query = MagicMock()
    query.select.return_value = query
    query.order.return_value = query
    query.range.return_value = query
    query.execute.side_effect = [
        MagicMock(data=first_page),
        MagicMock(data=second_page),
    ]
    client = MagicMock()
    client.table.return_value = query
    monkeypatch.setattr("scripts.clean_knowledge.get_supabase", lambda: client)

    result = fetch_all_knowledge_chunks()

    assert len(result) == 1001
    assert result[-1]["id"] == 1000
    assert query.range.call_args_list[0].args == (0, 999)
    assert query.range.call_args_list[1].args == (1000, 1999)
