import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, time as dt_time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import config
import database
import uuid
import qrcode
from io import BytesIO

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 用户会话状态管理（需要并发保护）
import threading
user_sessions = {}  # {user_id: {album_id, group_buffer, last_group_time, group_number}}
user_sessions_lock = threading.Lock()  # 保护并发访问

async def init_database():
    """初始化数据库"""
    await database.db.init_db()
    logger.info("Database initialized")

def is_admin(user_id: int) -> bool:
    """检查用户是否是超管"""
    return user_id in config.ADMIN_USER_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    try:
        user_id = update.effective_user.id
        logger.info(f"收到 /start 命令，用户ID: {user_id}")
        logger.info(f"当前超管列表: {config.ADMIN_USER_IDS}")
        logger.info(f"用户 {user_id} 是否是超管: {is_admin(user_id)}")
        
        keyboard = [
            [KeyboardButton("📸 创建新相册")],
            [KeyboardButton("📊 我的相册")]
        ]
        
        # 如果是超管，添加用户授权和群发消息按钮
        if is_admin(user_id):
            logger.info(f"用户 {user_id} 是超管，添加授权和群发按钮")
            keyboard.append([KeyboardButton("🔐 用户授权")])
            keyboard.append([KeyboardButton("📢 群发消息")])
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        # 内联按钮
        inline_keyboard = [
            [InlineKeyboardButton("ℹ️ 帮助", callback_data="show_help")],
            [InlineKeyboardButton("联系管理员", url="https://t.me/faziliaobot")]
        ]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)
        
        welcome_text = (
            "👋 欢迎使用媒体分享Bot！\n\n"
            "📸 创建新相册 - 开始收集媒体\n"
            "📊 我的相册 - 查看已创建的相册（含删除功能）\n\n"
        )
        
        if is_admin(user_id):
            welcome_text += "🔐 用户授权 - 管理用户授权（超管功能）\n"
            welcome_text += "📢 群发消息 - 向所有授权用户群发消息（超管功能）\n\n"
        
        welcome_text += f"💡 提示：相册将在{config.ALBUM_EXPIRE_DAYS}天后自动删除"
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup
        )
        
        await update.message.reply_text(
            "👇 需要帮助？点击下方按钮",
            reply_markup=inline_markup
        )
        logger.info(f"已发送欢迎消息给用户 {user_id}")
    except Exception as e:
        logger.error(f"处理 /start 命令时出错: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    await update.message.reply_text(
        "📖 使用说明：\n\n"
        "1️⃣ 点击「📸 创建新相册」或发送 /new_album [相册名称]\n"
        "2️⃣ 开始发送媒体（照片+文字或视频），10个为一组\n"
        "3️⃣ 每组发送完成后，Bot会自动确认收集情况\n"
        f"4️⃣ 继续发送下一组，最多{config.MAX_MEDIA_GROUPS}组\n"
        "5️⃣ 点击「✅ 确认收集完毕」生成网页和二维码\n\n"
        f"⚠️ 重要提示：\n"
        f"• 相册将在{config.ALBUM_EXPIRE_DAYS}天后自动删除\n"
        f"• 最多可上传{config.MAX_MEDIA_GROUPS}组媒体\n"
        f"• 可通过「📊 我的相册」查看和删除相册"
    )

