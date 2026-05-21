# Dify Portal

基于 FastAPI + Jinja2 的 Dify 应用门户系统，支持多应用管理、用户权限控制、流式对话、文件上传、语音功能和企业微信集成。

## 功能特性

- **多应用管理** - 集中管理多个 Dify 应用，支持应用可见性配置
- **用户系统** - 用户名密码登录，支持管理员和普通用户权限
- **流式对话** - SSE 实时流式输出，打字机效果，支持中断
- **文件上传** - 多文件上传、图片预览、多模态对话
- **语音功能** - 语音转文字（STT）、文字转语音（TTS）
- **消息反馈** - 点赞/点踩、复制、重新生成
- **权限管理** - 应用可见性（所有人/指定用户）、用户搜索、批量授权
- **管理后台** - 应用管理、用户管理、使用统计
- **企业微信集成** - 企微 OAuth 免登、扫码登录（可选）

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

在 `app/database.py` 中修改默认 Dify 服务器地址，或在管理后台添加应用时填写：

```python
# 默认 Dify API 地址
base_url = "https://your-dify-server.com"
```

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
│   └── wechat_auth.py   # 企微认证（可选）
├── templates/           # Jinja2 模板
│   ├── login.html       # 登录页
│   ├── index.html       # 应用列表
│   ├── chat.html        # 对话页面
│   └── admin/           # 管理后台
├── static/              # CSS/JS/图片
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
5. 保存后即可在首页看到

## 获取 Dify API Key

1. 登录你的 Dify 控制台
2. 进入目标应用
3. 点击左上角 "API 访问"
4. 复制 "API 密钥"

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

## 截图

![登录页](docs/screenshots/login.png)
![应用列表](docs/screenshots/apps.png)
![对话页面](docs/screenshots/chat.png)
![管理后台](docs/screenshots/admin.png)

> 截图待补充，欢迎提交 PR。

## 贡献

欢迎提交 Issue 和 PR。

## License

MIT License
