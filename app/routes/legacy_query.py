"""Legacy single-turn query route."""

from __future__ import annotations

from fastapi import APIRouter

from app.api_support.helpers import _source_node_payload
from app.api_support.schemas import QueryRequest
from app.api_support.state import get_app_state


router = APIRouter()


@router.post("/query", deprecated=True)
async def query_rag(request: QueryRequest):
    """Legacy single-turn query endpoint.

    The Vue application uses /chat/stream. This endpoint is kept only for the
    old Streamlit debug client and quick manual checks.
    """

    query_engine = getattr(get_app_state(), "query_engine", None)
    if query_engine is None:
        return {"error": "RAG query engine is not initialized."}, 500

    print(f"\n🔍 【单次查询】问题：{request.question}")
    response = query_engine.query(request.question)

    source_nodes_data = []
    if response.source_nodes:
        for node in response.source_nodes:
            source_nodes_data.append(_source_node_payload(node))
    return {"answer": response.response, "source_nodes": source_nodes_data}
