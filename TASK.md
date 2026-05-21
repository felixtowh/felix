# Dify Portal 部署任务记录

## 任务概述
部署 Dify Portal 应用门户，支持用户名密码登录，用于集中管理 Dify 应用的访问。

## 部署信息
- **部署位置**: `~/dify-portal-deploy/`
- **访问地址**: `http://192.168.101.48:8088`
- **容器名**: `dify-portal`
- **端口**: `8088`

## 重要配置决策
### 数据存储
- **决定**: 数据不挂载宿主机，全部放在 Docker 容器中
- **原因**: 便于迁移和删除，保持环境隔离
- **位置**: 容器内 `/app/data/portal.db`

## 默认账号
| 用户名 | 密码 | 权限 |
|--------|------|------|
| WangHui | WangHui123 | 管理员 |
| test | test123 | 普通用户 |

## 文件结构
```
~/dify-portal-deploy/
├── docker-compose.yml    # Docker 配置（无数据挂载）
├── Dockerfile            # 构建配置
├── requirements.txt      # Python 依赖
├── app/                  # 应用代码
│   ├── __init__.py
│   ├── main.py          # FastAPI 主应用
│   ├── database.py      # SQLite 数据库操作
│   ├── dify_client.py   # Dify API 客户端
│   └── wechat_auth.py   # 企微授权（可选）
├── templates/           # HTML 模板
│   ├── index.html       # 应用列表
│   ├── login.html       # 登录页面
│   ├── chat.html        # 对话页面
│   ├── error.html       # 错误页面
│   └── admin/           # 管理后台模板
│       ├── index.html
│       ├── apps.html
│       ├── users.html
│       └── stats.html
├── static/              # 静态文件（CSS/JS）
├── data/                # 数据库目录（容器内）
├── uploads/             # 上传文件（容器内）
└── logs/                # 日志（容器内）
```

## 常用命令
```bash
# 进入部署目录
cd ~/dify-portal-deploy

# 重启容器
docker-compose restart

# 查看日志
docker logs dify-portal

# 停止容器
docker-compose down

# 启动容器
docker-compose up -d

# 进入容器
docker exec -it dify-portal /bin/bash

# 查看数据库
docker exec dify-portal sqlite3 /app/data/portal.db ".tables"
```

## 功能模块
1. **用户登录**: 用户名密码登录（默认密码：用户名+123）
2. **应用列表**: 展示可用的 Dify 应用
3. **对话界面**: 与 Dify 应用进行对话
4. **管理后台**:
   - 应用管理（添加/编辑/删除 Dify 应用）
   - 用户管理（设置管理员）
   - 使用统计

## 后续改动任务
- 所有代码改动由我（辉仔）执行
- 如需修改，请直接告知需求
- 避免开启新对话或压缩上下文

## 创建时间
2026-04-19

## 最后更新
2026-04-19 - 取消数据挂载，全部放在 Docker 容器中