async def new_album_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理「创建新相册」按钮"""
    user_id = update.effective_user.id
    
    # 检查用户授权（超管不需要授权）
    if not is_admin(user_id):
        has_auth = await database.db.check_user_authorization(user_id)
        if not has_auth:
            auth_info = await database.db.get_user_authorization(user_id)
            if auth_info:
                expire_date = datetime.fromisoformat(auth_info['expire_date'])
                await update.message.reply_text(
                    f"❌ 您的授权已过期！\n\n"
                    f"到期时间：{expire_date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    "请联系管理员续费授权。"
                )
            else:
                await update.message.reply_text(
                    "❌ 您尚未获得授权！\n\n"
                    "请联系管理员授权后使用相册功能。"
                )
            return
    
    await update.message.reply_text(
        "📝 请输入相册名称：\n\n"
        "例如：客户A资料、产品展示、活动照片"
    )

async def new_album_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /new_album 命令"""
    user_id = update.effective_user.id
    
    # 检查用户授权（超管不需要授权）
    if not is_admin(user_id):
        has_auth = await database.db.check_user_authorization(user_id)
        if not has_auth:
            auth_info = await database.db.get_user_authorization(user_id)
            if auth_info:
                expire_date = datetime.fromisoformat(auth_info['expire_date'])
                await update.message.reply_text(
                    f"❌ 您的授权已过期！\n\n"
                    f"到期时间：{expire_date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    "请联系管理员续费授权。"
                )
            else:
                await update.message.reply_text(
                    "❌ 您尚未获得授权！\n\n"
                    "请联系管理员授权后使用相册功能。"
                )
            return
    
    # 检查是否已有活跃相册（数据库中的）
    active_album = await database.db.get_user_active_album(user_id)
    
    # 也检查内存中的会话（使用锁保护）
    with user_sessions_lock:
        has_memory_session = user_id in user_sessions
    
    if active_album or has_memory_session:
        # 如果内存中有会话但数据库中没有，同步状态
        if has_memory_session and not active_album:
            # 内存会话可能已过期，清除它
            with user_sessions_lock:
                if user_id in user_sessions:
                    del user_sessions[user_id]
            active_album = None
        
        if active_album:
            # 获取已收集的组数
            groups = await database.db.get_album_groups(active_album['album_id'])
            group_count = len(groups)
            
            keyboard = [
                [InlineKeyboardButton("➡️ 继续当前相册", callback_data=f"continue_{active_album['album_id']}")],
                [InlineKeyboardButton("❌ 取消当前相册", callback_data=f"cancel_{active_album['album_id']}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"⚠️ 您已有一个进行中的相册：{active_album['album_name']}\n\n"
                f"📊 已收集 {group_count} 组媒体\n\n"
                "请选择继续或取消当前相册",
                reply_markup=reply_markup
            )
            return
    
    # 获取相册名称
    album_name = ' '.join(context.args) if context.args else update.message.text.replace("📸 创建新相册", "").strip()
    
    if not album_name or album_name == "📸 创建新相册":
        await update.message.reply_text("📝 请输入相册名称：\n\n例如：/new_album 客户A资料")
        return
    
    # 创建新相册
    album_id = str(uuid.uuid4())
    success = await database.db.create_album(album_id, user_id, album_name)
    
    if success:
        # 使用锁保护创建session
        with user_sessions_lock:
            user_sessions[user_id] = {
                'album_id': album_id,
                'group_buffer': [],
                'last_group_time': None,
                'group_number': 0,
                'collecting_task': None
            }
        
        # 显示操作按钮（使用内联按钮）
        inline_keyboard = [
            [InlineKeyboardButton("✅ 确认收集完毕", callback_data=f"finish_album_{album_id}")],
            [InlineKeyboardButton("❌ 取消相册", callback_data=f"cancel_album_{album_id}")],
            [InlineKeyboardButton("📊 查看进度", callback_data=f"show_progress_{album_id}")]
        ]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)
        
        await update.message.reply_text(
            f"✅ 相册「{album_name}」创建成功！\n\n"
            "📸 现在可以开始发送媒体了\n"
            "💡 支持照片、视频和文字说明，10个为一组",
            reply_markup=inline_markup
        )
    else:
        await update.message.reply_text("❌ 创建相册失败，请稍后重试")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理媒体消息"""
    user_id = update.effective_user.id
    
    # 检查是否在群发模式
    if hasattr(context, 'user_data') and user_id in context.user_data:
        user_data = context.user_data[user_id]
        if user_data.get('broadcast_mode'):
            # 群发模式：保存媒体消息
            message_type = 'photo' if update.message.photo else 'video' if update.message.video else 'document'
            file_id = None
            caption = update.message.caption or ''
            
            if update.message.photo:
                file_id = update.message.photo[-1].file_id
            elif update.message.video:
                file_id = update.message.video.file_id
            elif update.message.document:
                file_id = update.message.document.file_id
            
            if 'broadcast_messages' not in user_data:
                user_data['broadcast_messages'] = []
            
            user_data['broadcast_messages'].append({
                'type': message_type,
                'file_id': file_id,
                'caption': caption,
                'message_id': update.message.message_id
            })
            
            # 显示操作按钮
            keyboard = [
                [InlineKeyboardButton("✅ 确认群发", callback_data="broadcast_preview")],
                [InlineKeyboardButton("❌ 取消群发", callback_data="broadcast_cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ 已添加 {message_type} 到群发列表\n\n"
                f"当前已有 {len(user_data['broadcast_messages'])} 条消息\n\n"
                "继续发送或点击「✅ 确认群发」预览",
                reply_markup=reply_markup
            )
            return
    
    # 检查用户授权（超管不需要授权）
    if not is_admin(user_id):
        has_auth = await database.db.check_user_authorization(user_id)
        if not has_auth:
            await update.message.reply_text(
                "❌ 您尚未获得授权或授权已过期！\n\n"
                "请联系管理员授权后使用相册功能。"
            )
            return
    
    # 检查是否有活跃会话（内存中）- 使用锁保护
    with user_sessions_lock:
        has_session = user_id in user_sessions
    
    if not has_session:
        # 如果内存中没有会话，检查数据库中是否有活跃相册
        active_album = await database.db.get_user_active_album(user_id)
        if active_album:
            # 自动恢复会话
            album_id = active_album['album_id']
            groups = await database.db.get_album_groups(album_id)
            group_count = len(groups)
            
            # 从 media_buffer 恢复未处理的媒体
            buffered_media = await database.db.get_media_buffer(user_id, album_id)
            
            with user_sessions_lock:
                user_sessions[user_id] = {
                    'album_id': album_id,
                    'group_buffer': buffered_media,  # 恢复缓冲的媒体
                    'last_group_time': None,
                    'group_number': group_count,
                    'collecting_task': None
                }
            
            logger.info(f"自动恢复用户 {user_id} 的相册会话: {active_album['album_name']}")
            if buffered_media:
                logger.info(f"从 buffer 恢复了 {len(buffered_media)} 个未处理的媒体")
        else:
            # 既没有内存会话，也没有数据库中的活跃相册
            await update.message.reply_text(
                "⚠️ 请先创建相册！\n\n"
                "点击「📸 创建新相册」或发送 /new_album [相册名称]"
            )
            return
    
    # 获取session和album_id（使用锁保护）
    with user_sessions_lock:
        if user_id not in user_sessions:
            await update.message.reply_text("❌ 会话已失效，请重新创建相册")
            return
        session = user_sessions[user_id]
        album_id = session['album_id']
    
    # 检查相册状态
    album = await database.db.get_album_info(album_id)
    if not album or album['status'] != 'creating':
        await update.message.reply_text("❌ 相册不存在或已完成，请创建新相册")
        with user_sessions_lock:
            if user_id in user_sessions:
                del user_sessions[user_id]
        return
    
    # 处理不同类型的媒体
    media_item = None
    
    if update.message.photo:
        photo = update.message.photo[-1]  # 获取最大尺寸的照片
        media_item = {
            'file_id': photo.file_id,
            'type': 'photo',
            'caption': update.message.caption
        }
    elif update.message.video:
        video = update.message.video
        media_item = {
            'file_id': video.file_id,
            'type': 'video',
            'caption': update.message.caption
        }
    elif update.message.document:
        # 处理文档（可能是图片）
        doc = update.message.document
        if doc.mime_type and doc.mime_type.startswith('image/'):
            media_item = {
                'file_id': doc.file_id,
                'type': 'photo',
                'caption': update.message.caption
            }
    
    if not media_item:
        await update.message.reply_text("⚠️ 暂不支持此类型媒体，请发送照片或视频")
        return
    
    # 保存媒体到持久化 buffer（防止 Bot 重启丢失）
    save_success = await database.db.save_media_to_buffer(user_id, album_id, media_item)
    if save_success:
        logger.info(f"媒体已保存到 buffer: user={user_id}, album={album_id}")
    else:
        logger.error(f"媒体保存到 buffer 失败: user={user_id}")
    
    # 处理媒体组（Telegram 自动分组的媒体）
    media_group_id = update.message.media_group_id
    
    # 使用锁保护 session 操作
    with user_sessions_lock:
        if user_id not in user_sessions:
            logger.warning(f"用户 {user_id} 的session在处理媒体时丢失")
            return
        session = user_sessions[user_id]
    
    if media_group_id:
        # 如果有媒体组ID，添加到组缓冲区
        with user_sessions_lock:
            if 'media_groups' not in session:
                session['media_groups'] = {}
            
            if media_group_id not in session['media_groups']:
                session['media_groups'][media_group_id] = {
                    'items': [],
                    'last_time': datetime.now()
                }
            
            session['media_groups'][media_group_id]['items'].append(media_item)
            session['media_groups'][media_group_id]['last_time'] = datetime.now()
        
        # 延迟处理媒体组（等待组内所有媒体）
        with user_sessions_lock:
            if f'group_task_{media_group_id}' in session:
                session[f'group_task_{media_group_id}'].cancel()
        
        async def delayed_process_group():
            await asyncio.sleep(config.COLLECTION_DELAY_SECONDS)
            if user_id not in user_sessions:
                return
            session = user_sessions[user_id]
            if media_group_id in session.get('media_groups', {}):
                group_items = session['media_groups'][media_group_id]['items']
                if group_items:
                    # 将组内所有媒体添加到缓冲区
                    session['group_buffer'].extend(group_items)
                    del session['media_groups'][media_group_id]
            
            # 触发处理（不管多少个）
            if user_id in user_sessions and user_sessions[user_id].get('group_buffer'):
                await process_group(user_id, user_sessions[user_id], context)
        
        with user_sessions_lock:
            session[f'group_task_{media_group_id}'] = asyncio.create_task(delayed_process_group())
    else:
        # 单个媒体，直接添加到缓冲区
        with user_sessions_lock:
            if user_id not in user_sessions:
                return
            session = user_sessions[user_id]
            session['group_buffer'].append(media_item)
            session['last_group_time'] = datetime.now()
            buffer_size = len(session['group_buffer'])
        
        # 设置延迟任务（任意数量都可以成组，不需要等到10个）
        logger.info(f"设置延迟任务，当前: {buffer_size}个媒体")
        
        # 取消旧任务
        if user_id in user_sessions and user_sessions[user_id].get('collecting_task'):
            user_sessions[user_id]['collecting_task'].cancel()
        
        # 创建新的延迟任务（简化，不使用锁）
        async def delayed_process():
            try:
                logger.info(f"延迟 {config.COLLECTION_DELAY_SECONDS} 秒...")
                await asyncio.sleep(config.COLLECTION_DELAY_SECONDS)
                logger.info(f"延迟时间到！检查用户 {user_id}")
                
                # 检查session是否还存在
                if user_id not in user_sessions:
                    logger.warning(f"用户 {user_id} session已失效")
                    return
                
                # 检查buffer是否有数据
                if not user_sessions[user_id].get('group_buffer'):
                    logger.warning(f"用户 {user_id} buffer为空")
                    return
                
                logger.info(f"触发处理！用户 {user_id}")
                await process_group(user_id, user_sessions[user_id], context)
                
            except asyncio.CancelledError:
                logger.info(f"延迟任务被取消（用户 {user_id}）")
            except Exception as e:
                logger.error(f"延迟任务错误: {e}")
                import traceback
                traceback.print_exc()
        
        # 创建任务并保存
        task = asyncio.create_task(delayed_process())
        if user_id in user_sessions:
            user_sessions[user_id]['collecting_task'] = task
            logger.info(f"✓ 延迟任务已创建并保存")

async def process_group(user_id: int, session: dict, context: ContextTypes.DEFAULT_TYPE):
    """处理一组媒体（简化版，无复杂锁）"""
    try:
        if not session.get('group_buffer'):
            logger.warning(f"用户 {user_id} 缓冲区为空")
            return
        
        album_id = session['album_id']
        buffer_count = len(session['group_buffer'])
        logger.info(f"处理用户 {user_id} 的 {buffer_count} 个媒体")
        
        # 检查组数限制
        existing_groups = await database.db.get_album_groups(album_id)
        if len(existing_groups) >= config.MAX_MEDIA_GROUPS:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ 已达到最大组数限制（{config.MAX_MEDIA_GROUPS}组）"
            )
            return
        
        # 更新组号
        session['group_number'] += 1
        group_number = session['group_number']
        media_to_save = session['group_buffer'][:]  # 复制列表
        
        logger.info(f"保存第 {group_number} 组，{len(media_to_save)} 个媒体")
        
        # 保存到数据库
        group_id = await database.db.add_media_group(
            album_id, group_number, media_to_save
        )
        logger.info(f"✓ 保存成功 group_id={group_id}")
        
        # 统计
        photo_count = sum(1 for m in media_to_save if m.get('type') == 'photo')
        video_count = sum(1 for m in media_to_save if m.get('type') == 'video')
        
        # 发送确认消息
        status_text = f"✅ 已收集第 {group_number} 组\n\n📊 {photo_count}照片"
        if video_count > 0:
            status_text += f", {video_count}视频"
        
        inline_keyboard = [
            [InlineKeyboardButton("✅ 确认收集完毕", callback_data=f"finish_album_{album_id}")],
            [InlineKeyboardButton("❌ 取消相册", callback_data=f"cancel_album_{album_id}")]
        ]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)
        
        await context.bot.send_message(
            chat_id=user_id,
            text=status_text,
            reply_markup=inline_markup
        )
        logger.info(f"✓ 确认消息已发送")
        
        # 清空缓冲区
        session['group_buffer'] = []
        session['last_group_time'] = None
        
        # 清空数据库buffer
        await database.db.clear_media_buffer(user_id, album_id)
        logger.info(f"✓ Buffer已清空")
        
    except Exception as e:
        logger.error(f"处理媒体组失败: {e}")
        import traceback
        traceback.print_exc()

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理文本消息"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # 检查是否正在等待输入用户ID（授权流程）
    if hasattr(context, 'user_data') and user_id in context.user_data:
        user_data = context.user_data[user_id]
        if user_data.get('waiting_for_user_id'):
            # 尝试解析用户ID
            try:
                target_user_id = int(text.strip())
                months = user_data.get('auth_months', 1)
                
                # 执行授权
                success = await database.db.authorize_user(target_user_id, user_id, months)
                
                if success:
                    expire_date = datetime.now() + timedelta(days=months * 30)
                    await update.message.reply_text(
                        f"✅ 授权成功！\n\n"
                        f"用户ID：{target_user_id}\n"
                        f"授权时长：{months}个月\n"
                        f"到期时间：{expire_date.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                else:
                    await update.message.reply_text("❌ 授权失败，请稍后重试")
                
                # 清除等待状态
                del context.user_data[user_id]
                return
            except ValueError:
                await update.message.reply_text(
                    "❌ 请输入有效的用户ID（数字）\n\n"
                    "💡 用户可以通过 @userinfobot 获取自己的ID"
                )
                return
        elif user_data.get('waiting_for_check_user_id'):
            # 查询用户授权状态
            try:
                target_user_id = int(text.strip())
                auth_info = await database.db.get_user_authorization(target_user_id)
                
                if auth_info:
                    expire_date = datetime.fromisoformat(auth_info['expire_date'])
                    start_date = datetime.fromisoformat(auth_info['start_date'])
                    days_left = (expire_date - datetime.now()).days
                    
                    status = "✅ 有效" if days_left > 0 else "❌ 已过期"
                    await update.message.reply_text(
                        f"👤 用户ID: {target_user_id}\n"
                        f"状态: {status}\n"
                        f"开始时间: {start_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"到期时间: {expire_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"剩余天数: {days_left}天"
                    )
                else:
                    await update.message.reply_text(
                        f"❌ 用户 {target_user_id} 没有授权记录"
                    )
                
                # 清除等待状态
                del context.user_data[user_id]
                return
            except ValueError:
                await update.message.reply_text(
                    "❌ 请输入有效的用户ID（数字）\n\n"
                    "💡 用户可以通过 @userinfobot 获取自己的ID"
                )
                return
        elif user_data.get('waiting_for_revoke_user_id'):
            # 取消用户授权
            try:
                target_user_id = int(text.strip())
                success = await database.db.revoke_authorization(target_user_id)
                
                if success:
                    await update.message.reply_text(
                        f"✅ 已取消用户 {target_user_id} 的授权"
                    )
                else:
                    await update.message.reply_text(
                        f"❌ 取消授权失败，用户 {target_user_id} 可能没有授权记录"
                    )
                
                # 清除等待状态
                del context.user_data[user_id]
                return
            except ValueError:
                await update.message.reply_text(
                    "❌ 请输入有效的用户ID（数字）\n\n"
                    "💡 用户可以通过 @userinfobot 获取自己的ID"
                )
                return
        elif user_data.get('broadcast_mode'):
            # 群发消息模式：保存文本消息
            if 'broadcast_messages' not in user_data:
                user_data['broadcast_messages'] = []
            
            user_data['broadcast_messages'].append({
                'type': 'text',
                'text': text,
                'message_id': update.message.message_id
            })
            
            # 显示操作按钮
            keyboard = [
                [InlineKeyboardButton("✅ 确认群发", callback_data="broadcast_preview")],
                [InlineKeyboardButton("❌ 取消群发", callback_data="broadcast_cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ 已添加文本消息到群发列表\n\n"
                f"当前已有 {len(user_data['broadcast_messages'])} 条消息\n\n"
                "继续发送或点击「✅ 确认群发」预览",
                reply_markup=reply_markup
            )
            return
    
    if text == "📸 创建新相册":
        await new_album_button(update, context)
    # 以下按钮已改为内联按钮，保留键盘按钮的处理以兼容旧消息
    elif text == "✅ 确认收集完毕":
        # 如果有活跃会话，处理完成
        with user_sessions_lock:
            has_session = user_id in user_sessions
        if has_session:
            await finish_album(update, context)
        else:
            await update.message.reply_text("⚠️ 没有进行中的相册")
    elif text == "❌ 取消相册":
        await cancel_album(update, context)
    elif text == "📊 查看进度":
        await show_progress(update, context)
    elif text == "📊 我的相册":
        await show_my_albums(update, context)
    elif text == "🔐 用户授权":
        await admin_auth_button(update, context)
    elif text == "📢 群发消息":
        await broadcast_message_button(update, context)
    else:
        # 使用锁保护检查
        with user_sessions_lock:
            has_session = user_id in user_sessions
        
        if not has_session:
            # 如果没有活跃会话，将文本作为相册名称处理
            if text and text.strip() and text not in ["📸 创建新相册", "📊 我的相册", "🔐 用户授权", "📢 群发消息"]:
                # 模拟命令参数
                context.args = text.strip().split()
                await new_album_command(update, context)
            else:
                # 内联按钮
                inline_keyboard = [
                    [InlineKeyboardButton("ℹ️ 帮助", callback_data="show_help")]
                ]
                inline_markup = InlineKeyboardMarkup(inline_keyboard)
                await update.message.reply_text("💡 需要帮助？点击下方按钮", reply_markup=inline_markup)
        else:
            # 有活跃会话时，文本可能是说明文字，忽略或提示
            await update.message.reply_text("💡 当前正在收集媒体，请发送照片或视频")

async def finish_album(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """完成相册收集"""
    user_id = update.effective_user.id
    
    # 使用锁保护检查和获取session
    with user_sessions_lock:
        if user_id not in user_sessions:
            await update.message.reply_text("⚠️ 没有进行中的相册")
            return
        session = user_sessions[user_id].copy()
    album_id = session['album_id']
    
    # 处理剩余的媒体组
    if 'media_groups' in session:
        for media_group_id, group_data in list(session['media_groups'].items()):
            if group_data['items']:
                session['group_buffer'].extend(group_data['items'])
        session['media_groups'] = {}
    
    # 处理剩余的缓冲区
    if session['group_buffer']:
        await process_group(user_id, session, context)
    
    # 检查是否有媒体
    groups = await database.db.get_album_groups(album_id)
    if not groups:
        await update.message.reply_text("⚠️ 相册中没有媒体，请先发送媒体")
        return
    
    # 获取相册的访问token
    album_info = await database.db.get_album_info(album_id)
    if not album_info or not album_info.get('access_token'):
        await update.message.reply_text("❌ 获取相册访问信息失败")
        return
    
    access_token = album_info['access_token']
    
    # 生成URL路径（包含访问token）
    url_path = f"/album/{album_id}"
    await database.db.complete_album(album_id, url_path)
    
    # 生成二维码（包含访问token）
    full_url = f"{config.DOMAIN}{url_path}?token={access_token}"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(full_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format='PNG')
    bio.seek(0)
    
    # 发送结果
    keyboard = [
        [KeyboardButton("📸 创建新相册")],
        [KeyboardButton("📊 我的相册")]
    ]
    
    # 如果是超管，添加超管按钮
    if is_admin(user_id):
        keyboard.append([KeyboardButton("🔐 用户授权")])
        keyboard.append([KeyboardButton("📢 群发消息")])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_photo(
        photo=bio,
        caption=f"✅ 相册创建完成！\n\n"
                f"🔗 链接：{full_url}\n\n"
                f"📱 在微信中发送二维码图片即可分享给客户",
        reply_markup=reply_markup
    )
    
    # 清理会话和buffer
    await database.db.clear_media_buffer(user_id, album_id)
    with user_sessions_lock:
        if user_id in user_sessions:
            del user_sessions[user_id]

async def cancel_album(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消相册"""
    user_id = update.effective_user.id
    
    # 使用锁保护检查
    with user_sessions_lock:
        has_session = user_id in user_sessions
    
    if not has_session:
        await update.message.reply_text("⚠️ 没有进行中的相册")
        return
    
    session = user_sessions[user_id]
    album_id = session['album_id']
    
    keyboard = [
        [InlineKeyboardButton("✅ 确认取消", callback_data=f"confirm_cancel_{album_id}")],
        [InlineKeyboardButton("❌ 不取消", callback_data="no_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "⚠️ 确认要取消当前相册吗？\n\n"
        "取消后，已收集的媒体将无法恢复",
        reply_markup=reply_markup
    )

async def show_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示收集进度"""
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text("⚠️ 没有进行中的相册")
        return
    
    session = user_sessions[user_id]
    album_id = session['album_id']
    
    album = await database.db.get_album_info(album_id)
    groups = await database.db.get_album_groups(album_id)
    
    progress_text = f"📊 相册进度：{album['album_name']}\n\n"
    progress_text += f"✅ 已收集 {len(groups)}/{config.MAX_MEDIA_GROUPS} 组媒体\n"
    
    if session['group_buffer']:
        progress_text += f"📝 当前组：{len(session['group_buffer'])} 个媒体\n"
    
    total_media = sum(g['media_count'] for g in groups) + len(session['group_buffer'])
    progress_text += f"📸 总计：{total_media} 个媒体文件\n\n"
    progress_text += f"⏰ 将在{config.ALBUM_EXPIRE_DAYS}天后自动删除"
    
    await update.message.reply_text(progress_text)

async def show_my_albums(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示用户的相册列表"""
    user_id = update.effective_user.id
    
    # 获取用户的所有相册（包括已完成和进行中的）
    async def get_user_albums():
        import aiosqlite
        async with aiosqlite.connect(database.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT album_id, album_name, status, created_at, completed_at, expire_at
                FROM albums 
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 20
            ''', (user_id,)) as cursor:
                albums = await cursor.fetchall()
                return [dict(album) for album in albums]
    
    albums = await get_user_albums()
    
    if not albums:
        await update.message.reply_text("📊 您还没有创建任何相册")
        return
    
    # 组织相册列表
    text = "📊 您的相册列表：\n\n"
    keyboard_buttons = []
    
    for album in albums[:10]:  # 最多显示10个
        status_icon = "✅" if album['status'] == 'completed' else "🔄"
        status_text = "已完成" if album['status'] == 'completed' else "进行中"
        
        from datetime import datetime
        created = datetime.fromisoformat(album['created_at']) if isinstance(album['created_at'], str) else album['created_at']
        created_str = created.strftime("%m-%d %H:%M")
        
        text += f"{status_icon} {album['album_name']}\n"
        text += f"   状态：{status_text} | 创建：{created_str}\n"
        
        if album['status'] == 'completed':
            # 获取访问token
            album_full = await database.db.get_album_info(album['album_id'])
            if album_full and album_full.get('access_token'):
                token = album_full['access_token']
                text += f"   链接：{config.DOMAIN}/album/{album['album_id']}?token={token}\n"
            else:
                text += f"   链接：{config.DOMAIN}/album/{album['album_id']}\n"
        
        text += "\n"
        
        # 添加删除按钮
        keyboard_buttons.append([
            InlineKeyboardButton(
                f"🗑️ 删除「{album['album_name']}」",
                callback_data=f"delete_album_{album['album_id']}"
            )
        ])
    
    if len(albums) > 10:
        text += f"\n...还有 {len(albums) - 10} 个相册"
    
    reply_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None
    
    await update.message.reply_text(text, reply_markup=reply_markup)

async def admin_auth_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理「用户授权」键盘按钮"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ 您没有权限使用此功能")
        return
    
    # 显示内联按钮选择具体操作
    keyboard = [
        [InlineKeyboardButton("👤 授权用户", callback_data="admin_authorize")],
        [InlineKeyboardButton("📋 查看授权列表", callback_data="admin_list")],
        [InlineKeyboardButton("🔍 查询用户授权", callback_data="admin_check")],
        [InlineKeyboardButton("❌ 取消用户授权", callback_data="admin_revoke")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔐 用户授权管理\n\n"
        "请选择操作：",
        reply_markup=reply_markup
    )

async def broadcast_message_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理「群发消息」键盘按钮"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ 您没有权限使用此功能")
        return
    
    # 设置群发模式
    if not hasattr(context, 'user_data'):
        context.user_data = {}
    context.user_data[user_id] = {
        'broadcast_mode': True,
        'broadcast_messages': []
    }
    
    # 显示操作按钮
    keyboard = [
        [InlineKeyboardButton("✅ 确认群发", callback_data="broadcast_preview")],
        [InlineKeyboardButton("❌ 取消群发", callback_data="broadcast_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📢 群发消息模式\n\n"
        "请开始发送要群发的内容：\n"
        "• 支持照片、视频、文字\n"
        "• 可以发送多条消息\n"
        "• 发送完成后，点击「✅ 确认群发」按钮预览并发送\n\n"
        "💡 提示：消息将发送给所有有有效授权的用户",
        reply_markup=reply_markup
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """超管命令入口（保留命令形式）"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ 您没有权限使用此命令")
        return
    
    # 显示内联按钮选择具体操作
    keyboard = [
        [InlineKeyboardButton("👤 授权用户", callback_data="admin_authorize")],
        [InlineKeyboardButton("📋 查看授权列表", callback_data="admin_list")],
        [InlineKeyboardButton("🔍 查询用户授权", callback_data="admin_check")],
        [InlineKeyboardButton("❌ 取消用户授权", callback_data="admin_revoke")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔐 用户授权管理\n\n"
        "请选择操作：",
        reply_markup=reply_markup
    )

async def admin_authorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """授权用户命令：/authorize <user_id> <months>"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ 您没有权限使用此命令")
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "📝 使用方法：\n"
            "/authorize <用户ID> <月数>\n\n"
            "示例：\n"
            "/authorize 123456789 1  （授权1个月）\n"
            "/authorize 123456789 3  （授权3个月）"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        months = int(context.args[1])
        
        if months not in [1, 3]:
            await update.message.reply_text("❌ 授权月数只能是 1 或 3")
            return
        
        success = await database.db.authorize_user(target_user_id, user_id, months)
        
        if success:
            expire_date = datetime.now() + timedelta(days=months * 30)
            await update.message.reply_text(
                f"✅ 授权成功！\n\n"
                f"用户ID：{target_user_id}\n"
                f"授权时长：{months}个月\n"
                f"到期时间：{expire_date.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        else:
            await update.message.reply_text("❌ 授权失败，请稍后重试")
    except ValueError:
        await update.message.reply_text("❌ 参数格式错误，用户ID和月数必须是数字")

async def admin_list_authorizations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看所有授权列表"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ 您没有权限使用此命令")
        return
    
    authorizations = await database.db.get_all_authorizations()
    
    if not authorizations:
        await update.message.reply_text("📋 当前没有有效的授权")
        return
    
    text = "📋 授权列表：\n\n"
    for auth in authorizations[:20]:  # 最多显示20个
        expire_date = datetime.fromisoformat(auth['expire_date'])
        start_date = datetime.fromisoformat(auth['start_date'])
        days_left = (expire_date - datetime.now()).days
        
        status = "✅" if days_left > 0 else "❌"
        text += f"{status} 用户ID: {auth['user_id']}\n"
        text += f"   开始: {start_date.strftime('%Y-%m-%d')}\n"
        text += f"   到期: {expire_date.strftime('%Y-%m-%d')}\n"
        text += f"   剩余: {days_left}天\n\n"
    
    if len(authorizations) > 20:
        text += f"\n...还有 {len(authorizations) - 20} 个授权"
    
    await update.message.reply_text(text)

async def admin_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询用户授权状态"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ 您没有权限使用此命令")
        return
    
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "📝 使用方法：\n"
            "/check_user <用户ID>\n\n"
            "示例：\n"
            "/check_user 123456789"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        auth_info = await database.db.get_user_authorization(target_user_id)
        
        if auth_info:
            expire_date = datetime.fromisoformat(auth_info['expire_date'])
            start_date = datetime.fromisoformat(auth_info['start_date'])
            days_left = (expire_date - datetime.now()).days
            
            status = "✅ 有效" if days_left > 0 else "❌ 已过期"
            await update.message.reply_text(
                f"👤 用户ID: {target_user_id}\n"
                f"状态: {status}\n"
                f"开始时间: {start_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"到期时间: {expire_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"剩余天数: {days_left}天"
            )
        else:
            await update.message.reply_text(
                f"❌ 用户 {target_user_id} 没有授权记录"
            )
    except ValueError:
        await update.message.reply_text("❌ 用户ID必须是数字")

async def delete_album(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除相册"""
    user_id = update.effective_user.id
    
    # 获取用户的所有相册
    async def get_user_albums():
        import aiosqlite
        async with aiosqlite.connect(database.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT album_id, album_name, status
                FROM albums 
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 20
            ''', (user_id,)) as cursor:
                albums = await cursor.fetchall()
                return [dict(album) for album in albums]
    
    albums = await get_user_albums()
    
    if not albums:
        await update.message.reply_text("📊 您还没有创建任何相册")
        return
    
    # 生成删除按钮
    keyboard_buttons = []
    for album in albums[:10]:
        keyboard_buttons.append([
            InlineKeyboardButton(
                f"🗑️ 删除「{album['album_name']}」",
                callback_data=f"delete_album_{album['album_id']}"
            )
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    
    await update.message.reply_text(
        "🗑️ 选择要删除的相册：\n\n"
        "⚠️ 删除后无法恢复！",
        reply_markup=reply_markup
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理内联按钮回调"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("confirm_cancel_"):
        album_id = data.replace("confirm_cancel_", "")
        user_id = query.from_user.id
        
        # 删除数据库中的相册
        success = await database.db.delete_album(album_id, user_id)
        
        if success:
            # 清除buffer
            await database.db.clear_media_buffer(user_id, album_id)
            
            # 如果内存中有会话，也清除
            with user_sessions_lock:
                if user_id in user_sessions:
                    if user_sessions[user_id].get('album_id') == album_id:
                        del user_sessions[user_id]
            
            keyboard = [
                [KeyboardButton("📸 创建新相册")],
                [KeyboardButton("📊 我的相册")]
            ]
            
            # 如果是超管，添加超管按钮
            if is_admin(user_id):
                keyboard.append([KeyboardButton("🔐 用户授权")])
                keyboard.append([KeyboardButton("📢 群发消息")])
            
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await query.edit_message_text("✅ 相册已取消并删除", reply_markup=None)
            await query.message.reply_text("✅ 相册已取消并删除", reply_markup=reply_markup)
        else:
            await query.edit_message_text("❌ 取消失败，相册不存在或无权限", reply_markup=None)
    elif data == "no_cancel":
        await query.edit_message_text("✅ 继续收集媒体...", reply_markup=None)
    
    elif data.startswith("finish_album_"):
        album_id = data.replace("finish_album_", "")
        user_id = query.from_user.id
        
        # 验证权限
        album = await database.db.get_album_info(album_id)
        if not album or album['user_id'] != user_id:
            await query.answer("❌ 无权限操作此相册", show_alert=True)
            return
        
        if user_id not in user_sessions or user_sessions[user_id]['album_id'] != album_id:
            await query.answer("❌ 会话已过期，请重新开始", show_alert=True)
            return
        
        # 模拟 finish_album 调用
        # 创建一个假的 update 对象
        class FakeUpdate:
            def __init__(self, query_obj):
                self.effective_user = query_obj.from_user
                self.message = query_obj.message
        
        fake_update = FakeUpdate(query)
        await finish_album(fake_update, context)
        await query.answer("✅ 相册已完成")
    
    elif data.startswith("show_progress_"):
        album_id = data.replace("show_progress_", "")
        user_id = query.from_user.id
        
        # 验证权限
        album = await database.db.get_album_info(album_id)
        if not album or album['user_id'] != user_id:
            await query.answer("❌ 无权限查看此相册", show_alert=True)
            return
        
        groups = await database.db.get_album_groups(album_id)
        group_count = len(groups)
        
        buffer_count = 0
        if user_id in user_sessions and user_sessions[user_id]['album_id'] == album_id:
            buffer_count = len(user_sessions[user_id]['group_buffer'])
        
        progress_text = f"📊 相册进度：{album['album_name']}\n\n"
        progress_text += f"✅ 已收集 {group_count}/{config.MAX_MEDIA_GROUPS} 组媒体\n"
        
        if buffer_count > 0:
            progress_text += f"📝 当前组：{buffer_count}/{10} 个媒体\n"
        
        total_media = sum(g['media_count'] for g in groups) + buffer_count
        progress_text += f"📸 总计：{total_media} 个媒体文件\n\n"
        progress_text += f"⏰ 将在{config.ALBUM_EXPIRE_DAYS}天后自动删除"
        
        await query.answer()
        await query.edit_message_text(progress_text)
    elif data.startswith("cancel_album_"):
        album_id = data.replace("cancel_album_", "")
        user_id = query.from_user.id
        
        # 验证相册属于该用户
        album = await database.db.get_album_info(album_id)
        if not album:
            await query.edit_message_text("❌ 相册不存在")
            return
        
        if album['user_id'] != user_id:
            await query.edit_message_text("❌ 无权限操作此相册")
            return
        
        if album['status'] != 'creating':
            await query.edit_message_text("❌ 相册已完成，无法取消")
            return
        
        keyboard = [
            [InlineKeyboardButton("✅ 确认取消", callback_data=f"confirm_cancel_{album_id}")],
            [InlineKeyboardButton("❌ 不取消", callback_data="no_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "⚠️ 确认要取消当前相册吗？\n\n"
            "取消后，已收集的媒体将无法恢复",
            reply_markup=reply_markup
        )
    elif data.startswith("cancel_"):
        # 处理取消按钮
        album_id = data.replace("cancel_", "")
        user_id = query.from_user.id
        
        # 确认取消
        keyboard = [
            [InlineKeyboardButton("✅ 确认取消", callback_data=f"confirm_cancel_{album_id}")],
            [InlineKeyboardButton("❌ 不取消", callback_data="no_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("⚠️ 确定要取消当前相册吗？", reply_markup=reply_markup)
    
    elif data.startswith("continue_"):
        album_id = data.replace("continue_", "")
        user_id = query.from_user.id
        
        # 获取相册信息
        album = await database.db.get_album_info(album_id)
        if not album or album['status'] != 'creating':
            await query.edit_message_text("❌ 相册不存在或已完成")
            return
        
        # 获取已收集的组数
        groups = await database.db.get_album_groups(album_id)
        group_count = len(groups)
        
        # 恢复用户会话
        user_sessions[user_id] = {
            'album_id': album_id,
            'group_buffer': [],
            'last_group_time': None,
            'group_number': group_count,
            'collecting_task': None
        }
        
        # 显示恢复消息和操作按钮（使用内联按钮）
        inline_keyboard = [
            [InlineKeyboardButton("✅ 确认收集完毕", callback_data=f"finish_album_{album_id}")],
            [InlineKeyboardButton("❌ 取消相册", callback_data=f"cancel_album_{album_id}")],
            [InlineKeyboardButton("📊 查看进度", callback_data=f"show_progress_{album_id}")]
        ]
        inline_markup = InlineKeyboardMarkup(inline_keyboard)
        
        await query.edit_message_text(
            f"✅ 已恢复相册「{album['album_name']}」\n\n"
            f"📊 当前进度：已收集 {group_count}/{config.MAX_MEDIA_GROUPS} 组媒体\n\n"
            f"📸 现在可以继续发送媒体了",
            reply_markup=None
        )
        
        await query.message.reply_text(
            f"✅ 已恢复相册「{album['album_name']}」\n\n"
            f"📊 当前进度：已收集 {group_count}/{config.MAX_MEDIA_GROUPS} 组媒体\n\n"
            f"💡 继续发送媒体，10个为一组",
            reply_markup=inline_markup
        )
    elif data.startswith("delete_album_"):
        album_id = data.replace("delete_album_", "")
        user_id = query.from_user.id
        
        # 获取相册信息
        album = await database.db.get_album_info(album_id)
        if not album:
            await query.edit_message_text("❌ 相册不存在")
            return
        
        # 确认删除
        keyboard = [
            [InlineKeyboardButton("✅ 确认删除", callback_data=f"confirm_delete_{album_id}")],
            [InlineKeyboardButton("❌ 取消", callback_data="cancel_delete")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚠️ 确认要删除相册「{album['album_name']}」吗？\n\n"
            f"删除后无法恢复！",
            reply_markup=reply_markup
        )
    elif data.startswith("confirm_delete_"):
        album_id = data.replace("confirm_delete_", "")
        user_id = query.from_user.id
        
        # 获取相册信息（用于显示名称）
        album = await database.db.get_album_info(album_id)
        if not album:
            await query.edit_message_text("❌ 相册不存在")
            return
        
        # 删除相册
        success = await database.db.delete_album(album_id, user_id)
        
        if success:
            # 清除buffer
            await database.db.clear_media_buffer(user_id, album_id)
            
            # 如果删除的是当前活跃会话，清除会话
            with user_sessions_lock:
                if user_id in user_sessions:
                    if user_sessions[user_id].get('album_id') == album_id:
                        del user_sessions[user_id]
            
            await query.edit_message_text(
                f"✅ 相册「{album['album_name']}」已删除",
                reply_markup=None
            )
        else:
            await query.edit_message_text("❌ 删除失败，相册不存在或无权限")
    elif data == "cancel_delete":
        await query.edit_message_text("✅ 已取消删除")
    elif data == "admin_authorize":
        user_id = query.from_user.id
        if not is_admin(user_id):
            await query.answer("❌ 您没有权限", show_alert=True)
            return
        
        # 显示授权时长选择按钮
        keyboard = [
            [InlineKeyboardButton("1个月", callback_data="auth_1_month")],
            [InlineKeyboardButton("3个月", callback_data="auth_3_month")],
            [InlineKeyboardButton("❌ 取消", callback_data="admin_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👤 授权用户\n\n"
            "请选择授权时长：\n\n"
            "然后发送用户ID（用户可以通过 @userinfobot 获取）",
            reply_markup=reply_markup
        )
        
        # 设置用户状态，等待输入用户ID
        if not hasattr(context, 'user_data'):
            context.user_data = {}
        context.user_data[user_id] = {'waiting_for_user_id': True}
    elif data.startswith("auth_"):
        user_id = query.from_user.id
        if not is_admin(user_id):
            await query.answer("❌ 您没有权限", show_alert=True)
            return
        
        months = 1 if data == "auth_1_month" else 3
        
        # 保存选择的月数，等待用户输入用户ID
        if not hasattr(context, 'user_data'):
            context.user_data = {}
        context.user_data[user_id] = {
            'waiting_for_user_id': True,
            'auth_months': months
        }
        
        await query.edit_message_text(
            f"📝 授权 {months} 个月\n\n"
            "请发送要授权的用户ID：\n\n"
            "💡 用户可以通过 @userinfobot 获取自己的ID"
        )
    elif data == "admin_list":
        user_id = query.from_user.id
        if not is_admin(user_id):
            await query.answer("❌ 您没有权限", show_alert=True)
            return
        authorizations = await database.db.get_all_authorizations()
        if not authorizations:
            await query.edit_message_text("📋 当前没有有效的授权")
            return
        text = "📋 授权列表：\n\n"
        for auth in authorizations[:20]:
            expire_date = datetime.fromisoformat(auth['expire_date'])
            start_date = datetime.fromisoformat(auth['start_date'])
            days_left = (expire_date - datetime.now()).days
            status = "✅" if days_left > 0 else "❌"
            text += f"{status} 用户ID: {auth['user_id']}\n"
            text += f"   开始: {start_date.strftime('%Y-%m-%d')}\n"
            text += f"   到期: {expire_date.strftime('%Y-%m-%d')}\n"
            text += f"   剩余: {days_left}天\n\n"
        if len(authorizations) > 20:
            text += f"\n...还有 {len(authorizations) - 20} 个授权"
        await query.edit_message_text(text)
    elif data == "admin_check":
        user_id = query.from_user.id
        if not is_admin(user_id):
            await query.answer("❌ 您没有权限", show_alert=True)
            return
        
        # 设置等待输入用户ID状态
        if not hasattr(context, 'user_data'):
            context.user_data = {}
        context.user_data[user_id] = {'waiting_for_check_user_id': True}
        
        await query.edit_message_text(
            "🔍 查询用户授权\n\n"
            "请发送要查询的用户ID：\n\n"
            "💡 用户可以通过 @userinfobot 获取自己的ID"
        )
    elif data == "admin_revoke":
        user_id = query.from_user.id
        if not is_admin(user_id):
            await query.answer("❌ 您没有权限", show_alert=True)
            return
        
        # 设置等待输入用户ID状态（取消授权）
        if not hasattr(context, 'user_data'):
            context.user_data = {}
        context.user_data[user_id] = {'waiting_for_revoke_user_id': True}
        
        await query.edit_message_text(
            "❌ 取消用户授权\n\n"
            "请发送要取消授权的用户ID：\n\n"
            "💡 用户可以通过 @userinfobot 获取自己的ID"
        )
    elif data == "admin_cancel":
        user_id = query.from_user.id
        # 清除等待状态
        if hasattr(context, 'user_data') and user_id in context.user_data:
            del context.user_data[user_id]
        
        # 返回主菜单
        keyboard = [
            [InlineKeyboardButton("👤 授权用户", callback_data="admin_authorize")],
            [InlineKeyboardButton("📋 查看授权列表", callback_data="admin_list")],
            [InlineKeyboardButton("🔍 查询用户授权", callback_data="admin_check")],
            [InlineKeyboardButton("❌ 取消用户授权", callback_data="admin_revoke")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🔐 用户授权管理\n\n"
            "请选择操作：",
            reply_markup=reply_markup
        )
    elif data == "broadcast_preview":
        user_id = query.from_user.id
        if not is_admin(user_id):
            await query.answer("❌ 您没有权限", show_alert=True)
            return
        
        if not hasattr(context, 'user_data') or user_id not in context.user_data:
            await query.answer("❌ 群发会话已过期，请重新开始", show_alert=True)
            return
        
        user_data = context.user_data[user_id]
        if not user_data.get('broadcast_mode') or not user_data.get('broadcast_messages'):
            await query.answer("❌ 还没有收集到消息，请先发送内容", show_alert=True)
            return
        
        messages = user_data['broadcast_messages']
        
        # 获取所有授权用户
        authorizations = await database.db.get_all_authorizations()
        valid_users = [auth['user_id'] for auth in authorizations]
        
        # 预览消息
        preview_text = f"📢 群发消息预览\n\n"
        preview_text += f"消息数量：{len(messages)} 条\n"
        preview_text += f"接收用户：{len(valid_users)} 人\n\n"
        preview_text += "消息内容：\n"
        preview_text += "-" * 30 + "\n"
        
        for i, msg in enumerate(messages, 1):
            if msg['type'] == 'text':
                preview_text += f"{i}. 文本：{msg['text'][:50]}...\n"
            else:
                preview_text += f"{i}. {msg['type']}"
                if msg.get('caption'):
                    preview_text += f"：{msg['caption'][:30]}..."
                preview_text += "\n"
        
        preview_text += "-" * 30 + "\n\n"
        preview_text += "⚠️ 确认要发送给所有授权用户吗？"
        
        keyboard = [
            [InlineKeyboardButton("✅ 确认发送", callback_data="broadcast_confirm")],
            [InlineKeyboardButton("❌ 取消", callback_data="broadcast_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(preview_text, reply_markup=reply_markup)
    
    elif data == "broadcast_confirm":
        user_id = query.from_user.id
        if not is_admin(user_id):
            await query.answer("❌ 您没有权限", show_alert=True)
            return
        
        if not hasattr(context, 'user_data') or user_id not in context.user_data:
            await query.answer("❌ 群发会话已过期", show_alert=True)
            return
        
        user_data = context.user_data[user_id]
        messages = user_data.get('broadcast_messages', [])
        
        if not messages:
            await query.answer("❌ 没有消息可发送", show_alert=True)
            return
        
        # 获取所有授权用户
        authorizations = await database.db.get_all_authorizations()
        valid_users = [auth['user_id'] for auth in authorizations]
        
        if not valid_users:
            await query.edit_message_text("❌ 没有找到有效的授权用户")
            del context.user_data[user_id]
            return
        
        await query.edit_message_text("📤 正在发送消息，请稍候...")
        
        # 发送消息
        success_count = 0
        fail_count = 0
        
        for target_user_id in valid_users:
            try:
                for msg in messages:
                    if msg['type'] == 'text':
                        await context.bot.send_message(
                            chat_id=target_user_id,
                            text=msg['text']
                        )
                    elif msg['type'] == 'photo':
                        await context.bot.send_photo(
                            chat_id=target_user_id,
                            photo=msg['file_id'],
                            caption=msg.get('caption', '')
                        )
                    elif msg['type'] == 'video':
                        await context.bot.send_video(
                            chat_id=target_user_id,
                            video=msg['file_id'],
                            caption=msg.get('caption', '')
                        )
                    elif msg['type'] == 'document':
                        await context.bot.send_document(
                            chat_id=target_user_id,
                            document=msg['file_id'],
                            caption=msg.get('caption', '')
                        )
                success_count += 1
            except Exception as e:
                logger.error(f"发送消息给用户 {target_user_id} 失败: {e}")
                fail_count += 1
        
        # 清除群发模式
        del context.user_data[user_id]
        
        await query.edit_message_text(
            f"✅ 群发完成！\n\n"
            f"成功：{success_count} 人\n"
            f"失败：{fail_count} 人\n"
            f"总计：{len(valid_users)} 人"
        )
    
    elif data == "broadcast_cancel":
        user_id = query.from_user.id
        if hasattr(context, 'user_data') and user_id in context.user_data:
            del context.user_data[user_id]
        await query.edit_message_text("❌ 已取消群发")
    
    elif data == "show_help":
        await query.answer()
        help_text = (
            "📖 使用说明：\n\n"
            "1️⃣ 点击「📸 创建新相册」或发送 /new_album [相册名称]\n"
            "2️⃣ 开始发送媒体（照片+文字或视频），10个为一组\n"
            "3️⃣ 每组发送完成后，Bot会自动确认收集情况\n"
            f"4️⃣ 继续发送下一组，最多{config.MAX_MEDIA_GROUPS}组\n"
            "5️⃣ 点击「✅ 确认收集完毕」生成网页和二维码\n\n"
            f"⚠️ 重要提示：\n"
            f"• 相册将在{config.ALBUM_EXPIRE_DAYS}天后自动删除\n"
            f"• 最多可上传{config.MAX_MEDIA_GROUPS}组媒体\n"
            f"• 可通过「📊 我的相册」查看和删除相册"
        )
        await query.edit_message_text(help_text)

async def cleanup_task(context: ContextTypes.DEFAULT_TYPE):
    """定时清理过期相册"""
    try:
        count = await database.db.cleanup_expired_albums()
        if count > 0:
            logger.info(f"Cleaned up {count} expired albums")
    except Exception as e:
        logger.error(f"Error cleaning up albums: {e}")

def main():
    """主函数"""
    # 检查 Bot Token
    if not config.TELEGRAM_BOT_TOKEN:
        token = os.getenv('TELEGRAM_BOT_TOKEN')
        if token:
            config.TELEGRAM_BOT_TOKEN = token
        else:
            logger.error("TELEGRAM_BOT_TOKEN not set!")
            logger.error("请设置环境变量 TELEGRAM_BOT_TOKEN 或创建 .env 文件")
            return
    
    # 创建应用（会自动初始化数据库）
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    
    # 使用 post_init 钩子初始化数据库
    async def post_init_hook(app):
        await init_database()
        logger.info("Database initialization completed via post_init")
    
    application.post_init = post_init_hook
    
    # 注册处理器（注意顺序：先注册命令，再注册消息）
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("new_album", new_album_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("authorize", admin_authorize_user))
    application.add_handler(CommandHandler("list_auth", admin_list_authorizations))
    application.add_handler(CommandHandler("check_user", admin_check_user))
    application.add_handler(CallbackQueryHandler(callback_handler))
    # 媒体处理器放在文本处理器之前，避免冲突
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.IMAGE, handle_media))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # 启动清理任务（检查 job_queue 是否可用）
    if application.job_queue:
        async def cleanup_wrapper(context):
            """清理任务包装器"""
            try:
                await database.db.cleanup_expired_albums()
            except Exception as e:
                logger.error(f"Error cleaning up albums: {e}")
        
        application.job_queue.run_repeating(
            cleanup_wrapper,
            interval=6 * 3600,  # 6小时
            first=3600  # 1小时后开始
        )
        logger.info("Cleanup job scheduled")
        
        # 启动授权到期提醒任务
        async def check_expiring_authorizations(context):
            """检查即将到期的授权并提醒用户"""
            try:
                expiring_auths = await database.db.get_expiring_authorizations(days_before=1)
                
                for auth in expiring_auths:
                    user_id = auth['user_id']
                    expire_date = datetime.fromisoformat(auth['expire_date'])
                    
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=(
                                "⚠️ 授权即将到期提醒\n\n"
                                f"您的相册功能授权将在明天到期！\n"
                                f"到期时间：{expire_date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                                "请及时联系管理员续费，避免影响使用。"
                            )
                        )
                        # 标记提醒已发送
                        await database.db.mark_reminder_sent(user_id)
                        logger.info(f"已发送到期提醒给用户 {user_id}")
                    except Exception as e:
                        logger.error(f"发送到期提醒失败 (用户 {user_id}): {e}")
                        # 如果用户阻止了bot，仍然标记为已发送，避免重复尝试
                        await database.db.mark_reminder_sent(user_id)
            except Exception as e:
                logger.error(f"检查到期授权时出错: {e}")
        
        # 每天检查一次（在凌晨2点）
        application.job_queue.run_daily(
            check_expiring_authorizations,
            time=dt_time(hour=2, minute=0)
        )
        logger.info("Authorization reminder job scheduled")
    else:
        logger.warning("JobQueue not available, scheduled tasks disabled")
    
    # 启动Bot
    logger.info("Bot starting...")
    
    # run_polling() 会自动管理事件循环
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    # Windows 事件循环修复
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # 运行主函数
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        import traceback
        traceback.print_exc()

