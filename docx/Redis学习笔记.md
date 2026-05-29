# Redis 学习笔记

> 按照项目实践路径整理，结合 WeiQuiz RAG 项目代码，边学边用。

---

## Step 1：Redis 基础

### 什么是 Redis？

Redis (Remote Dictionary Server) 是一个开源的、**基于内存**的数据结构存储系统，可用作数据库、缓存、消息队列。

**核心特点：**
- 纯内存操作，读写速度极快（10万+ QPS）
- 支持多种数据结构：String、Hash、List、Set、ZSet
- 支持 TTL（过期时间），数据到期自动清除
- 单线程处理命令（6.0+ 网络 I/O 多线程，命令仍单线程）
- 持久化：RDB（快照）和 AOF（追加日志）两种方式

### 安装与启动

```bash
# Windows：下载 Redis for Windows 或用 WSL
# WSL / Linux
sudo apt install redis-server
redis-server               # 启动
redis-cli ping             # 验证连接（返回 PONG）
redis-cli                  # 进入交互模式
```

### 基础命令

```bash
# String
SET name "hello"           # 设置
SET name "hello" EX 60     # 设置并在 60 秒后过期
GET name                   # 获取
DEL name                   # 删除
EXISTS name                # 判断是否存在（0/1）
TTL name                   # 查看剩余存活秒数（-1=永不过期，-2=不存在）
EXPIRE name 120            # 重新设置 TTL

# 批量操作
MSET k1 v1 k2 v2           # 批量设置
MGET k1 k2                 # 批量获取
```

### Python 中的连接池

```python
import redis

pool = redis.ConnectionPool(
    host="localhost",
    port=6379,
    db=0,
    decode_responses=True,   # 自动解码 bytes → str
    max_connections=20,
)
r = redis.Redis(connection_pool=pool)
r.set("key", "value", ex=3600)
print(r.get("key"))          # "value"
```

---

## Step 2：Hash 数据结构

### 适用场景
一个 key 对应多个字段（类似 Python dict），适合存储"对象"：用户信息、会话摘要、配置项。

### 常用命令

```bash
HSET user:1 name "张三" age 25 email "zs@example.com"
HGET user:1 name              # 获取单个字段
HGETALL user:1                # 获取所有字段
HDEL user:1 email             # 删除字段
HLEN user:1                   # 字段数量
HEXISTS user:1 name           # 字段是否存在
HSET user:1 name "李四"       # 修改字段（单字段更新，不影响其他字段）
EXPIRE user:1 86400           # 为整个 Hash 设置 TTL
```

### String vs Hash 存对象

| | String (JSON) | Hash |
|---|---|---|
| 存储方式 | 整体序列化为字符串 | 字段独立存储 |
| 读写粒度 | 全量读写 | 可单字段读写 |
| 适用场景 | 整体读写、复杂嵌套对象 | 频繁部分更新 |
| 内存占用 | 较小（无字段名开销） | 字段较多时略大 |

---

## Step 3：List 数据结构

### 适用场景
双端链表，适合消息队列、最新 N 条记录、活动日志。

### 常用命令

```bash
RPUSH msgs '{"role":"user","content":"hello"}'   # 从右侧追加
LPUSH msgs "first"                                # 从左侧插入
LRANGE msgs 0 -1                                  # 获取全部（0到最后）
LRANGE msgs 0 9                                   # 获取前10条
LLEN msgs                                         # 列表长度
LTRIM msgs -100 -1                                # 只保留最后100条（防止无限增长）
```

### 防止内存无限增长

每次追加消息后执行 `LTRIM`，只保留最新的 N 条：

```python
pipe = r.pipeline()
pipe.rpush(key, json.dumps(new_msg))
pipe.ltrim(key, -200, -1)   # 只保留最后200条
pipe.execute()
```

---

## Step 4：缓存模式与企业级设计

### TTL（Time To Live）设置原则

- 会话消息：7 天（用户可能隔几天继续对话）
- 会话列表：1 天（访问频繁，可接受短暂重建）
- 文档分块：1 天（文档变更时主动失效）
- 热点数据：TTL 加随机偏移（防止集中过期）

```python
import random
ttl = 86400 + random.randint(-3600, 3600)   # 24小时 ± 1小时
```

### Cache-Aside（旁路缓存）模式

