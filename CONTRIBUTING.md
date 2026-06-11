# 贡献指南

感谢你关注 WeiQuiz。

## 本地开发

后端：

```bash
cp .env.example .env
docker compose up -d redis postgres
uv sync
uv run uvicorn app.api:app --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## 提交 Pull Request 前

- 保持改动聚焦，避免把无关重构混在一起。
- 行为变化需要补充或更新测试。
- 不要提交 `.env`、本地文档、索引文件、生成数据或 API Key。
- 后端改动请运行相关测试，例如 `uv run pytest`。
- 前端改动请在 `frontend/` 下运行 `npm run build`。

## 代码风格

- 遵循现有模块边界，优先复用项目已有抽象。
- API、检索结果和 metadata 尽量使用明确的数据结构，不要随意传递松散字典。
- RAG Workflow 逻辑应尽量可测试，不强依赖外部服务。
- 注释只解释不明显的设计决策，避免重复描述代码本身。

## 文档规范

- 用户可见的安装、启动、功能变化，请更新 `README.md`。
- 深入设计、排障和实现细节放到 `docs/`。
- 示例配置请同步更新 `.env.example`。

## 安全要求

- 不要提交真实密钥、Token、内部文档或私有数据。
- 涉及 SQL、工具调用、权限、文件路径处理的改动，需要额外说明安全边界。
- 如果发现安全问题，请先私下联系维护者，不要直接公开漏洞细节。
