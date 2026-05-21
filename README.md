# Dify Portal

基于 FastAPI + Jinja2 的 Dify 应用门户系统，支持多应用管理、用户权限控制、流式对话、文件上传、语音功能、记忆系统、瞬时回复和企业微信集成。

## 功能特性

### 核心功能
- **多应用管理** - 集中管理多个 Dify 应用，支持应用可见性配置
- **用户系统** - 用户名密码登录，支持管理员和普通用户权限分级
- **流式对话** - SSE 实时流式输出，打字机效果，支持中断和重新生成
- **文件上传** - 多文件上传、图片预览、多模态对话支持
- **语音功能** - 语音转文字（STT）、文字转语音（TTS）
- **消息反馈** - 点赞/点踩、复制、重新生成

### 高级功能
- **记忆系统** - 支持对话记忆功能，让 AI 记住上下文和用户偏好
- **瞬时回复** - 快速响应模式，减少等待时间
- **思考模式** - 展示 AI 的推理过程（Thinking Mode）
- **权限管理** - 应用可见性（所有人/指定用户）、用户搜索、批量授权
- **管理后台** - 应用管理、用户管理、使用统计、系统设置

### 企业微信集成
- **企微 OAuth 免登** - 企业微信内免密码登录
- **企微扫码登录** - 支持企业微信扫码登录
- **企微 Bot** - 企业微信机器人自动回复
- **用户同步** - 自动同步企微组织架构

## 技术栈

- **后端**: FastAPI + SQLite + Jinja2 模板
- **前端**: 原生 JavaScript + Tailwind CSS
- **部署**: Docker Compose
- **依赖**: httpx, python-multipart, aiofiles

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/felixtowh/felix.git
cd felix
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# 企业微信配置（可选，不需要企微登录可留空）
WECHAT_CORP_ID=your-corp-id
WECHAT_AGENT_ID=your-agent-id
WECHAT_SECRET=your-secret

# Portal 公网访问地址（用于企微 OAuth 回调）
PORTAL_URL=https://your-domain.com
```

### 3. 配置 Dify 应用

在管理后台添加应用时填写：
- 应用名称
- API Key（从 Dify 后台获取）
- Dify 服务器地址（如 `https://your-dify-server.com`）
- 可选：欢迎语、Logo、记忆/瞬时回复/思考模式开关

### 4. 启动服务

```bash
docker compose up -d
```

访问 http://localhost:8088

## 默认账号

| 用户名 | 密码 | 权限 |
|--------|------|------|
| admin | admin001 | 管理员 |

> **安全提示**: 首次登录后请立即修改默认密码。

## 功能详解

### 记忆系统
- 在应用配置中开启"启用记忆"
- AI 会自动记住对话历史中的关键信息
- 支持记忆的查看、编辑和删除
- 适用于需要长期记忆的场景（如客服、个人助理）

### 瞬时回复
- 在应用配置中开启"瞬时回复"
- 针对常见问题提供预置快速回复
- 减少用户等待时间，提升体验

### 思考模式
- 在应用配置中开启"思考模式"
- 展示 AI 的推理过程（Chain of Thought）
- 适用于复杂问题解答、教学场景

### 企微 Bot
- 配置企微应用后，自动创建 Bot
- 支持群聊和单聊自动回复
- 可配置多个 Dify 应用对应不同 Bot

## 环境变量说明

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `WECHAT_CORP_ID` | 否 | 企业微信 CorpID |
| `WECHAT_AGENT_ID` | 否 | 企业微信 AgentID |
| `WECHAT_SECRET` | 否 | 企业微信 Secret |
| `PORTAL_URL` | 否 | Portal 公网地址，用于企微回调 |

## 目录结构

