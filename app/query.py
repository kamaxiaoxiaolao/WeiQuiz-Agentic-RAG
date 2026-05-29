# d:\study\java\workspace\WeiQuiz\bigModel\app\query.py

import os
# 强制忽略本地地址的代理
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"
from llama_index.core import VectorStoreIndex, StorageContext, load_index_from_storage
from llama_index.core.settings import Settings
# 导入 OpenAILike，不再需要 llama_index.llms.openai.OpenAI
from llama_index.llms.openai_like import OpenAILike 
from llama_index.embeddings.openai import OpenAIEmbedding # 嵌入模型目前保持不变
from app.ingest.loader import get_chroma_vector_store
from app.config import settings # 导入你的配置
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.query_engine import RetrieverQueryEngine
# from llama_index.postprocessor.flag_embedding_reranker import FlagEmbeddingReranker
from llama_index.postprocessor.dashscope_rerank import DashScopeRerank
from llama_index.llms.ollama import Ollama
from llama_index.core.callbacks import CallbackManager, LlamaDebugHandler
from typing import Tuple
from llama_index.core.query_engine import BaseQueryEngine




# 1. 初始化调试处理器
debug_handler = LlamaDebugHandler(print_trace_on_end=True)
callback_manager = CallbackManager([debug_handler])

# 2. 将它注入全局设置
Settings.callback_manager = callback_manager

def setup_query_engine():
    """
    设置并返回一个 LlamaIndex 查询引擎。
    """
    # 1. 配置 LlamaIndex 的全局 Settings
    # LLM 使用 OpenAILike
    Settings.llm = OpenAILike(
        model=settings.llm_model,  # 直接使用 config 中配置的实际模型名称
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        is_chat_model=True, # 假设我们的 qwen 模型是聊天模型
        temperature=0.7,    # 可以从 config 中读取或使用默认值
    )
    
    # 嵌入模型目前仍使用 OpenAIEmbedding，因为没有 OpenAILikeEmbedding 且它之前工作正常
    Settings.embed_model = OpenAIEmbedding(
        api_base=settings.llm_api_base,
        model="text-embedding-ada-002", # 占位符，实际模型名称通过 model_name 传递
        api_key=settings.llm_api_key,
        model_name=settings.embedding_model
    )
    vector_store = get_chroma_vector_store(
        persist_dir=settings.index_dir,
        collection_name="wei_quiz_collection" # 保证和 loader 使用的名称一致
    )

    # 2. 从持久化目录加载存储上下文
    # storage_context = StorageContext.from_defaults(persist_dir=settings.index_dir)

    # 3. 从存储上下文加载 VectorStoreIndex
    # index = load_index_from_storage(storage_context=storage_context)
    index = VectorStoreIndex.from_vector_store(vector_store)

    # 4. 从索引创建查询引擎
    query_engine = index.as_query_engine(similarity_top_k=4)
    return query_engine

# [这是我们要新增的函数]
def setup_rerank_query_engine():
    """
    设置并返回一个集成了 BGE Reranker 的 LlamaIndex 查询引擎。
    """
    # 1. 配置全局 Settings (这部分逻辑和原来一样)
    # Settings.llm = OpenAILike(
    #     model=settings.llm_model,
    #     api_key=settings.llm_api_key,
    #     api_base=settings.llm_api_base,
    #     is_chat_model=True,
    #     temperature=0.7,
    # )
    Settings.llm = Ollama(
        model="qwen3:4b", # 确保你已经 ollama run qwen2:7b
        base_url="http://localhost:11434",
        request_timeout=120.0,
        # 1. 限制 LlamaIndex 端的上下文窗口
        context_window=4096, 
        # 2. 强制 Ollama 端只分配 4096 token 的 KV Cache 内存
        additional_kwargs={"num_ctx": 4096}
    )
    Settings.embed_model = OpenAIEmbedding(
        api_base=settings.llm_api_base,
        model_name=settings.embedding_model,
        api_key=settings.llm_api_key,
    )

    # 2. 连接到 ChromaDB 并加载索引 (这部分逻辑也和原来一样)
    vector_store = get_chroma_vector_store(
        persist_dir=settings.index_dir,
        collection_name="wei_quiz_collection"
    )
    index = VectorStoreIndex.from_vector_store(vector_store=vector_store)

    # 3. 配置重排器
    # reranker = FlagEmbeddingReranker(model="BAAI/bge-reranker-base", top_n=3)
    reranker = DashScopeRerank(
        model="gte-rerank", # 百炼提供的重排模型名称
        top_n=3,
        api_key=settings.llm_api_key # 复用你已有的 API Key
    )

    # 4. 创建查询引擎时，集成重排器
    query_engine = index.as_query_engine(
        similarity_top_k=10,
        node_postprocessors=[reranker]
    )
    
    return query_engine


