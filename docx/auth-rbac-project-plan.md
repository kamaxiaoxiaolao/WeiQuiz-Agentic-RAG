# WeiQuiz 账号体系与权限体系项目计划

## 1. 背景与目标

当前 WeiQuiz / SuperMew 的会话体系仍以匿名 `session_id` 为核心，聊天记忆保存在 Redis 中，接口缺少稳定的用户身份识别、资源归属校验和角色权限控制。短期记忆可以暂时依赖 `session_id`，但 Session Summary、长期记忆、Mem0 风格用户记忆都必须建立在 `user_id` 隔离之上。

本期目标是在现有 FastAPI + Redis + PostgreSQL 架构上，先落地一套可用、主流、可演进的账号体系与权限体系，为后续记忆系统和企业级 RAG 能力打身份基础。

本期必须完成：

- `POST /auth/register`：注册普通用户。
- `POST /auth/login`：用户名密码登录，返回 JWT Access Token。
- `GET /auth/me`：获取当前登录用户。
- Bearer Token + JWT 鉴权。
- RBAC 角色：`admin` / `user`。
- `/chat/stream` 必须登录后访问。
- 会话资源必须绑定 `owner_user_id`。
- 普通用户只能查询、读取、删除自己的会话。
- 文档管理接口仅 `admin` 可用。

本期不追求复杂 IAM，而是先实现一版安全边界清晰的 MVP。后续再扩展 Refresh Token、审计、多租户、文档 ACL、SSO 和组织权限。

## 2. 设计原则

- 认证和业务逻辑解耦：认证层回答“你是谁”，权限层回答“你能做什么”。
- 默认最小权限：匿名用户不能访问聊天、会话和文档管理接口。
- 数据隔离优先于 UI 隔离：后端必须校验 `user_id` 和 `role`，不能只靠前端隐藏按钮。
- 显式资源归属：会话、消息、长期记忆、文档操作都要能追溯到用户。
- JWT 只存最小声明：不要把邮箱、昵称、复杂权限列表放入 token。
- 数据库状态优先：JWT 中的 role 可作为快速信息，但最终以数据库中的用户状态和角色为准。
- 先单体、后企业化：当前先做简单 RBAC，为后续租户、组织、SSO 留扩展点。

## 3. 当前系统现状

当前已有能力：

- 聊天入口已经收敛到 `POST /chat/stream`。
- 会话接口：`GET /sessions`、`DELETE /sessions/{session_id}`、`GET /sessions/{session_id}/messages`。
- 文档接口：`POST /documents/upload`，后续需要补 `GET /documents` 与 `DELETE /documents/{doc_id}`。
- Redis 保存 `ChatMemoryBuffer` 短期记忆。
- PostgreSQL 已用于部分存储，但账号和会话归属还未完整接入。

当前风险：

- `session_id` 由客户端传入，可能被猜测或抢占。
- Redis key 只基于 `session_id`，没有用户维度。
- 会话列表、读取、删除缺少 owner 校验。
- 文档管理接口缺少管理员权限边界。
- 后续长期记忆无法安全按用户隔离。

## 4. 本期范围

### 4.1 本期做

- 用户注册、登录、当前用户查询。
- 密码哈希与校验。
- JWT Access Token 签发与解析。
- `get_current_user()`、`get_current_active_user()`、`require_admin()`。
- `/chat/stream` 接入登录态。
- `MemoryService` key 改为 `user_id + session_id`。
- `chat_sessions` 表保存会话归属。
- `/sessions*` 接口按 `owner_user_id` 隔离。
- 文档管理接口接入 `admin` 权限。
- 首个管理员初始化方案。
- 认证、权限、跨用户隔离测试。

### 4.2 本期不做

- Refresh Token。
- 服务端主动 logout / token 黑名单。
- 多端会话管理。
- SSO / OAuth2 Provider 对接。
- 多租户 `tenant_id`。
- 组织、部门、用户组。
- 文档级 ACL。
- 细粒度 ABAC。

这些能力放到后续企业级权限阶段。

## 5. 总体架构

```text
Client
  -> POST /auth/register
  -> POST /auth/login
  -> access_token
  -> Authorization: Bearer <token>
  -> get_current_user()
  -> load user from database
  -> check user.status
  -> require role if needed
  -> business API filters by owner_user_id / role
```

核心模块：

```text
app/
  auth/
    __init__.py
    schemas.py          请求/响应模型
    security.py         密码哈希、JWT 编解码
    service.py          注册、登录、当前用户逻辑
    dependencies.py     get_current_user / require_admin
    repository.py       users / chat_sessions 数据访问
  storage/
    auth_models.py      User / ChatSession ORM 模型
```

