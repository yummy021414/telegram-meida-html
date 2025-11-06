import aiosqlite
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import config

class Database:
    def __init__(self):
        self.db_path = config.DATABASE_PATH
    
    async def init_db(self):
        """初始化数据库表"""
        async with aiosqlite.connect(self.db_path) as db:
            # 相册表
            await db.execute('''
                CREATE TABLE IF NOT EXISTS albums (
                    album_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    album_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'creating',
                    created_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    expire_at TIMESTAMP NOT NULL,
                    url_path TEXT,
                    access_token TEXT UNIQUE
                )
            ''')
            
            # 媒体组表（每组最多10个媒体）
            await db.execute('''
                CREATE TABLE IF NOT EXISTS media_groups (
                    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id TEXT NOT NULL,
                    group_number INTEGER NOT NULL,
                    media_count INTEGER NOT NULL DEFAULT 0,
                    photo_count INTEGER NOT NULL DEFAULT 0,
                    video_count INTEGER NOT NULL DEFAULT 0,
                    text_content TEXT,
                    collected_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (album_id) REFERENCES albums(album_id)
                )
            ''')
            
            # 媒体文件表
            await db.execute('''
                CREATE TABLE IF NOT EXISTS media_files (
                    media_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    caption TEXT,
                    timestamp TIMESTAMP NOT NULL,
                    FOREIGN KEY (group_id) REFERENCES media_groups(group_id)
                )
            ''')
            
            # 用户授权表
            await db.execute('''
                CREATE TABLE IF NOT EXISTS user_authorizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE,
                    authorized_by INTEGER NOT NULL,
                    start_date TIMESTAMP NOT NULL,
                    expire_date TIMESTAMP NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP NOT NULL,
                    reminder_sent INTEGER NOT NULL DEFAULT 0
                )
            ''')
            
            # 媒体缓冲区表（持久化队列，防止Bot重启丢失）
            await db.execute('''
                CREATE TABLE IF NOT EXISTS media_buffer (
                    buffer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    album_id TEXT NOT NULL,
                    media_json TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (album_id) REFERENCES albums(album_id)
                )
            ''')
            
            # 创建索引
            await db.execute('CREATE INDEX IF NOT EXISTS idx_album_user ON albums(user_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_album_status ON albums(status)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_media_group ON media_files(group_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_album_expire ON albums(expire_at)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_auth_user ON user_authorizations(user_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_auth_expire ON user_authorizations(expire_date)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_auth_active ON user_authorizations(is_active)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_buffer_user_album ON media_buffer(user_id, album_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_buffer_created ON media_buffer(created_at)')
            
            await db.commit()
    
    async def create_album(self, album_id: str, user_id: int, album_name: str) -> bool:
        """创建新相册"""
        try:
            import secrets
            expire_at = datetime.now() + timedelta(days=config.ALBUM_EXPIRE_DAYS)
            # 生成访问token
            access_token = secrets.token_urlsafe(32)
            
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    INSERT INTO albums (album_id, user_id, album_name, status, created_at, expire_at, access_token)
                    VALUES (?, ?, ?, 'creating', ?, ?, ?)
                ''', (album_id, user_id, album_name, datetime.now(), expire_at, access_token))
                await db.commit()
                return True
        except Exception as e:
            print(f"Error creating album: {e}")
            return False
    
    async def get_user_active_album(self, user_id: int) -> Optional[Dict]:
        """获取用户当前活跃的相册"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM albums 
                WHERE user_id = ? AND status = 'creating'
                ORDER BY created_at DESC LIMIT 1
            ''', (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    async def add_media_group(self, album_id: str, group_number: int, 
                             media_items: List[Dict]) -> int:
        """添加媒体组（带事务和验证）"""
        import json
        import logging
        logger = logging.getLogger(__name__)
        
        async with aiosqlite.connect(self.db_path) as db:
            try:
                # 开启事务
                await db.execute('BEGIN TRANSACTION')
                
                # 统计媒体类型
                photo_count = sum(1 for m in media_items if m['type'] in ['photo', 'document'])
                video_count = sum(1 for m in media_items if m['type'] == 'video')
                text_content = None
                
                # 查找文字说明（通常在第一个或最后一个媒体）
                for item in media_items:
                    if item.get('caption'):
                        text_content = item['caption']
                        break
                
                # 插入媒体组
                cursor = await db.execute('''
                    INSERT INTO media_groups 
                    (album_id, group_number, media_count, photo_count, video_count, text_content, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (album_id, group_number, len(media_items), photo_count, video_count, 
                      text_content, datetime.now()))
                
                group_id = cursor.lastrowid
                
                # 插入媒体文件
                for idx, item in enumerate(media_items):
                    await db.execute('''
                        INSERT INTO media_files 
                        (group_id, file_id, file_type, sequence, caption, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (group_id, item['file_id'], item['type'], idx + 1, 
                          item.get('caption'), datetime.now()))
                
                # 提交事务
                await db.commit()
                
                # 写入后立即验证
                async with db.execute('''
                    SELECT group_id, media_count FROM media_groups WHERE group_id = ?
                ''', (group_id,)) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        logger.error(f"[DB] Media group write verification failed: group_id={group_id}")
                        raise Exception(f"Failed to verify media group {group_id}")
                    
                    # 验证媒体文件数量
                    async with db.execute('''
                        SELECT COUNT(*) FROM media_files WHERE group_id = ?
                    ''', (group_id,)) as count_cursor:
                        file_count = (await count_cursor.fetchone())[0]
                        if file_count != len(media_items):
                            logger.error(f"[DB] Media files count mismatch: expected {len(media_items)}, got {file_count}")
                            raise Exception(f"Media files count mismatch: {file_count} != {len(media_items)}")
                
                logger.info(f"[DB] Successfully saved media group {group_id} with {len(media_items)} files")
                return group_id
                
            except Exception as e:
                # 回滚事务
                await db.rollback()
                logger.error(f"[DB] Failed to save media group: {e}")
                import traceback
                traceback.print_exc()
                raise
    
    async def get_album_groups(self, album_id: str) -> List[Dict]:
        """获取相册的所有媒体组"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM media_groups 
                WHERE album_id = ?
                ORDER BY group_number ASC
            ''', (album_id,)) as cursor:
                groups = await cursor.fetchall()
                return [dict(group) for group in groups]
    
    async def get_group_media(self, group_id: int) -> List[Dict]:
        """获取媒体组的所有媒体文件"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM media_files 
                WHERE group_id = ?
                ORDER BY sequence ASC
            ''', (group_id,)) as cursor:
                media = await cursor.fetchall()
                return [dict(m) for m in media]
    
    async def complete_album(self, album_id: str, url_path: str) -> bool:
        """完成相册创建"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE albums 
                SET status = 'completed', completed_at = ?, url_path = ?
                WHERE album_id = ?
            ''', (datetime.now(), url_path, album_id))
            await db.commit()
            return True
    
    async def get_album_by_token(self, access_token: str) -> Optional[Dict]:
        """通过访问token获取相册信息"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM albums WHERE access_token = ?
            ''', (access_token,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    async def verify_album_access(self, album_id: str, access_token: str) -> bool:
        """验证相册访问权限"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('''
                SELECT access_token FROM albums WHERE album_id = ?
            ''', (album_id,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0] == access_token:
                    return True
                return False
    
    async def get_album_info(self, album_id: str) -> Optional[Dict]:
        """获取相册信息"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT album_id, user_id, album_name, status, created_at, completed_at, expire_at, url_path, access_token
                FROM albums WHERE album_id = ?
            ''', (album_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    async def get_album_full_data(self, album_id: str) -> Optional[Dict]:
        """获取相册完整数据（包括所有媒体组和文件）"""
        album = await self.get_album_info(album_id)
        if not album:
            print(f"[DEBUG] 相册 {album_id} 不存在")
            return None
        
        groups = await self.get_album_groups(album_id)
        print(f"[DEBUG] 相册 {album_id} 找到 {len(groups)} 个媒体组")
        
        for group in groups:
            media = await self.get_group_media(group['group_id'])
            group['media'] = media
            print(f"[DEBUG] 组 {group['group_number']} 包含 {len(media)} 个媒体文件")
        
        album['groups'] = groups
        return album
    
    async def delete_album(self, album_id: str, user_id: int) -> bool:
        """删除指定相册（需要验证用户权限）- 使用事务保护"""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute('BEGIN TRANSACTION')
                
                # 验证相册属于该用户
                async with db.execute('''
                    SELECT user_id FROM albums WHERE album_id = ?
                ''', (album_id,)) as cursor:
                    row = await cursor.fetchone()
                    if not row or row[0] != user_id:
                        await db.rollback()
                        return False
                
                # 获取所有组ID
                async with db.execute('''
                    SELECT group_id FROM media_groups WHERE album_id = ?
                ''', (album_id,)) as cursor:
                    group_ids = await cursor.fetchall()
                
                # 删除媒体文件
                for (group_id,) in group_ids:
                    await db.execute('DELETE FROM media_files WHERE group_id = ?', (group_id,))
                
                # 删除媒体组
                await db.execute('DELETE FROM media_groups WHERE album_id = ?', (album_id,))
                
                # 删除media_buffer
                await db.execute('DELETE FROM media_buffer WHERE album_id = ?', (album_id,))
                
                # 删除相册
                await db.execute('DELETE FROM albums WHERE album_id = ?', (album_id,))
                
                await db.commit()
                return True
            except Exception as e:
                await db.rollback()
                print(f"Error deleting album: {e}")
                return False
    
    async def cleanup_expired_albums(self) -> int:
        """清理过期相册（3天后自动焚毁，只清理completed状态的相册）"""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute('BEGIN TRANSACTION')
                
                # 获取过期的已完成相册ID（只清理completed状态，避免误删creating状态）
                async with db.execute('''
                    SELECT album_id FROM albums 
                    WHERE expire_at < ? AND status = 'completed'
                ''', (datetime.now(),)) as cursor:
                    expired_albums = await cursor.fetchall()
                
                if not expired_albums:
                    await db.commit()
                    return 0
                
                # 删除相关媒体组和文件
                for (album_id,) in expired_albums:
                    # 获取所有组ID
                    async with db.execute('''
                        SELECT group_id FROM media_groups WHERE album_id = ?
                    ''', (album_id,)) as cursor:
                        group_ids = await cursor.fetchall()
                    
                    # 删除媒体文件
                    for (group_id,) in group_ids:
                        await db.execute('DELETE FROM media_files WHERE group_id = ?', (group_id,))
                    
                    # 删除媒体组
                    await db.execute('DELETE FROM media_groups WHERE album_id = ?', (album_id,))
                    
                    # 删除媒体缓冲区
                    await db.execute('DELETE FROM media_buffer WHERE album_id = ?', (album_id,))
                
                # 删除相册（只删除completed状态的）
                await db.execute('''
                    DELETE FROM albums 
                    WHERE expire_at < ? AND status = 'completed'
                ''', (datetime.now(),))
                
                await db.commit()
                return len(expired_albums)
            except Exception as e:
                await db.rollback()
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"[DB] Failed to cleanup expired albums: {e}")
                raise
    
    async def authorize_user(self, user_id: int, authorized_by: int, months: int) -> bool:
        """授权用户使用相册功能"""
        try:
            start_date = datetime.now()
            expire_date = start_date + timedelta(days=months * 30)
            
            async with aiosqlite.connect(self.db_path) as db:
                # 检查是否已有授权记录
                async with db.execute('''
                    SELECT id FROM user_authorizations WHERE user_id = ?
                ''', (user_id,)) as cursor:
                    existing = await cursor.fetchone()
                
                if existing:
                    # 更新现有授权
                    await db.execute('''
                        UPDATE user_authorizations 
                        SET authorized_by = ?, start_date = ?, expire_date = ?, 
                            is_active = 1, reminder_sent = 0, created_at = ?
                        WHERE user_id = ?
                    ''', (authorized_by, start_date, expire_date, datetime.now(), user_id))
                else:
                    # 创建新授权
                    await db.execute('''
                        INSERT INTO user_authorizations 
                        (user_id, authorized_by, start_date, expire_date, is_active, created_at, reminder_sent)
                        VALUES (?, ?, ?, ?, 1, ?, 0)
                    ''', (user_id, authorized_by, start_date, expire_date, datetime.now()))
                
                await db.commit()
                return True
        except Exception as e:
            print(f"Error authorizing user: {e}")
            return False
    
    async def check_user_authorization(self, user_id: int) -> bool:
        """检查用户是否有有效授权"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('''
                SELECT id FROM user_authorizations 
                WHERE user_id = ? AND is_active = 1 AND expire_date > ?
            ''', (user_id, datetime.now())) as cursor:
                result = await cursor.fetchone()
                return result is not None
    
    async def get_user_authorization(self, user_id: int) -> Optional[Dict]:
        """获取用户授权信息"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM user_authorizations 
                WHERE user_id = ? AND is_active = 1
                ORDER BY expire_date DESC LIMIT 1
            ''', (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    async def get_all_authorizations(self) -> List[Dict]:
        """获取所有授权列表（用于超管查看）"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM user_authorizations 
                WHERE is_active = 1
                ORDER BY expire_date ASC
            ''') as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def revoke_authorization(self, user_id: int) -> bool:
        """撤销用户授权"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    UPDATE user_authorizations 
                    SET is_active = 0 
                    WHERE user_id = ?
                ''', (user_id,))
                await db.commit()
                return True
        except Exception as e:
            print(f"Error revoking authorization: {e}")
            return False
    
    async def get_expiring_authorizations(self, days_before: int = 1) -> List[Dict]:
        """获取即将到期的授权（用于提醒）"""
        expire_threshold = datetime.now() + timedelta(days=days_before)
        expire_end = datetime.now() + timedelta(days=days_before + 1)
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM user_authorizations 
                WHERE is_active = 1 
                AND expire_date >= ? 
                AND expire_date < ?
                AND reminder_sent = 0
            ''', (expire_threshold, expire_end)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def save_media_to_buffer(self, user_id: int, album_id: str, media_item: Dict) -> bool:
        """保存媒体到持久化缓冲区（防止Bot重启丢失）"""
        import json
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    INSERT INTO media_buffer (user_id, album_id, media_json, created_at)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, album_id, json.dumps(media_item), datetime.now()))
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"[DB] Failed to save media to buffer: {e}")
            return False
    
    async def get_media_buffer(self, user_id: int, album_id: str) -> List[Dict]:
        """获取用户的媒体缓冲区（按创建时间排序）"""
        import json
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('''
                SELECT media_json FROM media_buffer 
                WHERE user_id = ? AND album_id = ?
                ORDER BY created_at ASC
            ''', (user_id, album_id)) as cursor:
                rows = await cursor.fetchall()
                return [json.loads(row[0]) for row in rows]
    
    async def clear_media_buffer(self, user_id: int, album_id: str) -> bool:
        """清空用户的媒体缓冲区"""
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    DELETE FROM media_buffer 
                    WHERE user_id = ? AND album_id = ?
                ''', (user_id, album_id))
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"[DB] Failed to clear media buffer: {e}")
            return False
    
    async def mark_reminder_sent(self, user_id: int) -> bool:
        """标记提醒已发送"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    UPDATE user_authorizations 
                    SET reminder_sent = 1 
                    WHERE user_id = ?
                ''', (user_id,))
                await db.commit()
                return True
        except Exception as e:
            print(f"Error marking reminder sent: {e}")
            return False

# 全局数据库实例
db = Database()