def setup_hybrid_query_engine():
    # 1. 初始化基础配置 (Settings 保持 Ollama 配置)
    Settings.llm = Ollama(
        model="qwen3:4b", # 确保你已经 ollama run qwen2:7b
        base_url="http://localhost:11434",
        request_timeout=120.0,
        # 1. 限制 LlamaIndex 端的上下文窗口
        context_window=4096, 
        # 2. 强制 Ollama 端只分配 4096 token 的 KV Cache 内存
        additional_kwargs={"num_ctx": 4096}
    )
    Settings.embed_model = OpenAIEmbedding(
        api_base=settings.llm_api_base,
        model_name=settings.embedding_model,
        api_key=settings.llm_api_key,
    )
    # 2. 连接到 ChromaDB 并加载索引 (这部分逻辑也和原来一样)
    vector_store = get_chroma_vector_store(
        persist_dir=settings.index_dir,
        collection_name="wei_quiz_collection"
    )
    # index = VectorStoreIndex.from_vector_store(vector_store=vector_store)
    storage_context = StorageContext.from_defaults(
        persist_dir=settings.index_dir, 
        vector_store=vector_store
    )
    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        storage_context=storage_context # 将 storage_context 传给 Index
    )
    # 3. 【核心】获取所有节点以构建 BM25 索引
     # 注意：在企业级应用中，BM25 索引通常也需要持久化。这里我们演示动态构建
    # nodes = index.as_retriever().retrieve(" ") # 技巧：通过空查询拿到一些基础节点，或者从 storage 中获取
    # 更标准做法是从 docstore 获取所有 nodes
    # nodes = index.docstore.get_all_nodes()
    all_nodes = list(storage_context.docstore.docs.values())
    print(f"🎉 成功从本地 docstore 加载了 {len(all_nodes)} 个文本节点用于 BM25！")
    vector_retriever = index.as_retriever(similarity_top_k=4)
    # 4. 【核心】创建 BM25Retriever
    bm25_retriever = BM25Retriever.from_defaults(
        nodes=all_nodes,
        similarity_top_k=4
    )
    # 5. 【融合】使用 RRF 算法融合多路结果
    fusion_retriever = QueryFusionRetriever(
        [vector_retriever, bm25_retriever],
        similarity_top_k=10, # 融合后保留多少个给 Reranker
        num_queries=1,       # 如果开启多查询，可以设置大于1
        mode="reciprocal_rerank", # 使用 RRF 算法
        use_async=True,
    )
    # 6. 【重排】使用 BGE Reranker 对融合结果进行重排
    reranker = DashScopeRerank(model="gte-rerank", top_n=3, api_key=settings.llm_api_key)
    # 7. 构建最终查询引擎
    query_engine = RetrieverQueryEngine.from_args(
        retriever=fusion_retriever,
        node_postprocessors=[reranker]
    )
    return query_engine




if __name__ == "__main__":
    print("加载知识库并准备查询引擎...")
    # query_engine = setup_query_engine()
    # query_engine = setup_rerank_query_engine()
    query_engine = setup_hybrid_query_engine()
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