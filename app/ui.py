import streamlit as st
import requests
import json
from uuid import uuid4

# --- 页面配置 ---
st.set_page_config(
    page_title="WeiQuiz - 本地知识库问答",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 自定义 CSS 美化 ---
def inject_custom_css():
    st.markdown("""
        <style>
        /* 隐藏 Streamlit 默认的 Header 和 Footer，让应用更沉浸 */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        
        /* 调整主容器的内边距 */
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
        }
        
        /* 美化引用来源框 */
        .source-box {
            background-color: #f0f2f6;
            border-left: 4px solid #4CAF50;
            padding: 10px 15px;
            margin-bottom: 10px;
            border-radius: 4px;
            font-size: 0.9em;
            color: #31333F;
        }
        
        /* 暗黑模式适配 */
        @media (prefers-color-scheme: dark) {
            .source-box {
                background-color: #262730;
                color: #FAFAFA;
            }
        }
        </style>
    """, unsafe_allow_html=True)

inject_custom_css()

# --- 应用标题 ---
st.title("🤖 WeiQuiz")
st.markdown("##### 你的专属本地知识库问答助手 ✨")
st.divider()

# --- 全局变量 ---
API_BASE_URL = "http://127.0.0.1:8000"

# --- 函数定义 ---
def query_backend(question: str):
    """调用 FastAPI 后端进行单次查询（非流式）"""
    try:
        response = requests.post(
            f"{API_BASE_URL}/query",
            json={"question": question},
            timeout=60
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"调用后端 API 失败: 请检查后端服务是否启动 ({e})")
        return None

def chat_backend_stream(session_id: str, message: str):
    """调用 FastAPI 后端进行流式对话，并具备健壮的 SSE 三段式多事件解析能力"""
    resp = requests.post(
        f"{API_BASE_URL}/chat/stream",
        json={"session_id": session_id, "message": message},
        stream=True,
        timeout=300,
    )
    resp.raise_for_status()

    answer_chunks = []
    end_payload = None
    current_event = "chunk" # 默认当作普通的文本 chunk 事件

    for raw_line in resp.iter_lines(decode_unicode=True):
        if not raw_line:
            continue

        if raw_line.startswith("event:"):
            # 捕获 SSE 事件类型 (例如 event: status, event: chunk 或 event: result)
            current_event = raw_line.split(":", 1)[1].strip()
            continue

        if raw_line.startswith("data:"):
            data = raw_line.split(":", 1)[1].lstrip()

            if data == "[DONE]":
                break

            # 1. 处理系统状态事件 (status)
            if current_event == "status":
                yield ("status", data, None)
                continue

            # 2. 处理最终溯源结果事件 (result)
            # 兼容：有时后端可能没法发 event: result，拦截包含了 source_nodes 的 JSON 字符串
            if current_event == "result" or (data.strip().startswith("{") and '"source_nodes"' in data):
                try:
                    parsed_data = json.loads(data)
                    if "source_nodes" in parsed_data:
                        end_payload = parsed_data
                        continue  # 成功解析为溯源数据，不再作为文字渲染到屏幕上
                except Exception:
                    pass # 解析失败则退化为普通文本

            # 3. 处理普通的文字流事件 (chunk)
            # 对于普通的 chunk 事件，恢复换行符
            text_delta = data.replace("\\n", "\n")
            answer_chunks.append(text_delta)
            yield ("chunk", text_delta, None)

    yield ("end", "".join(answer_chunks), end_payload)

def list_sessions():
    try:
        resp = requests.get(f"{API_BASE_URL}/sessions", timeout=10)
        resp.raise_for_status()
        return resp.json().get("sessions", [])
    except requests.exceptions.RequestException:
        return []

def get_session_messages(session_id: str):
    try:
        resp = requests.get(f"{API_BASE_URL}/sessions/{session_id}/messages", timeout=10)
        resp.raise_for_status()
        return resp.json().get("messages", [])
    except requests.exceptions.RequestException:
        return []

def delete_session(session_id: str):
    try:
        resp = requests.delete(f"{API_BASE_URL}/sessions/{session_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException:
        return {"ok": False}

# --- 初始化状态 ---
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "你好！我是 **WeiQuiz** 助手，很高兴为你服务。请问有什么我可以帮你的吗？"}]
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid4().hex