```
.
├── docker-compose.yml    # Docker 部署配置
├── Dockerfile            # 容器构建文件
├── requirements.txt      # Python 依赖
├── app/                  # 后端代码
│   ├── main.py          # FastAPI 主应用
│   ├── database.py      # SQLite 数据库操作
│   ├── dify_client.py   # Dify API 客户端
│   ├── wechat_auth.py   # 企微认证（可选）
│   ├── wecom_bot.py     # 企微 Bot
│   ├── bot_manager.py   # Bot 管理器
│   └── bot_worker.py    # Bot 工作线程
├── templates/           # Jinja2 模板
│   ├── login.html       # 登录页
│   ├── index.html       # 应用列表
│   ├── chat.html        # 对话页面
│   ├── error.html       # 错误页面
│   └── admin/           # 管理后台
│       ├── index.html   # 仪表盘
│       ├── apps.html    # 应用管理
│       ├── users.html   # 用户管理
│       ├── stats.html   # 使用统计
│       ├── bots.html    # Bot 管理
│       └── settings.html # 系统设置
├── static/              # CSS/JS/图片
│   ├── css/            # 样式文件
│   ├── js/             # 脚本文件
│   └── images/         # 图片资源
└── data/                # SQLite 数据库（运行后生成）
```

## 添加 Dify 应用

1. 登录管理后台（`/admin`）
2. 进入"应用管理"
3. 点击"添加应用"
4. 填写：
   - 应用名称
   - API Key（从 Dify 后台获取）
   - Dify 服务器地址
   - 欢迎语（可选）
   - 功能开关（记忆/瞬时回复/思考模式）
5. 保存后即可在首页看到

## 获取 Dify API Key

1. 登录你的 Dify 控制台
2. 进入目标应用
3. 点击左上角 "API 访问"
4. 复制 "API 密钥"

## 企微集成配置

### 1. 企微后台配置
- 登录企业微信管理后台
- 创建应用，获取 `CorpID`、`AgentID`、`Secret`
- 设置可信域名（你的 Portal 域名）

### 2. Portal 配置
- 在 `.env` 中填写企微参数
- 重启服务
- 在管理后台"系统设置"中完成 OAuth 配置

### 3. 用户同步
- 首次登录会自动同步企微用户信息
- 支持批量导入企微用户

## 常见问题

### Q: 如何修改端口？
编辑 `docker-compose.yml`：

```yaml
ports:
  - "8088:8088"  # 修改左侧数字
```

### Q: 数据库文件在哪？
容器内 `/app/data/portal.db`，宿主机挂载在 `./data/` 目录。

### Q: 如何备份？
备份 `data/` 目录即可：

```bash
cp -r data data-backup-$(date +%Y%m%d)
```

### Q: 企微登录不工作？
确认：
1. `PORTAL_URL` 是公网可访问的 HTTPS 地址
2. 企微后台配置的回调域名正确
3. `WECHAT_CORP_ID`、`WECHAT_AGENT_ID`、`WECHAT_SECRET` 无误

### Q: 记忆功能如何使用？
1. 在应用编辑页面开启"启用记忆"
2. 对话中 AI 会自动提取关键信息
3. 在管理后台"应用详情"中查看和编辑记忆

### Q: 瞬时回复和正常回复的区别？
- **瞬时回复**: 针对常见问题快速响应，适合简单查询
- **正常回复**: 完整调用 Dify API，适合复杂对话

## 开发

```bash
# 本地运行（需 Python 3.9+）
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8088
```

## 安全建议

1. **修改默认密码** - 首次登录后立即修改
2. **使用 HTTPS** - 生产环境务必配置 SSL
3. **定期备份** - 备份 `data/` 目录
4. **限制访问** - 使用防火墙限制端口访问
5. **保护 API Key** - 不要在客户端暴露 Dify API Key

## 截图

截图待补充，欢迎提交 PR。

## 贡献

欢迎提交 Issue 和 PR。

## License

All Rights Reserved

Copyright (c) 2026 felixtowh

未经版权持有人事先书面许可，任何人不得以任何形式或方式使用、复制、修改、合并、
发布、分发、再许可或销售本软件的任何部分。
