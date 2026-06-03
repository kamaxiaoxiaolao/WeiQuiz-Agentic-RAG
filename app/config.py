# app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
import os # 导入 os 模块，用于判断环境变量
from dotenv import load_dotenv # 导入 load_dotenv，用于在测试时加载 .env 文件

class Settings(BaseSettings):
    """
    项目配置类，通过 pydantic-settings 管理。
    配置项可以从环境变量、.env 文件、或默认值加载。
    """
    model_config = SettingsConfigDict(
        env_file=".env",  # 指定 .env 文件
        env_file_encoding="utf-8",
        extra="ignore" # 忽略 .env 文件中未在 Settings 类中定义的变量
    )

    # --- 通用配置 ---
    project_name: str = "WeiQuiz RAG"
    docs_dir: str = "data/docs" # 知识库文档存放目录
    index_dir: str = "data/index" # 索引文件存放目录
    audit_dir: str = "data/audit" # 文档审计文件存放目录
    milvus_dir: str = "data/index/milvus" # Milvus 向量数据库存放目录
    milvus_uri: str = "http://localhost:19530" # Milvus 服务地址
    milvus_collection: str = "wei_quiz_collection"
    vector_store_backend: str = "pgvector"
    pgvector_table_name: str = "wei_quiz_vectors"
    pgvector_embed_dim: int = 1536
    test_md_path: str = "data/md/data/md/test.md" # 测试 Markdown 文档路径
    test_md_name: str = "test" # 测试 Markdown 文档名称
    test_pdf_path: str = "data/pdf/test.pdf" # 测试 PDF 文档路径
    test_pdf_name: str = "test" # 测试 PDF 文档名称
    # --- 模型配置 (阿里云百炼 DashScope 兼容 OpenAI API) ---
    # 使用 openai Python SDK 接入 DashScope 的 OpenAI 兼容 API
    # base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_base: str = "https://token-plan-cn.xiaomimimo.com/v1"
    embedding_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    router_api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ollama_base_url: str = "http://localhost:11434"
    # 阿里云 DashScope 提供的模型名称，例如 Qwen 系列（用于文本生成）
    # 请根据你在百炼开通的具体模型服务进行选择，例如：qwen-turbo, qwen-plus, qwen-max
    llm_model: str = "mimo-v2.5-pro"
    router_model: str = "qwen3.6-flash"
    router_timeout_seconds: float = 5.0
    
    # Embedding 模型名称，DashScope 通常提供 text-embedding-v1 或 text-embedding-v2
    # LlamaIndex 可以通过 OpenAIEmbedding 类，指向这个 base_url 来使用 DashScope 的 Embedding 服务
    embedding_model: str = "text-embedding-v1"
    embedding_batch_size: int = 20
    
    # 阿里云 DashScope 的 API Key
    llm_api_key: str # 此处不再设置默认值，强制要求从环境变量或 .env 加载
    qwen_llm_api_key: str
    # --- RAG 检索配置 ---
    chunk_size: int = 512 # 文档分块大小
    chunk_overlap: int = 50 # 文档分块重叠大小
    hierarchical_chunk_sizes: str = "2048,512,128"
    top_k: int = 5 # 检索 TopK 片段数量
    auto_merging_enabled: bool = True
    auto_merging_threshold: float = 0.5
    auto_merging_max_chars: int = 4000
    rerank_enabled: bool = True
    rerank_min_candidates: int = 6
    rerank_timeout_seconds: float = 5.0
    bm25_state_path: str = "data/index/bm25_state.json"
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    retrieval_cache_enabled: bool = True
    retrieval_cache_ttl: int = 60 * 60 * 6

    # --- Redis 配置 ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 1
    redis_password: str = ""
    redis_max_connections: int = 20

    # --- PostgreSQL parent chunk store ---
    postgres_url: str = "postgresql+psycopg://postgres:123456@localhost:5432/weiquiz"
    parent_store_enabled: bool = True

    # --- OCR settings ---
    ocr_engine: str = "paddleocr"
    ocr_strategy: str = "ocr_only"
    ocr_languages: str = "chi_sim,eng"
    ocr_tesseract_languages: str = "chi_sim+eng"
    ocr_infer_table_structure: bool = False
    ocr_max_pages: int = 3
    ocr_output_dir: str = "data/audit/ocr"
    ocr_paddle_lang: str = "ch"
    ocr_paddle_det_model: str = "PP-OCRv5_mobile_det"
    ocr_paddle_rec_model: str = "PP-OCRv5_mobile_rec"
    ocr_pdf_dpi: int = 100

    # --- 各缓存场景 TTL（单位：秒）---
    chat_msg_ttl: int = 60 * 60 * 24 * 7   # 会话消息：7 天
    session_list_ttl: int = 60 * 60 * 24   # 会话列表：1 天
    chunk_doc_ttl: int = 60 * 60 * 24      # 父文档分块：1 天

    # --- Auth / JWT 配置 ---
    jwt_secret_key: str = "change-me-in-production-use-a-random-string"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 120
    admin_invite_code: str = ""

    # --- 密码哈希配置 ---
    password_hash_algorithm: str = "pbkdf2_sha256"
    password_hash_iterations: int = 310000

    # --- Bootstrap Admin ---
    auth_bootstrap_admin_enabled: bool = False
    auth_bootstrap_admin_username: str = ""
    auth_bootstrap_admin_password: str = ""

    # --- Long-term memory / Mem0 ---
    mem0_enabled: bool = False
    mem0_mode: str = "platform"
    mem0_api_key: str = ""
    mem0_search_limit: int = 5
    mem0_async_add: bool = True

    # --- Tools / Web Search ---
    web_search_enabled: bool = False
    web_search_provider: str = "mcp"
    web_search_top_k: int = 5

# 创建一个 Settings 实例，供全局使用
settings = Settings()

if __name__ == "__main__":
    # 示例：打印当前配置
    # 确保 .env 文件中的 LLM_API_KEY 能够被加载
    load_dotenv() # 加载 .env 文件

    print("--- Current Project Settings ---")
    print(f"Project Name: {settings.project_name}")
    print(f"Docs Directory: {settings.docs_dir}")
    print(f"Index Directory: {settings.index_dir}")
    print(f"LLM Model: {settings.llm_model}")
    print(f"Embedding Model: {settings.embedding_model}")
    print(f"LLM API Base: {settings.llm_api_base}")
    # 为了避免泄露 API Key，只打印部分，或者在生产环境中完全不打印
    print(f"LLM API Key (masked): {settings.llm_api_key[:4]}...{settings.llm_api_key[-4:]}" if settings.llm_api_key else "LLM API Key: Not set")
    print(f"Chunk Size: {settings.chunk_size}")
    print(f"Chunk Overlap: {settings.chunk_overlap}")
    print(f"Top K: {settings.top_k}")