最常用的缓存模式，逻辑由应用层控制：

**读流程：**
```
请求 → 查 Redis → 命中 → 返回
                → 未命中 → 查数据库 → 写入 Redis（设 TTL）→ 返回
```

**写流程：**
```
请求 → 写数据库 → 删除 Redis 对应 key → 返回
```

> 为什么先写库再删缓存，而不是先删缓存再写库？
> 先删缓存的问题：删除后，另一个线程读缓存未命中，加载旧数据写入缓存，之后写库完成 → 缓存中是旧数据。
> 先写库的顺序更安全，极短的不一致窗口可由 TTL 兜底。

### 缓存三大问题

| 问题 | 描述 | 解决方案 |
|---|---|---|
| **缓存穿透** | 查询不存在的 key，每次都打到数据库 | ① 缓存空值（TTL 设短）② 布隆过滤器 |
| **缓存击穿** | 热点 key 恰好过期，大量请求同时涌入数据库 | ① SETNX 互斥锁 ② 逻辑过期（异步刷新） |
| **缓存雪崩** | 大量 key 同时过期，数据库压力激增 | ① TTL 加随机偏移 ② 多级缓存 ③ 熔断降级 |

### Pipeline（批量操作）

```python
pipe = r.pipeline()
for chunk_id in chunk_ids:
    pipe.delete(f"wq:doc:chunk:{chunk_id}")
pipe.execute()   # 一次 RTT，发送所有命令
```

普通单次 DEL 每次需要一次网络往返，Pipeline 将 N 条命令打包一次发送，适合批量操作。

---

## 项目中的企业级缓存设计

### Key 命名规范

```
wq:chat:msg:{user_id}:{session_id}   → 会话消息列表（List）
wq:chat:sessions:{user_id}           → 用户会话摘要（Hash）
wq:doc:chunk:{chunk_id}              → 父文档分块（String/JSON）
```

### 缓存架构图

```
Streamlit UI
    ↓ user_id + session_id + message
FastAPI /chat
    ├── 读：get_chat_messages(user_id, session_id)
    │       命中 → 跳过重建，直接用缓存消息
    │       未命中 → 用内存 memory_buffer
    │
    ├── 写：chat_engine.chat(message)
    │       完成后 set_chat_messages(...) 写入 Redis List
    │            set_session_entry(...) 更新 Hash 摘要
    │
    └── 失效：DELETE /sessions/{user_id}/{session_id}
                invalidate_chat_messages(...)
                invalidate_session_list(...)

RAG 检索
    └── build_rag_components(redis_client)
            → _cache_all_nodes() 写入 wq:doc:chunk:{id}
            → 灌库后 invalidate_chunk_docs_batch() 批量失效
```

### 降级设计

缓存层不是核心依赖，Redis 不可用时接口仍正常工作：

```python
def _get_redis(request):
    pool = getattr(request.app.state, "redis_pool", None)
    if pool is None:
        return None   # 返回 None，调用方检查后降级
    try:
        return redis.Redis(connection_pool=pool)
    except Exception:
        return None
```

---

## 面试高频题汇总

1. **Redis 为什么这么快？** → 内存 + 单线程 + I/O 多路复用 + 高效数据结构
2. **单线程为什么不会成为瓶颈？** → 瓶颈在网络/内存带宽，CPU 不是瓶颈；避免了锁竞争和上下文切换
3. **Redis 数据类型和适用场景？** → String/Hash/List/Set/ZSet，各自对应不同业务模型
4. **Hash 和 String 存 JSON 的区别？** → Hash 支持单字段更新，String 必须全量读写
5. **为什么用连接池？** → 避免频繁 TCP 握手，控制并发连接数上限
6. **List 做消息队列的缺点？** → 无 ACK、无消费组、无持久化进度；复杂场景用 Stream
7. **Cache-Aside 为什么先写库再删缓存？** → 先删缓存会有脏数据窗口，先写库更安全
8. **缓存穿透/击穿/雪崩？** → 三大问题及对应解决方案（见上表）
9. **Pipeline vs MULTI/EXEC？** → Pipeline 是批量发送优化（非原子），事务保证原子性但不支持回滚
10. **如何在 FastAPI 中管理 Redis 连接？** → lifespan 初始化连接池，挂载到 app.state，接口复用