## 6. 认证方式

### 6.1 Token 类型

- 使用 HTTP Bearer Token。
- 第一版只做 JWT Access Token。
- Token 放在请求头：

```text
Authorization: Bearer <token>
```

### 6.2 JWT Claims

```json
{
  "sub": "usr_xxx",
  "user_id": "usr_xxx",
  "role": "user",
  "jti": "uuid",
  "iat": 1747562400,
  "exp": 1747569600
}
```

说明：

- `sub` 使用用户 ID。
- `user_id` 便于业务读取。
- `role` 只作为辅助信息，最终权限以数据库 `users.role` 为准。
- `jti` 为后续黑名单或风控预留。
- 不放邮箱、昵称、复杂权限集合。

### 6.3 过期策略

- Access Token 默认 2 小时。
- 第一版不做 Refresh Token。
- 第一版退出登录由前端删除 token；服务端无法立即使已签发 token 失效。
- 如果用户被禁用，`get_current_user()` 必须查数据库并拒绝访问。

## 7. 密码与账号安全

### 7.1 密码哈希

第一版定为：

```text
PBKDF2-SHA256
```

原因：

- Python 标准库可实现，依赖少。
- Windows 本地环境兼容性好。
- 对当前 MVP 足够稳定。

后续可以升级：

```text
Argon2id > bcrypt > PBKDF2-SHA256
```

密码存储格式建议包含算法和参数，便于后续迁移：

```text
pbkdf2_sha256$310000$salt$hash
```

### 7.2 登录安全

- 登录失败统一返回“用户名或密码错误”。
- 不区分用户不存在和密码错误。
- 登录成功更新 `last_login_at`。
- 预留登录失败限流能力。
- 禁用用户不能登录，也不能通过旧 token 继续访问。

### 7.3 管理员初始化

不开放公开管理员注册。

第一版采用环境变量 bootstrap 首个管理员：

```text
AUTH_BOOTSTRAP_ADMIN_ENABLED=false
AUTH_BOOTSTRAP_ADMIN_USERNAME=
AUTH_BOOTSTRAP_ADMIN_PASSWORD=
```

规则：

- 仅当 `users` 表为空时允许创建 bootstrap admin。
- 如果已有用户，启动时不再创建管理员。
- 不重复覆盖已有管理员密码。
- 生产环境必须关闭默认 bootstrap 或使用强密码。

## 8. 权限模型

本期使用简单 RBAC：

```text
admin
user
```

权限矩阵：

| 能力 | anonymous | user | admin |
| --- | --- | --- | --- |
| 注册 | yes | no | no |
| 登录 | yes | yes | yes |
| `/auth/me` | no | yes | yes |
| `/chat/stream` | no | yes | yes |
| 查看自己的会话列表 | no | yes | yes |
| 查看自己的会话消息 | no | yes | yes |
| 删除自己的会话 | no | yes | yes |
| 上传文档 | no | no | yes |
| 查询文档列表 | no | no | yes |
| 删除文档 | no | no | yes |

说明：

- admin 本期不默认查看所有用户会话，避免权限边界过宽。
- 如果后续需要管理员审计用户会话，应单独设计审计接口和审计日志。

## 9. API 设计

### 9.1 `POST /auth/register`

用途：注册普通用户。

请求：

```json
{
  "username": "alice",
  "password": "StrongPassword123!",
  "display_name": "Alice",
  "email": "alice@example.com"
}
```

响应：

```json
{
  "id": "usr_xxx",
  "username": "alice",
  "display_name": "Alice",
  "email": "alice@example.com",
  "role": "user",
  "status": "active",
  "created_at": "2026-05-18T10:00:00Z"
}
```

约束：

- `username` 唯一。
- `email` 可选，提供时唯一。
- 默认角色为 `user`。
- 注册接口不能创建 `admin`。

### 9.2 `POST /auth/login`

用途：用户名密码登录。

请求：

```json
{
  "username": "alice",
  "password": "StrongPassword123!"
}
```

