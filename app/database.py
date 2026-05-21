import os
import json
import sqlite3
import hashlib
import secrets
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from zoneinfo import ZoneInfo

def now_beijing():
    """返回当前北京时间（YYYY-MM-DD HH:MM:SS）"""
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')

def hash_password(password: str) -> str:
    """使用 PBKDF2_HMAC 哈希密码，返回 salt:hash 格式"""
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return f"{salt}:{pwd_hash.hex()}"

def verify_password(password: str, stored: str) -> bool:
    """验证密码，兼容旧明文存储"""
    if not stored:
        return False
    if ":" not in stored:
        return password == stored
    salt, stored_hash = stored.split(":", 1)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return pwd_hash.hex() == stored_hash

DATABASE_PATH = os.getenv("DATABASE_URL", "sqlite:///app/data/portal.db").replace("sqlite://", "")

def init_db():
    """初始化数据库"""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    
    with get_db() as db:
        # Dify 应用表
        db.execute("""
            CREATE TABLE IF NOT EXISTS apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                api_key TEXT NOT NULL,
                base_url TEXT DEFAULT 'http://your-dify-server.com',
                description TEXT,
                is_active BOOLEAN DEFAULT 1,
                welcome_message TEXT DEFAULT '你好！有什么可以帮助你的吗？',
                logo_url TEXT,
                user_field TEXT DEFAULT 'name',
                skip_ssl_verify BOOLEAN DEFAULT 0,
                is_home_app BOOLEAN DEFAULT 0,
                enable_memory BOOLEAN DEFAULT 0,
                opening_statement TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 迁移：添加新字段（如果表已存在）
        try:
            db.execute("ALTER TABLE apps ADD COLUMN logo_url TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE apps ADD COLUMN user_field TEXT DEFAULT 'name'")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE apps ADD COLUMN skip_ssl_verify BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE apps ADD COLUMN is_home_app BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE apps ADD COLUMN opening_statement TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE apps ADD COLUMN enable_memory BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE apps ADD COLUMN enable_thinking BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE apps ADD COLUMN enable_instant_reply BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        
        # 用户表（企微用户）
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userid TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password TEXT,
                is_admin BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 应用权限表（用户-应用关联）
        db.execute("""
            CREATE TABLE IF NOT EXISTS app_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                app_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (app_id) REFERENCES apps (id),
                UNIQUE(user_id, app_id)
            )
        """)
        
        # 应用可见性设置表
        db.execute("""
            CREATE TABLE IF NOT EXISTS app_visibility (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id INTEGER NOT NULL,
                visibility_type TEXT DEFAULT 'all',  -- 'all' 或 'specific'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (app_id) REFERENCES apps (id),
                UNIQUE(app_id)
            )
        """)
        
        # 消息反馈表（点赞/点踩）
        db.execute("""
            CREATE TABLE IF NOT EXISTS message_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                app_id INTEGER NOT NULL,
                feedback_type TEXT NOT NULL,  -- 'like' 或 'dislike'
                content TEXT,  -- 反馈内容/评论
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (app_id) REFERENCES apps (id),
                UNIQUE(message_id, user_id)
            )
        """)
        
        # 使用统计表
        db.execute("""
            CREATE TABLE IF NOT EXISTS usage_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                app_id INTEGER NOT NULL,
                message_count INTEGER DEFAULT 1,
                last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (app_id) REFERENCES apps (id),
                UNIQUE(user_id, app_id)
            )
        """)
        
        # 每日统计表
        db.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                active_users INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 会话映射表（本地会话ID与Dify会话ID的映射）
        db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                app_id INTEGER NOT NULL,
                local_conv_id TEXT NOT NULL,
                dify_conv_id TEXT NOT NULL,
                title TEXT,
                total_tokens INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (app_id) REFERENCES apps (id),
                UNIQUE(user_id, app_id, local_conv_id)
            )
        """)
        
        # 迁移：添加 total_tokens 字段（如果表已存在）
        try:
            db.execute("ALTER TABLE conversation_mappings ADD COLUMN total_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        
        db.commit()
        
        # 本地会话缓存表（用于快速查询和持久化）
        db.execute("""
            CREATE TABLE IF NOT EXISTS conversations_local (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                app_id INTEGER NOT NULL,
                conversation_id TEXT NOT NULL,
                name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, app_id, conversation_id)
            )
        """)
        
        # 本地消息缓存表（用于消息持久化）
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages_local (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                app_id INTEGER NOT NULL,
                conversation_id TEXT NOT NULL,
                message_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations_local(conversation_id)
            )
        """)
        
        # 应用收藏表
        db.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                app_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (app_id) REFERENCES apps (id),
                UNIQUE(user_id, app_id)
            )
        """)
        
        # 应用每日统计表（用于准确的按应用按日统计）
        db.execute("""
            CREATE TABLE IF NOT EXISTS app_daily_stats (
                date TEXT NOT NULL,
                app_id INTEGER NOT NULL,
                active_users INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                conversation_count INTEGER DEFAULT 0,
                PRIMARY KEY (date, app_id),
                FOREIGN KEY (app_id) REFERENCES apps (id)
            )
        """)
        
        # ISV 授权企业表
        db.execute("""
            CREATE TABLE IF NOT EXISTS auth_corps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                corp_id TEXT UNIQUE NOT NULL,
                corp_name TEXT,
                agent_id TEXT,
                permanent_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 用户记忆/习惯表
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # 系统配置表
        db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ==================== Bot 管理相关表 ====================

        # 企微 Bot 表
        db.execute("""
            CREATE TABLE IF NOT EXISTS wecom_bots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL DEFAULT '',
                bot_id       TEXT NOT NULL UNIQUE,
                secret       TEXT NOT NULL,
                enabled      INTEGER NOT NULL DEFAULT 1,
                status       TEXT NOT NULL DEFAULT 'stopped',
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Dify 应用 API 配置表（Bot 专用，与 portal 的 apps 表独立）
        db.execute("""
            CREATE TABLE IF NOT EXISTS bot_dify_apps (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL DEFAULT '',
                api_key      TEXT NOT NULL,
                base_url     TEXT NOT NULL DEFAULT 'https://api.dify.ai/v1',
                enabled      INTEGER NOT NULL DEFAULT 1,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Bot → Dify 映射表
        db.execute("""
            CREATE TABLE IF NOT EXISTS bot_dify_mappings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id       INTEGER NOT NULL UNIQUE,
                dify_app_id  INTEGER NOT NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bot_id) REFERENCES wecom_bots(id) ON DELETE CASCADE,
                FOREIGN KEY (dify_app_id) REFERENCES bot_dify_apps(id) ON DELETE CASCADE
            )
        """)

        # Bot 对话上下文表
        db.execute("""
            CREATE TABLE IF NOT EXISTS bot_conversations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id       INTEGER NOT NULL,
                user_id      TEXT NOT NULL,
                dify_conv_id TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bot_id) REFERENCES wecom_bots(id) ON DELETE CASCADE
            )
        """)
        try:
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_conv_bot_user ON bot_conversations(bot_id, user_id)")
        except Exception:
            pass

        # Bot 消息记录表
        db.execute("""
            CREATE TABLE IF NOT EXISTS bot_messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id       INTEGER NOT NULL,
                user_id      TEXT NOT NULL,
                user_name    TEXT DEFAULT '',
                question     TEXT NOT NULL,
                answer       TEXT NOT NULL,
                token_count  INTEGER DEFAULT 0,
                dify_app_id  INTEGER,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bot_id) REFERENCES wecom_bots(id) ON DELETE CASCADE
            )
        """)

        # Bot 反馈 ID 缓存表
        db.execute("""
            CREATE TABLE IF NOT EXISTS bot_feedback_ids (
                bot_id       INTEGER NOT NULL,
                user_id      TEXT NOT NULL,
                message_id   TEXT NOT NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (bot_id, user_id),
                FOREIGN KEY (bot_id) REFERENCES wecom_bots(id) ON DELETE CASCADE
            )
        """)

        db.commit()

@contextmanager
def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# ==================== 应用管理 ====================

def get_all_apps():
    """获取所有应用（首页应用排第一位）"""
    with get_db() as db:
        return db.execute(
            "SELECT * FROM apps ORDER BY is_home_app DESC, created_at DESC"
        ).fetchall()

def get_active_apps():
    """获取启用的应用（首页应用排第一位）"""
    with get_db() as db:
        return db.execute(
            "SELECT * FROM apps WHERE is_active = 1 ORDER BY is_home_app DESC, created_at DESC"
        ).fetchall()

def get_home_app():
    """获取首页应用"""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM apps WHERE is_home_app = 1 AND is_active = 1 LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
        return None

def get_app(app_id: int):
    """获取单个应用"""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM apps WHERE id = ?", (app_id,)
        ).fetchone()
        if row:
            return dict(row)
        return None

def create_app(name: str, api_key: str, base_url: str = None,
               welcome_message: str = None, is_active: bool = True,
               logo_url: str = None, user_field: str = 'name', skip_ssl_verify: bool = False,
               opening_statement: str = None, is_home_app: bool = False, enable_memory: bool = False,
               enable_thinking: bool = False, enable_instant_reply: bool = False):
    """创建应用"""
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO apps (name, api_key, base_url, welcome_message, is_active, logo_url, user_field, skip_ssl_verify, opening_statement, is_home_app, enable_memory, enable_thinking, enable_instant_reply, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, api_key, base_url or 'http://your-dify-server.com', welcome_message, is_active, logo_url, user_field, skip_ssl_verify, opening_statement, is_home_app, enable_memory, enable_thinking, enable_instant_reply, now_beijing(), now_beijing())
        )
        db.commit()
        if is_home_app:
            db.execute("UPDATE apps SET is_home_app = 0 WHERE id != ?", (cursor.lastrowid,))
            db.commit()
        return cursor.lastrowid

def update_app(app_id: int, **kwargs):
    """更新应用"""
    allowed_fields = ['name', 'api_key', 'base_url', 'is_active', 'welcome_message', 'logo_url', 'user_field', 'skip_ssl_verify', 'opening_statement', 'is_home_app', 'enable_memory', 'enable_thinking', 'enable_instant_reply']
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    
    if not updates:
        return False
    
    set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
    values = list(updates.values()) + [app_id]
    
    with get_db() as db:
        db.execute(f"UPDATE apps SET {set_clause}, updated_at = ? WHERE id = ?", list(updates.values()) + [now_beijing(), app_id])
        db.commit()
        if updates.get('is_home_app'):
            db.execute("UPDATE apps SET is_home_app = 0 WHERE id != ?", (app_id,))
            db.commit()
        return True

def delete_app(app_id: int):
    """删除应用"""
    with get_db() as db:
        db.execute("DELETE FROM apps WHERE id = ?", (app_id,))
        db.execute("DELETE FROM app_permissions WHERE app_id = ?", (app_id,))
        db.execute("DELETE FROM app_visibility WHERE app_id = ?", (app_id,))
        db.execute("DELETE FROM favorites WHERE app_id = ?", (app_id,))
        db.execute("DELETE FROM message_feedback WHERE app_id = ?", (app_id,))
        db.execute("DELETE FROM conversation_mappings WHERE app_id = ?", (app_id,))
        db.execute("DELETE FROM usage_stats WHERE app_id = ?", (app_id,))
        db.commit()

# ==================== 用户管理 ====================

def get_or_create_user(userid: str, name: str, password: str = None):
    """获取或创建用户（企微用户）。返回 (user_dict, is_new)"""
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE userid = ?", (userid,)
        ).fetchone()

        if user:
            # 更新用户信息
            db.execute(
                "UPDATE users SET name = ? WHERE userid = ?",
                (name, userid)
            )
            db.commit()
            return dict(user), False

        # 创建新用户，默认密码：admin001
        default_password = password or "admin001"
        cursor = db.execute(
            """INSERT INTO users (userid, name, password, created_at)
               VALUES (?, ?, ?, ?)""",
            (userid, name, hash_password(default_password), now_beijing())
        )
        db.commit()

        new_user = db.execute(
            "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        user_dict = dict(new_user) if new_user else None

        # 新用户自动授权所有活跃应用
        if user_dict:
            apps = get_active_apps()
            for app in apps:
                try:
                    db.execute(
                        "INSERT INTO app_permissions (user_id, app_id) VALUES (?, ?)",
                        (user_dict["id"], app["id"])
                    )
                except sqlite3.IntegrityError:
                    pass
            db.commit()

        return (user_dict, True) if user_dict else (None, False)

def get_user_by_userid(userid: str):
    """通过 userid 获取用户"""
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE userid = ?", (userid,)
        ).fetchone()
        return dict(user) if user else None

def get_user_by_name(name: str):
    """通过 name 获取用户（匹配第一个，用于 Dify 外部 API 回调）"""
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        return dict(user) if user else None

def get_all_users():
    """获取所有用户"""
    with get_db() as db:
        return db.execute(
            "SELECT id, userid, name, is_admin, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()

def set_user_admin(user_id: int, is_admin: bool):
    """设置用户管理员权限"""
    with get_db() as db:
        db.execute(
            "UPDATE users SET is_admin = ? WHERE id = ?",
            (is_admin, user_id)
        )
        db.commit()

def delete_user(user_id: int):
    """删除用户"""
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.execute("DELETE FROM app_permissions WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM favorites WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM message_feedback WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM conversation_mappings WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM usage_stats WHERE user_id = ?", (user_id,))
        db.commit()

def update_user(user_id: int, **kwargs):
    """更新用户信息"""
    allowed_fields = ['name', 'userid', 'password']
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}

    if not updates:
        return False

    with get_db() as db:
        set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [user_id]

        db.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?",
            values
        )
        db.commit()
        return True

def reset_user_password(user_id: int, new_password: str):
    """重置用户密码（使用 PBKDF2_HMAC 哈希存储）"""
    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()

        if not user:
            return False

        db.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (hash_password(new_password), user_id)
        )
        db.commit()
        return True


def batch_create_users(users_data: list):
    """批量创建用户
    users_data: 列表，每个元素是字典，包含 userid, name
    """
    with get_db() as db:
        created_count = 0
        skipped_count = 0
        
        for user_data in users_data:
            userid = user_data.get('userid', '').strip()
            name = user_data.get('name', '').strip()
            password = user_data.get('password', '').strip() or (userid + '001')

            if not userid or not name:
                skipped_count += 1
                continue

            # 检查用户是否已存在
            existing = db.execute(
                "SELECT 1 FROM users WHERE userid = ?", (userid,)
            ).fetchone()

            if existing:
                skipped_count += 1
                continue

            # 创建新用户
            db.execute(
                """INSERT INTO users (userid, name, password, created_at)
                   VALUES (?, ?, ?, ?)""",
                (userid, name, hash_password(password), now_beijing())
            )
            created_count += 1
        
        db.commit()
        return {'created': created_count, 'skipped': skipped_count}

# ==================== 应用权限管理 ====================

def get_user_allowed_apps(user_id: Optional[int]):
    """获取用户有权限的应用（首页应用始终可见且排第一位）"""
    with get_db() as db:
        if user_id is None:
            # 未登录用户，返回所有人可见的启用应用（含首页应用）
            return db.execute("""
                SELECT a.* FROM apps a
                LEFT JOIN app_visibility av ON a.id = av.app_id
                WHERE a.is_active = 1 
                AND (a.is_home_app = 1 OR av.visibility_type IS NULL OR av.visibility_type = 'all')
                ORDER BY a.is_home_app DESC, a.created_at DESC
            """).fetchall()
        
        # 检查用户是否有特定权限
        has_permission = db.execute(
            "SELECT 1 FROM app_permissions WHERE user_id = ? LIMIT 1",
            (user_id,)
        ).fetchone()
        
        if has_permission:
            # 返回有权限的应用 + 所有人可见的应用 + 首页应用（去重）
            return db.execute("""
                SELECT DISTINCT a.* FROM apps a
                LEFT JOIN app_permissions ap ON a.id = ap.app_id AND ap.user_id = ?
                LEFT JOIN app_visibility av ON a.id = av.app_id
                WHERE a.is_active = 1 
                AND (a.is_home_app = 1 OR ap.user_id IS NOT NULL OR av.visibility_type IS NULL OR av.visibility_type = 'all')
                ORDER BY a.is_home_app DESC, a.created_at DESC
            """, (user_id,)).fetchall()
        else:
            # 如果没有特定权限，返回所有人可见的启用应用（含首页应用）
            return db.execute("""
                SELECT a.* FROM apps a
                LEFT JOIN app_visibility av ON a.id = av.app_id
                WHERE a.is_active = 1 
                AND (a.is_home_app = 1 OR av.visibility_type IS NULL OR av.visibility_type = 'all')
                ORDER BY a.is_home_app DESC, a.created_at DESC
            """).fetchall()

def user_has_app_permission(user_id: Optional[int], app_id: int):
    """检查用户是否有应用权限（首页应用始终有权限）"""
    with get_db() as db:
        # 首页应用始终允许访问
        app = db.execute(
            "SELECT is_home_app FROM apps WHERE id = ?",
            (app_id,)
        ).fetchone()
        if app and app['is_home_app']:
            return True
    
    if user_id is None:
        # 检查应用是否为所有人可见
        with get_db() as db:
            visibility = db.execute(
                "SELECT visibility_type FROM app_visibility WHERE app_id = ?",
                (app_id,)
            ).fetchone()
            return visibility is None or visibility['visibility_type'] == 'all'
    
    with get_db() as db:
        # 检查是否有权限记录
        has_permission = db.execute(
            "SELECT 1 FROM app_permissions WHERE user_id = ? AND app_id = ?",
            (user_id, app_id)
        ).fetchone()
        
        if has_permission:
            return True
        
        # 检查用户是否有任何权限记录
        has_any = db.execute(
            "SELECT 1 FROM app_permissions WHERE user_id = ? LIMIT 1",
            (user_id,)
        ).fetchone()
        
        # 如果用户有权限记录但不是这个应用，检查应用是否为所有人可见
        if has_any:
            visibility = db.execute(
                "SELECT visibility_type FROM app_visibility WHERE app_id = ?",
                (app_id,)
            ).fetchone()
            return visibility is None or visibility['visibility_type'] == 'all'
        
        # 如果没有权限记录，默认允许访问所有人可见的应用
        visibility = db.execute(
            "SELECT visibility_type FROM app_visibility WHERE app_id = ?",
            (app_id,)
        ).fetchone()
        return visibility is None or visibility['visibility_type'] == 'all'

def grant_app_permission(user_id: int, app_id: int):
    """授予用户应用权限"""
    with get_db() as db:
        try:
            db.execute(
                "INSERT INTO app_permissions (user_id, app_id, created_at) VALUES (?, ?, ?)",
                (user_id, app_id, now_beijing())
            )
            db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def revoke_app_permission(user_id: int, app_id: int):
    """撤销用户应用权限"""
    with get_db() as db:
        db.execute(
            "DELETE FROM app_permissions WHERE user_id = ? AND app_id = ?",
            (user_id, app_id)
        )
        db.commit()

def get_app_permissions(app_id: int):
    """获取应用的所有权限用户"""
    with get_db() as db:
        return db.execute("""
            SELECT u.id, u.userid, u.name
            FROM users u
            JOIN app_permissions ap ON u.id = ap.user_id
            WHERE ap.app_id = ?
        """, (app_id,)).fetchall()

# ==================== 使用统计 ====================

def record_usage(user_id: Optional[int], app_id: int):
    """记录使用统计
    
    注意：统计查询已改为直接从 conversation_mappings 表计算，
    此函数保留用于兼容现有调用，但不再维护 usage_stats / daily_stats / app_daily_stats
    等聚合/增量表，避免消息级计数与对话级计数的不一致。
    """
    # 所有统计由 conversation_mappings 的 created_at 驱动，
    # set_conversation_mapping() 已负责维护对话记录。
    pass

def get_usage_stats():
    """获取使用统计概览（基于 conversation_mappings 获取准确的对话统计）"""
    with get_db() as db:
        # 按应用统计（对话数）
        app_stats = db.execute("""
            SELECT 
                a.name as app_name,
                COUNT(DISTINCT cm.user_id) as user_count,
                COUNT(cm.id) as total_conversations,
                MAX(cm.created_at) as last_used
            FROM apps a
            LEFT JOIN conversation_mappings cm ON a.id = cm.app_id
            GROUP BY a.id
            ORDER BY total_conversations DESC
        """).fetchall()
        
        # 按用户统计（对话数）
        user_stats = db.execute("""
            SELECT 
                u.name as user_name,
                u.userid as userid,
                COUNT(DISTINCT cm.app_id) as app_count,
                COUNT(cm.id) as total_conversations,
                MAX(cm.created_at) as last_used
            FROM users u
            LEFT JOIN conversation_mappings cm ON u.id = cm.user_id
            GROUP BY u.id
            ORDER BY total_conversations DESC
        """).fetchall()
        
        # 总体统计（从实际表获取准确数量）
        total_users_row = db.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
        active_apps_row = db.execute("SELECT COUNT(*) as cnt FROM apps WHERE is_active = 1").fetchone()
        total_conversations_row = db.execute("SELECT COUNT(*) as cnt FROM conversation_mappings").fetchone()
        total_tokens_row = db.execute("SELECT COALESCE(SUM(total_tokens), 0) as cnt FROM conversation_mappings").fetchone()

        # 今日数据（基于 conversation_mappings 的 created_at）
        today_active_users_row = db.execute("""
            SELECT COUNT(DISTINCT user_id) as cnt FROM conversation_mappings
            WHERE date(created_at) = date('now')
        """).fetchone()
        today_conversations_row = db.execute("""
            SELECT COUNT(*) as cnt FROM conversation_mappings
            WHERE date(created_at) = date('now')
        """).fetchone()
        today_tokens_row = db.execute("""
            SELECT COALESCE(SUM(total_tokens), 0) as cnt FROM conversation_mappings
            WHERE date(created_at) = date('now')
        """).fetchone()
        today_active_apps_row = db.execute("""
            SELECT COUNT(DISTINCT app_id) as cnt FROM conversation_mappings
            WHERE date(created_at) = date('now')
        """).fetchone()

        total_dict = {
            'total_users': total_users_row['cnt'] if total_users_row else 0,
            'active_apps': active_apps_row['cnt'] if active_apps_row else 0,
            'total_conversations': total_conversations_row['cnt'] if total_conversations_row else 0,
            'total_tokens': total_tokens_row['cnt'] if total_tokens_row else 0,
            'today_active_users': today_active_users_row['cnt'] if today_active_users_row else 0,
            'today_conversations': today_conversations_row['cnt'] if today_conversations_row else 0,
            'today_tokens': today_tokens_row['cnt'] if today_tokens_row else 0,
            'today_active_apps': today_active_apps_row['cnt'] if today_active_apps_row else 0
        }
        
        return {
            'app_stats': [dict(row) for row in app_stats],
            'user_stats': [dict(row) for row in user_stats],
            'total': total_dict
        }

def get_daily_stats(days: int = 30):
    """获取近 N 天每日统计数据（基于 conversation_mappings 获取准确的每日对话数）"""
    with get_db() as db:
        rows = db.execute("""
            WITH RECURSIVE dates(date) AS (
                SELECT date('now', '-' || ? || ' days')
                UNION ALL
                SELECT date(date, '+1 day')
                FROM dates
                WHERE date < date('now')
            )
            SELECT 
                d.date,
                COALESCE(cm_ds.active_users, 0) as active_users,
                COALESCE(cm_ds.conversation_count, 0) as message_count
            FROM dates d
            LEFT JOIN (
                SELECT 
                    date(created_at) as date,
                    COUNT(DISTINCT user_id) as active_users,
                    COUNT(*) as conversation_count
                FROM conversation_mappings
                GROUP BY date(created_at)
            ) cm_ds ON d.date = cm_ds.date
            ORDER BY d.date
        """, (days,)).fetchall()
        
        return [dict(row) for row in rows]

def get_app_detail_stats(app_id: int, days: int = 30):
    """获取某个应用的详细使用统计"""
    with get_db() as db:
        # 1. 应用基本信息
        app = db.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
        if not app:
            return None
        
        app_info = dict(app)
        
        # 2. 总体使用统计（从 conversation_mappings 获取准确的对话数和用户数）
        total_stats = db.execute("""
            SELECT
                COUNT(DISTINCT user_id) as user_count,
                COUNT(*) as total_conversations,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                MAX(created_at) as last_used
            FROM conversation_mappings
            WHERE app_id = ?
        """, (app_id,)).fetchone()

        total_dict = dict(total_stats) if total_stats else {'user_count': 0, 'total_conversations': 0, 'total_tokens': 0, 'last_used': None}
        # 总对话数同时作为 total_messages 返回（兼容旧 API 字段名）
        total_dict['total_messages'] = total_dict['total_conversations']
        
        # 3. 按用户统计明细（从 conversation_mappings 获取准确的对话数）
        user_stats = db.execute("""
            SELECT 
                u.id,
                u.userid,
                u.name,
                COUNT(cm.id) as conversation_count,
                MAX(cm.created_at) as last_used
            FROM users u
            LEFT JOIN conversation_mappings cm ON u.id = cm.user_id AND cm.app_id = ?
            GROUP BY u.id
            HAVING conversation_count > 0
            ORDER BY conversation_count DESC, u.name
        """, (app_id,)).fetchall()
        
        # 4. 近 N 天每日统计（直接从 conversation_mappings 获取准确的每日对话分布）
        daily_rows = db.execute("""
            WITH RECURSIVE dates(date) AS (
                SELECT date('now', '-' || ? || ' days')
                UNION ALL
                SELECT date(date, '+1 day')
                FROM dates
                WHERE date < date('now')
            )
            SELECT 
                d.date,
                COALESCE(cm_ds.active_users, 0) as active_users,
                COALESCE(cm_ds.conversation_count, 0) as message_count
            FROM dates d
            LEFT JOIN (
                SELECT 
                    date(created_at) as date,
                    COUNT(DISTINCT user_id) as active_users,
                    COUNT(*) as conversation_count
                FROM conversation_mappings
                WHERE app_id = ?
                GROUP BY date(created_at)
            ) cm_ds ON d.date = cm_ds.date
            ORDER BY d.date
        """, (days, app_id)).fetchall()
        
        return {
            'app': app_info,
            'total': total_dict,
            'users': [dict(row) for row in user_stats],
            'daily': [dict(row) for row in daily_rows]
        }

# ==================== 会话映射管理 ====================

def get_conversation_mapping(user_id: int, app_id: int, local_conv_id: str):
    """获取会话映射"""
    with get_db() as db:
        row = db.execute(
            """SELECT dify_conv_id, title, created_at 
               FROM conversation_mappings 
               WHERE user_id = ? AND app_id = ? AND local_conv_id = ?""",
            (user_id, app_id, local_conv_id)
        ).fetchone()
        return row

def set_conversation_mapping(user_id: int, app_id: int, local_conv_id: str, dify_conv_id: str, title: str = None):
    """设置会话映射"""
    with get_db() as db:
        ts = now_beijing()
        db.execute(
            """INSERT INTO conversation_mappings
                (user_id, app_id, local_conv_id, dify_conv_id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, app_id, local_conv_id) DO UPDATE SET
                dify_conv_id = excluded.dify_conv_id,
                title = COALESCE(excluded.title, conversation_mappings.title),
                updated_at = ?""",
            (user_id, app_id, local_conv_id, dify_conv_id, title, ts, ts, ts)
        )
        db.commit()

def update_conversation_tokens(user_id: int, app_id: int, local_conv_id: str, total_tokens: int):
    """更新会话的 token 使用量"""
    with get_db() as db:
        db.execute(
            """UPDATE conversation_mappings
               SET total_tokens = ?, updated_at = ?
               WHERE user_id = ? AND app_id = ? AND local_conv_id = ?""",
            (total_tokens, now_beijing(), user_id, app_id, local_conv_id)
        )
        db.commit()

def get_user_conversations(user_id: int, app_id: int):
    """获取用户的所有会话映射"""
    with get_db() as db:
        rows = db.execute(
            """SELECT local_conv_id, dify_conv_id, title, created_at, updated_at
               FROM conversation_mappings 
               WHERE user_id = ? AND app_id = ?
               ORDER BY updated_at DESC""",
            (user_id, app_id)
        ).fetchall()
        return rows

def delete_conversation_mapping(user_id: int, app_id: int, local_conv_id: str):
    """删除会话映射"""
    with get_db() as db:
        db.execute(
            """DELETE FROM conversation_mappings 
               WHERE user_id = ? AND app_id = ? AND local_conv_id = ?""",
            (user_id, app_id, local_conv_id)
        )
        db.commit()

def get_user_active_conversation(user_id: int, app_id: int) -> Optional[str]:
    """获取用户在应用上的最近活跃会话ID（dify_conv_id）"""
    with get_db() as db:
        row = db.execute(
            """SELECT dify_conv_id FROM conversation_mappings 
               WHERE user_id = ? AND app_id = ?
               ORDER BY updated_at DESC LIMIT 1""",
            (user_id, app_id)
        ).fetchone()
        return row['dify_conv_id'] if row else None

def update_conversation_title(user_id: int, app_id: int, local_conv_id: str, title: str):
    """更新会话标题"""
    with get_db() as db:
        db.execute(
            """UPDATE conversation_mappings 
               SET title = ?, updated_at = ?
               WHERE user_id = ? AND app_id = ? AND local_conv_id = ?""",
            (title, now_beijing(), user_id, app_id, local_conv_id)
        )
        db.commit()

# ==================== 应用可见性管理 ====================

def set_app_visibility(app_id: int, visibility_type: str):
    """设置应用可见性"""
    with get_db() as db:
        ts = now_beijing()
        db.execute(
            """INSERT INTO app_visibility (app_id, visibility_type, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(app_id) DO UPDATE SET
                visibility_type = excluded.visibility_type,
                updated_at = ?""",
            (app_id, visibility_type, ts, ts, ts)
        )
        db.commit()

def get_app_visibility(app_id: int):
    """获取应用可见性设置"""
    with get_db() as db:
        row = db.execute(
            "SELECT visibility_type FROM app_visibility WHERE app_id = ?",
            (app_id,)
        ).fetchone()
        return row['visibility_type'] if row else 'all'

def batch_grant_app_permission(app_id: int, user_ids: list):
    """批量授予用户应用权限"""
    with get_db() as db:
        created_count = 0
        for user_id in user_ids:
            try:
                db.execute(
                    "INSERT INTO app_permissions (user_id, app_id) VALUES (?, ?)",
                    (user_id, app_id)
                )
                created_count += 1
            except sqlite3.IntegrityError:
                pass  # 已存在，跳过
        db.commit()
        return created_count

def batch_revoke_app_permission(app_id: int, user_ids: list):
    """批量撤销用户应用权限"""
    with get_db() as db:
        placeholders = ','.join(['?' for _ in user_ids])
        db.execute(
            f"DELETE FROM app_permissions WHERE app_id = ? AND user_id IN ({placeholders})",
            (app_id,) + tuple(user_ids)
        )
        db.commit()

# ==================== 消息反馈管理 ====================

def add_message_feedback(message_id: str, user_id: int, app_id: int, feedback_type: str, content: str = None):
    """添加消息反馈"""
    with get_db() as db:
        ts = now_beijing()
        try:
            db.execute(
                """INSERT INTO message_feedback (message_id, user_id, app_id, feedback_type, content, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (message_id, user_id, app_id, feedback_type, content, ts)
            )
            db.commit()
            return True
        except sqlite3.IntegrityError:
            # 已存在反馈，更新它
            db.execute(
                """UPDATE message_feedback 
                   SET feedback_type = ?, content = ?, created_at = ?
                   WHERE message_id = ? AND user_id = ?""",
                (feedback_type, content, ts, message_id, user_id)
            )
            db.commit()
            return True

def get_message_feedback(message_id: str, user_id: int = None):
    """获取消息反馈"""
    with get_db() as db:
        if user_id:
            row = db.execute(
                """SELECT feedback_type, content FROM message_feedback 
                   WHERE message_id = ? AND user_id = ?""",
                (message_id, user_id)
            ).fetchone()
            return dict(row) if row else None
        else:
            rows = db.execute(
                """SELECT feedback_type, COUNT(*) as count FROM message_feedback 
                   WHERE message_id = ? GROUP BY feedback_type""",
                (message_id,)
            ).fetchall()
            return {row['feedback_type']: row['count'] for row in rows}

def delete_message_feedback(message_id: str, user_id: int):
    """删除消息反馈"""
    with get_db() as db:
        db.execute(
            "DELETE FROM message_feedback WHERE message_id = ? AND user_id = ?",
            (message_id, user_id)
        )
        db.commit()
        return True

# ==================== 应用收藏 ====================

def add_favorite(user_id: int, app_id: int):
    """添加应用到收藏"""
    with get_db() as db:
        try:
            db.execute(
                "INSERT INTO favorites (user_id, app_id, created_at) VALUES (?, ?, ?)",
                (user_id, app_id, now_beijing())
            )
            db.commit()
            return True
        except sqlite3.IntegrityError:
            return True  # 已存在

def remove_favorite(user_id: int, app_id: int):
    """取消收藏应用"""
    with get_db() as db:
        db.execute(
            "DELETE FROM favorites WHERE user_id = ? AND app_id = ?",
            (user_id, app_id)
        )
        db.commit()
        return True

def get_user_favorites(user_id: int):
    """获取用户收藏的应用ID列表"""
    with get_db() as db:
        rows = db.execute(
            "SELECT app_id FROM favorites WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        return [row['app_id'] for row in rows]

def is_favorite(user_id: int, app_id: int):
    """检查应用是否被用户收藏"""
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND app_id = ?",
            (user_id, app_id)
        ).fetchone()
        return bool(row)


# ==================== ISV 授权企业管理 ====================

def save_auth_corp_db(corp_id: str, corp_name: str = None, agent_id: str = None, permanent_code: str = None):
    """保存或更新授权企业信息"""
    with get_db() as db:
        db.execute(
            """INSERT INTO auth_corps (corp_id, corp_name, agent_id, permanent_code, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(corp_id) DO UPDATE SET
                corp_name = excluded.corp_name,
                agent_id = excluded.agent_id,
                permanent_code = excluded.permanent_code""",
            (corp_id, corp_name, agent_id, permanent_code, now_beijing())
        )
        db.commit()

def get_auth_corp_db(corp_id: str) -> Optional[Dict]:
    """获取指定企业的授权信息"""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM auth_corps WHERE corp_id = ?",
            (corp_id,)
        ).fetchone()
        return dict(row) if row else None

