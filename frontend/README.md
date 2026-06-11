# WeiQuiz 前端

这是 WeiQuiz Agentic RAG 应用的 Vue 3 前端。

## 技术栈

- Vue 3
- TypeScript
- Vite
- Pinia
- Tailwind CSS
- lucide-vue-next

## 本地开发

```bash
npm install
npm run dev
```

开发时需要后端 FastAPI 服务可访问。生产环境中，前端通常与后端同源部署，或通过反向代理转发 API 请求。

## 构建

```bash
npm run build
```

## 主要视图

- `LoginView.vue`：登录和认证入口。
- `ChatView.vue`：聊天工作台，包含会话列表、知识库面板和调试面板。
- `AdminView.vue`：管理员功能，包括用户管理和审计相关工作流。

## 主要组件

- `ChatPanel.vue`：聊天输入、SSE 解析、流式渲染和引用展示。
- `SessionList.vue`：会话列表、新建会话和切换会话。
- `KnowledgeSidebar.vue`：文档上传、知识库状态和入库任务展示。
- `DebugPanel.vue`：展示路由、Trace、Source Nodes 等调试信息。
- `AppHeader.vue`：顶部导航和用户信息。