响应：

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 7200,
  "user": {
    "id": "usr_xxx",
    "username": "alice",
    "display_name": "Alice",
    "role": "user"
  }
}
```

### 9.3 `GET /auth/me`

请求头：

```text
Authorization: Bearer <jwt>
```

响应：

```json
{
  "id": "usr_xxx",
  "username": "alice",
  "display_name": "Alice",
  "email": "alice@example.com",
  "role": "user",
  "status": "active",
  "created_at": "2026-05-18T10:00:00Z"
}
```

### 9.4 错误响应格式

统一错误格式建议：

```json
{
  "error": {
    "code": "AUTH_INVALID_CREDENTIALS",
    "message": "用户名或密码错误"
  }
}
```

常见状态码：

- `401`：未登录、token 缺失、token 无效、token 过期。
- `403`：已登录但无权限。
- `409`：用户名或邮箱冲突。
- `422`：参数校验失败。

## 10. 数据模型

### 10.1 `users`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | varchar(64) | 用户 ID |
| `username` | varchar(64) | 唯一用户名 |
| `email` | varchar(128) | 唯一邮箱，可空 |
| `display_name` | varchar(128) | 展示名 |
| `password_hash` | text | 密码哈希 |
| `role` | varchar(32) | `admin` / `user` |
| `status` | varchar(32) | `active` / `disabled` |
| `created_at` | timestamptz | 创建时间 |
| `updated_at` | timestamptz | 更新时间 |
| `last_login_at` | timestamptz | 最后登录时间 |

约束：

- `username` 唯一索引。
- `email` 唯一索引。
- `role IN ('admin', 'user')`。
- `status IN ('active', 'disabled')`。

### 10.2 `chat_sessions`

用途：建立 `session_id` 与 `owner_user_id` 的强关联。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` | varchar(64) | 会话 ID |
| `owner_user_id` | varchar(64) | 所属用户 |
| `title` | varchar(255) | 会话标题 |
| `status` | varchar(32) | `active` / `deleted` |
| `created_at` | timestamptz | 创建时间 |
| `updated_at` | timestamptz | 更新时间 |
| `last_message_at` | timestamptz | 最后活跃时间 |

约束：

- `session_id` 主键或唯一索引。
- `owner_user_id` 建索引。
- `status` 建检查约束。

### 10.3 后续可选：`audit_logs`

本期可先预留模型，后续再启用。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigserial | 主键 |
| `user_id` | varchar(64) | 操作用户 |
| `action` | varchar(64) | 动作 |
| `resource_type` | varchar(64) | 资源类型 |
| `resource_id` | varchar(128) | 资源 ID |
| `result` | varchar(32) | success / denied / failed |
| `detail_json` | jsonb | 扩展信息 |
| `created_at` | timestamptz | 时间 |

## 11. 会话与记忆隔离

这是本期最关键的改造点。

### 11.1 第一阶段兼容策略

保留客户端传入 `session_id`，但不再无条件信任。

规则：

```text
POST /chat/stream
  -> current_user
  -> request.session_id
  -> if chat_sessions not exists:
       create session with owner_user_id=current_user.id
     else:
       check owner_user_id == current_user.id
       if not, return 403
  -> MemoryService.load(user_id, session_id)
```

### 11.2 Redis key 调整

当前：

```text
rag:chat:memory:{session_id}
rag:chat:sessions
```

改为：

```text
rag:chat:memory:{user_id}:{session_id}
rag:chat:sessions:{user_id}
rag:chat:summary:{user_id}:{session_id}
```

说明：

- `summary` key 是为下一阶段 Session Summary 压缩预留。
- 任何读取、保存、删除 memory 都必须带 `user_id`。

### 11.3 会话接口约束

- `GET /sessions`：只返回当前用户的 active sessions。
- `GET /sessions/{session_id}/messages`：先校验 owner，再返回消息。
- `DELETE /sessions/{session_id}`：先校验 owner，再删除 Redis memory，并将 session 标记为 deleted。

### 11.4 第二阶段改进

后续由服务端生成 `session_id`：

```text
POST /sessions
  -> create session
  -> return session_id
```

客户端不再自行决定会话主键。

## 12. 文档权限

### 12.1 本期接口

- `POST /documents/upload`
- `GET /documents`
- `DELETE /documents/{doc_id}`

### 12.2 权限

- 全部要求 `admin`。
- 普通用户访问返回 `403`。
- 匿名用户访问返回 `401`。
- 上传和删除建议写入审计日志，审计日志可在后续阶段启用。

### 12.3 后续扩展

如果未来支持用户私有知识库，需要为文档增加：

```text
owner_user_id
visibility: global / private
```

当前先不做，避免影响全局知识库 RAG。

## 13. 前端改造

需要改造：

- 登录页。
- 注册页。
- 登录状态保存 access_token。
- 请求统一带 `Authorization: Bearer <token>`。
- 顶部展示当前用户。
- 退出登录按钮：第一版只清理本地 token。
- admin 显示文档管理入口。
- user 只显示聊天和自己的会话列表。

注意：前端隐藏按钮只是体验优化，不能作为权限依据。

## 14. 实现步骤

### Step 1：用户模型与数据库

- 新增 `User` ORM。
- 新增 `ChatSession` ORM。
- 初始化建表或迁移。
- 补 settings：JWT、密码、bootstrap admin 配置。