# --- 侧边栏 ---
with st.sidebar:
    st.header("💬 会话管理")
    st.caption(f"当前 ID: `{st.session_state.session_id[:8]}...`")
    
    # 新建会话按钮
    if st.button("➕ 新建会话", use_container_width=True, type="primary"):
        st.session_state.session_id = uuid4().hex
        st.session_state._selected_session = st.session_state.session_id
        st.session_state.messages = [{"role": "assistant", "content": "你好！我是 **WeiQuiz** 助手，开启了一个新话题，请问有什么可以帮你的吗？"}]
        st.toast("✅ 已创建新会话！")
        st.rerun()

    st.divider()

    sessions = list_sessions()
    
    # 历史会话列表
    if sessions:
        st.subheader("📚 历史记录")
        st.session_state._selected_session = st.radio(
            "选择最近的会话",
            options=sessions,
            index=0 if st.session_state.session_id not in sessions else sessions.index(st.session_state.session_id),
            label_visibility="collapsed"
        )

        if st.session_state._selected_session != st.session_state.session_id:
            st.session_state.session_id = st.session_state._selected_session
            history = get_session_messages(st.session_state.session_id)
            if history:
                st.session_state.messages = history
            else:
                st.session_state.messages = [{"role": "assistant", "content": "该会话暂无历史消息。"}]
            st.rerun()

    st.divider()
    
    # 删除会话按钮
    if st.button("🗑️ 删除当前会话", use_container_width=True):
        sid = st.session_state.session_id
        result = delete_session(sid)
        if result.get("ok"):
            st.session_state.session_id = uuid4().hex
            st.session_state._selected_session = st.session_state.session_id
            st.session_state.messages = [{"role": "assistant", "content": "会话已删除，已为你重新创建新会话。"}]
            st.toast("🗑️ 会话已清理")
            st.rerun()
        else:
            st.error("删除失败，请检查后端服务。")
            
    # 底部信息
    st.markdown("<br>" * 5, unsafe_allow_html=True)
    st.caption("🚀 Powered by FastAPI & Streamlit")

# --- 显示历史消息 ---
for message in st.session_state.messages:
    # 区分头像：用户用人像，AI 用机器人
    avatar = "👤" if message["role"] == "user" else "🤖"
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])
        
        # 修复 Bug: 如果历史消息中包含溯源节点，同样使用美化样式渲染出来
        if "source_nodes" in message and message["source_nodes"]:
            with st.expander("📚 查看引用来源与上下文", expanded=False):
                for i, node in enumerate(message["source_nodes"]):
                    file_name = node.get('file_name', '未知文件')
                    score = node.get('score', 0)
                    text_content = node.get('text', '').strip()
                    
                    st.markdown(f"""
                    <div class="source-box">
                        <strong>来源 [{i+1}]</strong>: <code>{file_name}</code> <br>
                        <em>相关度得分: {score:.4f}</em><br>
                        <blockquote>{text_content}</blockquote>
                    </div>
                    """, unsafe_allow_html=True)

# --- 获取用户输入 ---
if prompt := st.chat_input("请输入你的问题，例如：这份文档的主要观点是什么？"):
    # 1. 将用户输入显示在界面上并添加到聊天记录
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt)

    # 2. 调用后端 API 并获取响应 (流式)
    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("机器人正在准备回答..."):
            status_placeholder = st.empty()  # 状态提示专属占位符
            placeholder = st.empty()         # 正文专属占位符
            
            streamed_text = ""
            source_nodes = []
            has_started_text = False

            try:
                for typ, chunk, payload in chat_backend_stream(st.session_state.session_id, prompt):
                    # 处理系统状态
                    if typ == "status":
                        if not has_started_text:
                            status_placeholder.caption(f"🔄 **系统状态:** {chunk}")
                    
                    # 处理 LLM 输出的文字
                    elif typ == "chunk":
                        if not has_started_text:
                            status_placeholder.empty() # 模型开始输出文字时，清除上方的系统状态提示
                            has_started_text = True
                        streamed_text += chunk
                        placeholder.markdown(streamed_text + "▌") # 增加闪烁光标效果
                    
                    # 处理流结束和溯源数据
                    elif typ == "end":
                        # 结束时移除光标
                        placeholder.markdown(streamed_text)
                        if payload and isinstance(payload, dict):
                            source_nodes = payload.get("source_nodes", []) or []
                            
            except requests.exceptions.RequestException as e:
                st.error(f"⚠️ 流式调用后端失败，请确认服务已启动并重试。 ({e})")

            # 显示溯源信息并美化
            if source_nodes:
                with st.expander("📚 查看引用来源与上下文", expanded=False):
                    for i, node in enumerate(source_nodes):
                        file_name = node.get('file_name', '未知文件')
                        score = node.get('score', 0)
                        text_content = node.get('text', '').strip()
                        
                        # 恢复并使用 HTML/CSS 渲染引用块
                        st.markdown(f"""
                        <div class="source-box">
                            <strong>来源 [{i+1}]</strong>: <code>{file_name}</code> <br>
                            <em>相关度得分: {score:.4f}</em><br>
                            <blockquote>{text_content}</blockquote>
                        </div>
                        """, unsafe_allow_html=True)

            # 3. 将助手的完整回答以及 source_nodes 添加到聊天记录中，解决刷新丢失引用的问题
            st.session_state.messages.append({
                "role": "assistant", 
                "content": streamed_text,
                "source_nodes": source_nodes
            })