def get_all_auth_corps_db() -> List[Dict]:
    """获取所有授权企业"""
    with get_db() as db:
        rows = db.execute("SELECT * FROM auth_corps ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

def delete_auth_corp_db(corp_id: str):
    """删除授权企业"""
    with get_db() as db:
        db.execute("DELETE FROM auth_corps WHERE corp_id = ?", (corp_id,))
        db.commit()


# ==================== 用户记忆/习惯 ====================

def get_user_memories(user_id: int) -> List[Dict]:
    """获取用户的所有活跃记忆"""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM user_memories WHERE user_id = ? AND is_active = 1 ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(row) for row in rows]

def create_user_memory(user_id: int, content: str, source: str = 'manual') -> int:
    """创建记忆，返回新记录 ID"""
    with get_db() as db:
        cursor = db.execute(
            "INSERT INTO user_memories (user_id, content, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, content, source, now_beijing(), now_beijing())
        )
        db.commit()
        return cursor.lastrowid

def update_user_memory(memory_id: int, content: str = None, is_active: bool = None) -> bool:
    """更新记忆内容或启用状态"""
    with get_db() as db:
        updates = []
        params = []
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)
        if not updates:
            return False
        updates.append("updated_at = ?")
        params.insert(-1, now_beijing())  # 在 memory_id 之前插入时间
        db.execute(
            f"UPDATE user_memories SET {', '.join(updates)} WHERE id = ?",
            params
        )
        db.commit()
        return True

