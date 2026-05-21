# Dify Portal

[English](#english) | [中文](#chinese)

---

<a name="english"></a>
## English

A Dify application portal based on FastAPI + Jinja2, supporting multi-app management, user permission control, streaming chat, file upload, voice features, memory system, instant reply, and WeCom integration.

### Features

**Core Features**
- **Multi-App Management** - Centralized management of multiple Dify apps with visibility configuration
- **User System** - Username/password login with admin and user roles
- **Streaming Chat** - SSE real-time streaming with typewriter effect and interrupt support
- **File Upload** - Multi-file upload, image preview, multimodal chat support
- **Voice Features** - Speech-to-Text (STT) and Text-to-Speech (TTS)
- **Message Feedback** - Like/dislike, copy, regenerate

**Advanced Features**
- **Memory System** - AI remembers context and user preferences across conversations
- **Instant Reply** - Fast response mode for common questions
- **Thinking Mode** - Display AI reasoning process (Chain of Thought)
- **Permission Management** - App visibility (public/specific users), user search, batch authorization
- **Admin Dashboard** - App management, user management, usage statistics, system settings

**WeCom Integration**
- **WeCom OAuth** - Password-free login within WeCom
- **WeCom QR Login** - Support WeCom QR code login
- **WeCom Bot** - Auto-reply in group and private chats
- **User Sync** - Automatic sync of WeCom organization structure

### Tech Stack

- **Backend**: FastAPI + SQLite + Jinja2 Templates
- **Frontend**: Vanilla JavaScript + Tailwind CSS
- **Deployment**: Docker Compose
- **Dependencies**: httpx, python-multipart, aiofiles

### Quick Start

```bash
git clone https://github.com/felixtowh/felix.git
cd felix
cp .env.example .env
# Edit .env with your configuration
docker compose up -d
```

Visit http://localhost:8088

### Default Account

| Username | Password | Role |
|----------|----------|------|
| admin | admin001 | Admin |

> **Security Tip**: Please change the default password after first login.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `WECHAT_CORP_ID` | No | WeCom CorpID |
| `WECHAT_AGENT_ID` | No | WeCom AgentID |
| `WECHAT_SECRET` | No | WeCom Secret |
| `PORTAL_URL` | No | Public URL for WeCom callbacks |

### License

Apache License 2.0

Copyright (c) 2026 felixtowh

---

<a name="chinese"></a>
## 中文

基于 FastAPI + Jinja2 的 Dify 应用门户系统，支持多应用管理、用户权限控制、流式对话、文件上传、语音功能、记忆系统、瞬时回复和企业微信集成。

### 功能特性

**核心功能**
- **多应用管理** - 集中管理多个 Dify 应用，支持应用可见性配置
- **用户系统** - 用户名密码登录，支持管理员和普通用户权限分级
- **流式对话** - SSE 实时流式输出，打字机效果，支持中断
- **文件上传** - 多文件上传、图片预览、多模态对话支持
- **语音功能** - 语音转文字（STT）、文字转语音（TTS）
- **消息反馈** - 点赞/点踩、复制、重新生成

**高级功能**
- **记忆系统** - AI 自动记住对话历史中的关键信息
- **瞬时回复** - 针对常见问题提供快速响应
- **思考模式** - 展示 AI 的推理过程
- **权限管理** - 应用可见性（所有人/指定用户）、用户搜索、批量授权
- **管理后台** - 应用管理、用户管理、使用统计、系统设置

**企业微信集成**
- **企微 OAuth 免登** - 企业微信内免密码登录
- **企微扫码登录** - 支持企业微信扫码登录
- **企微 Bot** - 群聊和单聊自动回复
- **用户同步** - 自动同步企微组织架构

### 技术栈

- **后端**: FastAPI + SQLite + Jinja2 模板
- **前端**: 原生 JavaScript + Tailwind CSS
- **部署**: Docker Compose
- **依赖**: httpx, python-multipart, aiofiles

### 快速开始

```bash
git clone https://github.com/felixtowh/felix.git
cd felix
cp .env.example .env
# 编辑 .env 填入配置
docker compose up -d
```

访问 http://localhost:8088

### 默认账号

| 用户名 | 密码 | 权限 |
|--------|------|------|
| admin | admin001 | 管理员 |

> **安全提示**: 首次登录后请立即修改默认密码。

### 环境变量说明

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `WECHAT_CORP_ID` | 否 | 企业微信 CorpID |
| `WECHAT_AGENT_ID` | 否 | 企业微信 AgentID |
| `WECHAT_SECRET` | 否 | 企业微信 Secret |
| `PORTAL_URL` | 否 | Portal 公网地址，用于企微回调 |

### 目录结构

```
.
├── docker-compose.yml    # Docker 部署配置
├── Dockerfile            # 容器构建文件
├── requirements.txt      # Python 依赖
├── app/                  # 后端代码
│   ├── main.py          # FastAPI 主应用
│   ├── database.py      # SQLite 数据库操作
│   ├── dify_client.py   # Dify API 客户端
│   ├── wechat_auth.py   # 企微认证
│   ├── wecom_bot.py     # 企微 Bot
│   ├── bot_manager.py   # Bot 管理器
│   └── bot_worker.py    # Bot 工作线程
├── templates/           # Jinja2 模板
│   ├── login.html       # 登录页
│   ├── index.html       # 应用列表
│   ├── chat.html        # 对话页面
│   └── admin/           # 管理后台
├── static/              # CSS/JS/图片
└── data/                # SQLite 数据库（运行后生成）
```

### 添加 Dify 应用

1. 登录管理后台（`/admin`）
2. 进入"应用管理"
3. 点击"添加应用"
4. 填写应用名称、API Key、Dify 服务器地址
5. 可选：开启记忆/瞬时回复/思考模式
6. 保存后即可在首页看到

### 获取 Dify API Key

1. 登录 Dify 控制台
2. 进入目标应用
3. 点击左上角 "API 访问"
4. 复制 "API 密钥"

### 常见问题

**Q: 如何修改端口？**
编辑 `docker-compose.yml`：
```yaml
ports:
  - "8088:8088"  # 修改左侧数字
```

**Q: 数据库文件在哪？**
容器内 `/app/data/portal.db`，宿主机挂载在 `./data/` 目录。

**Q: 如何备份？**
```bash
cp -r data data-backup-$(date +%Y%m%d)
```

**Q: 记忆功能如何使用？**
1. 在应用编辑页面开启"启用记忆"
2. 对话中 AI 会自动提取关键信息
3. 在管理后台查看和编辑记忆

### 开发

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8088
```

### 安全建议

1. **修改默认密码** - 首次登录后立即修改
2. **使用 HTTPS** - 生产环境务必配置 SSL
3. **定期备份** - 备份 `data/` 目录
4. **限制访问** - 使用防火墙限制端口访问

### 截图

截图待补充，欢迎提交 PR。

### 贡献

欢迎提交 Issue 和 PR。

### 许可证

Apache License 2.0

Copyright (c) 2026 felixtowh

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.

本软件采用 Apache 2.0 许可证，你可以：
- ✅ 自由使用、修改、分发
- ✅ 商业使用
- ✅ 闭源修改（只需声明变更）

但必须保留版权声明和许可证文本。
