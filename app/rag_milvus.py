from typing import Tuple

from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.settings import Settings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai_like import OpenAILike
from llama_index.postprocessor.dashscope_rerank import DashScopeRerank

from app.config import settings as app_settings
from app.ingest.milvus_loader import get_default_vector_store
from app.metadata_schema import SourceNodePayload
from app.retrieval.bm25_state import build_stateful_bm25_retriever
from app.retrieval.auto_merging_context import AutoMergingContextPostprocessor
from app.retrieval.parent_context import ParentContextPostprocessor
from app.storage.parent_store import build_parent_store


def setup_llamaindex_settings():
    Settings.llm = OpenAILike(
        model=app_settings.llm_model,
        api_key=app_settings.llm_api_key,
        api_base=app_settings.llm_api_base,
        is_chat_model=True,
        temperature=0.7,
    )
    Settings.embed_model = OpenAIEmbedding(
        model_name=app_settings.embedding_model,
        api_base=app_settings.embedding_api_base,
        api_key=app_settings.qwen_llm_api_key,
        embed_batch_size=app_settings.embedding_batch_size,
    )


def build_milvus_index_and_storage() -> Tuple[VectorStoreIndex, StorageContext]:
    setup_llamaindex_settings()

    vector_store = get_default_vector_store(
        settings=app_settings,
        index_dir=app_settings.index_dir,
        collection_name=app_settings.milvus_collection,
        dim=app_settings.pgvector_embed_dim,
    )

    if app_settings.parent_store_enabled:
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
    else:
        storage_context = StorageContext.from_defaults(
            persist_dir=app_settings.index_dir,
            vector_store=vector_store,
        )

    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        storage_context=storage_context,
    )
    return index, storage_context


def build_milvus_query_engine(index: VectorStoreIndex, storage_context: StorageContext) -> RetrieverQueryEngine:
    parent_store = build_parent_store(app_settings.postgres_url) if app_settings.parent_store_enabled else None
    if parent_store is not None:
        all_nodes = parent_store.list_chunk_nodes()
    else:
        all_nodes = list(storage_context.docstore.docs.values())

    vector_retriever = index.as_retriever(similarity_top_k=4)
    bm25_retriever = build_stateful_bm25_retriever(
        nodes=all_nodes,
        similarity_top_k=4,
    )

    fusion_retriever = QueryFusionRetriever(
        [vector_retriever, bm25_retriever],
        similarity_top_k=10,
        num_queries=1,
        mode="reciprocal_rerank",
        use_async=False,
    )

    reranker = DashScopeRerank(
        model="gte-rerank",
        top_n=3,
        api_key=app_settings.qwen_llm_api_key,
    )
    node_postprocessors = []
    if parent_store is not None:
        if app_settings.auto_merging_enabled:
            node_postprocessors.append(
                AutoMergingContextPostprocessor(
                    parent_store=parent_store,
                    merge_threshold=app_settings.auto_merging_threshold,
                    max_merge_chars=app_settings.auto_merging_max_chars,
                )
            )
        else:
            node_postprocessors.append(ParentContextPostprocessor(parent_store=parent_store))
    node_postprocessors.append(reranker)

    query_engine = RetrieverQueryEngine.from_args(
        retriever=fusion_retriever,
        node_postprocessors=node_postprocessors,
    )
    return fusion_retriever, reranker, query_engine


def build_rag_components() -> Tuple[VectorStoreIndex, QueryFusionRetriever, DashScopeRerank, RetrieverQueryEngine]:
    index, storage_context = build_milvus_index_and_storage()
    fusion_retriever, reranker, query_engine = build_milvus_query_engine(index=index, storage_context=storage_context)
    return index, fusion_retriever, reranker, query_engine


if __name__ == "__main__":
    index, query_engine = build_rag_components()
    print("RAG 查询引擎初始化完成。")
    print("查询引擎准备就绪！")

    while True:
        question = input("请输入你的问题 (输入 'exit' 退出): ")
        if question.lower() == 'exit':
            break
        
        print(f"正在查询：{question}")
        response = query_engine.query(question)
        print("\n--- 召回的源节点 (Top K) ---")
        if response.source_nodes:
            for i, node in enumerate(response.source_nodes):
                # 尝试获取文件名，如果不存在则显示“未知文件”
                file_name = node.metadata.get('file_name', '未知文件') 
                print(f"节点 {i+1} (分数: {node.score:.4f}) - 来自文件: {file_name}:")
                print("--------------------------------------------------")
                print(node.text)
                print("--------------------------------------------------\n")
        else:
            print("未召回任何源节点。")
        print("--------------------------------------------------\n")
        # --- 新增的代码结束 ---

        print("回答：", response.response)
        print("-" * 50)