def delete_user_memory(memory_id: int) -> bool:
    """删除记忆"""
    with get_db() as db:
        db.execute("DELETE FROM user_memories WHERE id = ?", (memory_id,))
        db.commit()
        return True

# ==================== 系统配置 ====================

def get_setting(key: str, default: str = "") -> str:
    """获取系统配置项"""
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

def set_setting(key: str, value: str) -> bool:
    """设置系统配置项"""
    with get_db() as db:
        db.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = ?""",
            (key, value, now_beijing(), now_beijing())
        )
        db.commit()
        return True


# ==================== Bot 管理 ====================

def list_wecom_bots(enabled_only: bool = False) -> list:
    with get_db() as db:
        sql = "SELECT * FROM wecom_bots"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id ASC"
        rows = db.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_wecom_bot(bot_pk: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM wecom_bots WHERE id=?", (bot_pk,)).fetchone()
    return dict(row) if row else None


def get_wecom_bot_by_bot_id(bot_id: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM wecom_bots WHERE bot_id=?", (bot_id,)).fetchone()
    return dict(row) if row else None


def create_wecom_bot(name: str, bot_id: str, secret: str, enabled: int = 1) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO wecom_bots(name, bot_id, secret, enabled, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (name, bot_id, secret, enabled, "stopped", now_beijing(), now_beijing()),
        )
        db.commit()
        return cur.lastrowid


def update_wecom_bot(bot_pk: int, **kwargs) -> bool:
    allowed = {"name", "bot_id", "secret", "enabled", "status"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = now_beijing()
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [bot_pk]
    with get_db() as db:
        db.execute(f"UPDATE wecom_bots SET {sets} WHERE id=?", vals)
        db.commit()
        return True


def delete_wecom_bot(bot_pk: int):
    with get_db() as db:
        db.execute("DELETE FROM wecom_bots WHERE id=?", (bot_pk,))
        db.execute("DELETE FROM bot_dify_mappings WHERE bot_id=?", (bot_pk,))
        db.execute("DELETE FROM bot_conversations WHERE bot_id=?", (bot_pk,))
        db.commit()


# ==================== Bot Dify 应用配置 ====================

def list_bot_dify_apps(enabled_only: bool = False) -> list:
    with get_db() as db:
        sql = "SELECT * FROM bot_dify_apps"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id ASC"
        rows = db.execute(sql).fetchall()
    return [dict(r) for r in rows]


def get_bot_dify_app(app_pk: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM bot_dify_apps WHERE id=?", (app_pk,)).fetchone()
    return dict(row) if row else None


def create_bot_dify_app(name: str, api_key: str, base_url: str, enabled: int = 1) -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO bot_dify_apps(name, api_key, base_url, enabled, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (name, api_key, base_url, enabled, now_beijing(), now_beijing()),
        )
        db.commit()
        return cur.lastrowid


def update_bot_dify_app(app_pk: int, **kwargs) -> bool:
    allowed = {"name", "api_key", "base_url", "enabled"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = now_beijing()
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [app_pk]
    with get_db() as db:
        db.execute(f"UPDATE bot_dify_apps SET {sets} WHERE id=?", vals)
        db.commit()
        return True


def delete_bot_dify_app(app_pk: int):
    with get_db() as db:
        db.execute("DELETE FROM bot_dify_apps WHERE id=?", (app_pk,))
        db.execute("DELETE FROM bot_dify_mappings WHERE dify_app_id=?", (app_pk,))
        db.commit()


def get_bot_dify_mappings_for_app(app_pk: int) -> list:
    with get_db() as db:
        rows = db.execute(
            "SELECT bot_id FROM bot_dify_mappings WHERE dify_app_id=?", (app_pk,)
        ).fetchall()
    return [dict(r) for r in rows]


# ==================== Bot-Dify 映射 ====================

def set_bot_dify_mapping(bot_pk: int, dify_app_pk: int) -> bool:
    with get_db() as db:
        db.execute(
            "INSERT INTO bot_dify_mappings(bot_id, dify_app_id, created_at) VALUES(?,?,?) "
            "ON CONFLICT(bot_id) DO UPDATE SET dify_app_id=excluded.dify_app_id",
            (bot_pk, dify_app_pk, now_beijing()),
        )
        db.commit()
        return True


def get_bot_dify_config(bot_pk: int):
    with get_db() as db:
        row = db.execute(
            "SELECT d.id, d.name, d.api_key, d.base_url, d.enabled "
            "FROM bot_dify_mappings m JOIN bot_dify_apps d ON m.dify_app_id = d.id "
            "WHERE m.bot_id=? AND d.enabled=1",
            (bot_pk,),
        ).fetchone()
    return dict(row) if row else None


def delete_bot_mapping(bot_pk: int):
    with get_db() as db:
        db.execute("DELETE FROM bot_dify_mappings WHERE bot_id=?", (bot_pk,))
        db.commit()


# ==================== Bot 对话上下文 ====================

def get_bot_conv_id(bot_pk: int, user_id: str):
    with get_db() as db:
        row = db.execute(
            "SELECT dify_conv_id FROM bot_conversations WHERE bot_id=? AND user_id=?",
            (bot_pk, user_id),
        ).fetchone()
    return row["dify_conv_id"] if row else None


def save_bot_conv_id(bot_pk: int, user_id: str, dify_conv_id: str):
    with get_db() as db:
        db.execute(
            "INSERT INTO bot_conversations(bot_id, user_id, dify_conv_id, created_at, updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(bot_id, user_id) DO UPDATE SET dify_conv_id=excluded.dify_conv_id, updated_at=excluded.updated_at",
            (bot_pk, user_id, dify_conv_id, now_beijing(), now_beijing()),
        )
        db.commit()


def delete_bot_conv_id(bot_pk: int, user_id: str):
    with get_db() as db:
        db.execute("DELETE FROM bot_conversations WHERE bot_id=? AND user_id=?", (bot_pk, user_id))
        db.commit()


# ==================== Bot 消息 ====================

def save_bot_message(bot_pk: int, user_id: str, user_name: str, question: str, answer: str,
                     token_count: int = 0, dify_app_id: int = None):
    with get_db() as db:
        db.execute(
            "INSERT INTO bot_messages(bot_id, user_id, user_name, question, answer, token_count, dify_app_id, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (bot_pk, user_id, user_name, question, answer, token_count, dify_app_id, now_beijing()),
        )
        db.commit()


def save_bot_feedback_id(bot_pk: int, user_id: str, message_id: str):
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO bot_feedback_ids(bot_id, user_id, message_id, created_at) VALUES(?,?,?,?)",
            (bot_pk, user_id, message_id, now_beijing()),
        )
        db.commit()


def get_bot_feedback_id(bot_pk: int, user_id: str):
    with get_db() as db:
        row = db.execute(
            "SELECT message_id FROM bot_feedback_ids WHERE bot_id=? AND user_id=?",
            (bot_pk, user_id),
        ).fetchone()
    return row["message_id"] if row else None


# ==================== Bot 统计 ====================

def bot_stats(bot_pk: int = None) -> dict:
    today = datetime.now(ZoneInfo('Asia/Shanghai')).strftime("%Y-%m-%d")
    with get_db() as db:
        bot_where = "WHERE bot_id=?" if bot_pk else ""
        date_kw = "AND" if bot_pk else "WHERE"
        params = (bot_pk,) if bot_pk else ()

        def _one(sql, params_extra=()):
            return db.execute(sql, params + params_extra).fetchone()[0]

        total_users = _one(f"SELECT COUNT(DISTINCT user_id) FROM bot_messages {bot_where}", params)
        total_msgs = _one(f"SELECT COUNT(*) FROM bot_messages {bot_where}", params)
        total_tokens = _one(f"SELECT COALESCE(SUM(token_count),0) FROM bot_messages {bot_where}", params)
        today_msgs = _one(f"SELECT COUNT(*) FROM bot_messages {bot_where} {date_kw} created_at LIKE ?", params + (f"{today}%",))
        today_users = _one(f"SELECT COUNT(DISTINCT user_id) FROM bot_messages {bot_where} {date_kw} created_at LIKE ?", params + (f"{today}%",))
        today_tokens = _one(f"SELECT COALESCE(SUM(token_count),0) FROM bot_messages {bot_where} {date_kw} created_at LIKE ?", params + (f"{today}%",))

    return {
        "total_users": total_users,
        "total_messages": total_msgs,
        "total_tokens": total_tokens,
        "today_messages": today_msgs,
        "today_users": today_users,
        "today_tokens": today_tokens,
    }


def bot_list_users(bot_pk: int = None, limit: int = 30) -> list:
    with get_db() as db:
        if bot_pk:
            rows = db.execute(
                "SELECT user_id, user_name, MAX(created_at) as last_time, "
                "COUNT(*) as msg_count, COALESCE(SUM(token_count),0) as total_tokens "
                "FROM bot_messages WHERE bot_id=? GROUP BY user_id ORDER BY last_time DESC LIMIT ?",
                (bot_pk, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT user_id, user_name, MAX(created_at) as last_time, "
                "COUNT(*) as msg_count, COALESCE(SUM(token_count),0) as total_tokens "
                "FROM bot_messages GROUP BY user_id ORDER BY last_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def bot_get_messages(user_id: str, bot_pk: int = None, limit: int = 100) -> list:
    with get_db() as db:
        if bot_pk:
            rows = db.execute(
                "SELECT * FROM bot_messages WHERE bot_id=? AND user_id=? ORDER BY id ASC LIMIT ?",
                (bot_pk, user_id, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM bot_messages WHERE user_id=? ORDER BY id ASC LIMIT ?",
                (user_id, limit),
            ).fetchall()
    return [dict(r) for r in rows]