### Step 2：安全工具

- 实现 `hash_password()`。
- 实现 `verify_password()`。
- 实现 `create_access_token()`。
- 实现 `decode_access_token()`。

### Step 3：认证接口

- 实现 `/auth/register`。
- 实现 `/auth/login`。
- 实现 `/auth/me`。
- 登录成功更新 `last_login_at`。

### Step 4：鉴权依赖

- 实现 `get_current_user()`。
- 实现 `get_current_active_user()`。
- 实现 `require_admin()`。
- 保证每次请求都会查数据库确认用户状态。

### Step 5：保护 `/chat/stream`

- `/chat/stream` 增加 `current_user`。
- 首次使用 `session_id` 时绑定当前用户。
- 发现 session 属于其他用户时返回 403。
- `MemoryService` 改为 `user_id + session_id` key。

### Step 6：保护会话接口

- `/sessions` 按 `owner_user_id` 过滤。
- `/sessions/{session_id}/messages` 校验 owner。
- `DELETE /sessions/{session_id}` 校验 owner。

### Step 7：保护文档接口

- `/documents/upload` 要求 `admin`。
- `GET /documents` 要求 `admin`。
- `DELETE /documents/{doc_id}` 要求 `admin`。

### Step 8：测试与加固

- 认证测试。
- 权限测试。
- 跨用户 session 隔离测试。
- admin 文档权限测试。
- disabled 用户访问测试。
- `.env.example` 补齐配置。

## 15. 测试计划

### 15.1 认证测试

- 注册成功。
- 重复用户名返回 409。
- 重复邮箱返回 409。
- 登录成功返回 token。
- 错误密码返回 401。
- disabled 用户无法登录。
- `/auth/me` 无 token 返回 401。
- `/auth/me` 有效 token 返回当前用户。

### 15.2 权限测试

- 匿名访问 `/chat/stream` 返回 401。
- user 访问 `/chat/stream` 成功。
- user 访问文档上传返回 403。
- admin 访问文档上传成功。
- user 访问 `GET /documents` 返回 403。
- admin 访问 `GET /documents` 成功。

### 15.3 会话隔离测试

- A 用户首次使用 session_id，会话绑定 A。
- A 用户能读取自己的 session。
- B 用户使用 A 的 session_id 返回 403。
- B 用户不能读取 A 的 messages。
- B 用户不能删除 A 的 session。
- 删除 session 后 Redis memory 被删除或失效。

### 15.4 JWT 测试

- token 过期返回 401。
- token 签名错误返回 401。
- token 中 role 与数据库 role 不一致时，以数据库为准。
- 用户 disabled 后，旧 token 不能继续访问。

## 16. 验收标准

本期完成标准：

1. `/auth/register`、`/auth/login`、`/auth/me` 可用。
2. `/chat/stream` 必须登录访问。
3. `MemoryService` 使用 `user_id + session_id` 隔离 key。
4. 会话首次使用会绑定当前用户。
5. 其他用户不能读取或删除不属于自己的会话。
6. 文档上传、列表、删除仅 `admin` 可访问。
7. JWT 每次解析后会查数据库用户状态。
8. disabled 用户无法继续访问受保护接口。
9. 关键认证、权限、隔离测试通过。

## 17. 风险与处理

| 风险 | 处理 |
| --- | --- |
| 客户端伪造或猜测 `session_id` | `chat_sessions.owner_user_id` 校验，不属于自己返回 403 |
| Redis key 串用户 | key 加 `user_id` 前缀 |
| JWT 角色过期或不一致 | 每次查数据库，以 DB role/status 为准 |
| 用户禁用后旧 token 仍有效 | `get_current_user()` 查 DB status |
| 注册接口创建 admin | 禁止，admin 只能 bootstrap 或脚本创建 |
| 前端绕过按钮限制 | 后端 RBAC 强制校验 |
| 第一版无法 logout 立即失效 | 明确限制，后续做 token 黑名单或 refresh token |

## 18. 后续演进

完成本期后，建议顺序：

1. Session Summary 记忆压缩，基于 `user_id + session_id`。
2. Refresh Token 与服务端注销。
3. 审计日志。
4. 管理员创建/禁用用户。
5. 多租户 `tenant_id`。
6. 文档级 ACL。
7. SSO / OAuth2 / 企业身份源。
8. Mem0 风格长期用户记忆，按 `user_id` namespace 隔离。

## 19. 参考依据

- FastAPI Security：OAuth2 with Password and Bearer with JWT。
- OWASP Password Storage Cheat Sheet。
- OWASP JWT Cheat Sheet。
- RFC 7519 JSON Web Token。
