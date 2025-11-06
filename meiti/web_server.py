from flask import Flask, render_template, abort, request, redirect, Response
import asyncio
import threading
import aiohttp
import database
import config
from PIL import Image, ImageDraw
import io
import time
import sys
from collections import deque

app = Flask(__name__)

# 请求速率控制（简化版，使用线程锁）
class RateLimiter:
    def __init__(self, max_calls=50, time_window=1.0):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = deque()
        self.lock = threading.Lock()
    
    def acquire_sync(self):
        """同步版本的acquire（不使用asyncio，避免死锁）"""
        with self.lock:
            now = time.time()
            # 移除过期的调用记录
            while self.calls and self.calls[0] < now - self.time_window:
                self.calls.popleft()
            
            # 记录本次调用
            self.calls.append(time.time())
            
            # 如果超过限制，返回False（调用方决定是否等待）
            return len(self.calls) <= self.max_calls
    
    async def acquire(self):
        """异步接口（实际使用同步实现）"""
        return self.acquire_sync()

# 创建速率限制器（每秒钟最多50个请求，提高性能）
rate_limiter = RateLimiter(max_calls=50, time_window=1.0)

# 文件URL缓存（避免重复请求Telegram API）
file_url_cache = {}
cache_lock = threading.Lock()  # 使用线程锁替代 asyncio.Lock()
CACHE_EXPIRE_TIME = 3600  # 缓存1小时

# ========== 常驻事件循环（解决 Windows 上 aiohttp 超时问题）==========
# 创建全局事件循环，在后台线程中持续运行
loop = asyncio.new_event_loop()
loop_ready = threading.Event()

def start_event_loop():
    """在后台线程中启动事件循环"""
    asyncio.set_event_loop(loop)
    loop.call_soon(loop_ready.set)  # 标记循环已就绪
    loop.run_forever()

# 启动后台线程运行事件循环
threading.Thread(target=start_event_loop, daemon=True).start()

# 等待事件循环就绪（最多等待2秒）
if not loop_ready.wait(timeout=2.0):
    print("[WARNING] Event loop startup timeout, but continuing...")

def run_async(coro):
    """将异步任务提交到常驻事件循环中执行"""
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=30)  # 添加超时保护，避免永久卡住
    except Exception as e:
        print(f"[ERROR] run_async failed: {e}")
        import traceback
        traceback.print_exc()
        raise

# ========== 全局 HTTP Session（只创建一次，提高性能）==========
global_session = None
session_lock = threading.Lock()

async def get_session_async():
    """在异步上下文中获取session（避免嵌套的run_async调用）"""
    global global_session
    
    # 使用锁保护，避免并发创建多个session
    with session_lock:
        if global_session is None or global_session.closed:
            print("[INFO] Creating global session...")
            # 直接在异步上下文中创建（不使用 run_async，避免嵌套）
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=30, ttl_dns_cache=300)
            global_session = aiohttp.ClientSession(connector=connector)
            print("[INFO] Global session created successfully")
    
    return global_session

def get_session():
    """获取全局session（同步版本，仅用于已经在事件循环外的场景）"""
    global global_session
    
    with session_lock:
        if global_session is None or global_session.closed:
            print("[INFO] Creating global session (sync)...")
            future = asyncio.run_coroutine_threadsafe(_create_session(), loop)
            global_session = future.result(timeout=5)
            print("[INFO] Global session created successfully (sync)")
        return global_session

async def _create_session():
    """在事件循环中创建 session 和 connector"""
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=30, ttl_dns_cache=300)
    return aiohttp.ClientSession(connector=connector)

async def get_telegram_file_url(file_id, retry_count=3):
    """获取 Telegram 文件的下载 URL（带缓存、指数退避重试和速率限制）"""
    bot_token = config.TELEGRAM_BOT_TOKEN
    
    if not bot_token:
        print(f"[ERROR] Bot Token未设置！")
        return None
    
    # 检查缓存（1小时有效期）
    with cache_lock:  # 使用线程锁（同步方式）
        if file_id in file_url_cache:
            cached_url, cached_time = file_url_cache[file_id]
            if time.time() - cached_time < CACHE_EXPIRE_TIME:
                print(f"[CACHE] 使用缓存URL for file_id={file_id}")
                return cached_url
            else:
                # 缓存过期，删除
                del file_url_cache[file_id]
    
    session = await get_session_async()  # 在异步上下文中直接获取session
    
    # 指数退避重试
    for attempt in range(retry_count):
        try:
            # 应用速率限制
            await rate_limiter.acquire()
            
            api_url = f"https://api.telegram.org/bot{bot_token}/getFile"
            print(f"[GETFILE] 尝试 {attempt + 1}/{retry_count}: file_id={file_id}")
            
            # 直接调用，不使用超时（避免Windows上的ClientTimeout问题）
            resp = await session.get(api_url, params={"file_id": file_id})
            
            try:
                async with resp:
                    print(f"[GETFILE] 响应状态: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"[GETFILE] 响应数据: {data}")
                        if data.get('ok'):
                            file_path = data['result']['file_path']
                            url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                            print(f"[GETFILE] 成功获取URL: {url}")
                            # 存入缓存（1小时）
                            with cache_lock:  # 使用线程锁（同步方式）
                                file_url_cache[file_id] = (url, time.time())
                            return url
                        elif data.get('error_code') == 400:
                            # file_id 无效，不再重试
                            print(f"[GETFILE] 错误：file_id无效，error={data.get('description')}")
                            return None
                        else:
                            print(f"[GETFILE] 错误：{data.get('description', '未知错误')}")
                    elif resp.status == 429:
                        # 速率限制，指数退避重试
                        retry_after = int(resp.headers.get('Retry-After', 2))
                        wait_time = retry_after * (2 ** attempt)  # 指数退避：2s, 4s, 8s
                        if attempt < retry_count - 1:
                            await asyncio.sleep(min(wait_time, 10))  # 最多等待10秒
                            await rate_limiter.acquire()
                            continue
                    else:
                        # 其他错误，指数退避重试
                        wait_time = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                        if attempt < retry_count - 1:
                            await asyncio.sleep(wait_time)
                            await rate_limiter.acquire()
                            continue
            except Exception as e:
                print(f"[GETFILE] Error processing response (attempt {attempt + 1}): {e}")
                import traceback
                traceback.print_exc()
                wait_time = 0.5 * (2 ** attempt)  # 指数退避
                if attempt < retry_count - 1:
                    await asyncio.sleep(wait_time)
                    await rate_limiter.acquire()
                    continue
        except Exception as e:
            print(f"[GETFILE] Error getting file URL (attempt {attempt + 1}): {e}")
            import traceback
            traceback.print_exc()
            wait_time = 0.5 * (2 ** attempt)  # 指数退避
            if attempt < retry_count - 1:
                await asyncio.sleep(wait_time)
                await rate_limiter.acquire()
                continue
    
    return None

@app.route('/proxy/<file_id>')
def proxy_file(file_id):
    """代理 Telegram 文件访问（带重试和错误处理，优化性能）"""
    async def fetch():
        print(f"[PROXY] 开始获取文件 file_id={file_id}")
        url = await get_telegram_file_url(file_id)
        if not url:
            print(f"[PROXY] 错误：无法获取文件URL，file_id={file_id}，返回占位图片")
            # 返回一个占位图片而不是 404
            img = Image.new('RGB', (400, 400), color='#f0f0f0')
            draw = ImageDraw.Draw(img)
            bio = io.BytesIO()
            img.save(bio, format='PNG')
            bio.seek(0)
            return Response(bio.getvalue(), content_type='image/png')
        
        print(f"[PROXY] 成功获取文件URL: {url[:50]}...")
        
        retry_count = 3
        session = await get_session_async()  # 在异步上下文中直接获取session
        
        # 指数退避重试
        for attempt in range(retry_count):
            try:
                # 应用速率限制
                await rate_limiter.acquire()
                
                print(f"[PROXY] 开始下载文件: {url[:50]}...")
                # 直接调用，不使用超时（避免Windows上的ClientTimeout问题）
                resp = await session.get(url)
                
                async with resp:
                    print(f"[PROXY] 下载响应状态: {resp.status}")
                    if resp.status == 200:
                        content = await resp.read()
                        content_size = len(content)
                        print(f"[PROXY] 下载成功，大小: {content_size} 字节")
                        
                        # 根据响应头或文件扩展名确定 Content-Type
                        content_type = resp.headers.get('Content-Type')
                        
                        # 如果响应头没有 Content-Type，根据 URL 判断
                        if not content_type or content_type == 'application/octet-stream':
                            if '.mp4' in url.lower():
                                content_type = 'video/mp4'
                            elif '.webm' in url.lower():
                                content_type = 'video/webm'
                            elif '.mov' in url.lower():
                                content_type = 'video/quicktime'
                            elif '.avi' in url.lower():
                                content_type = 'video/x-msvideo'
                            elif '.jpg' in url.lower() or '.jpeg' in url.lower():
                                content_type = 'image/jpeg'
                            elif '.png' in url.lower():
                                content_type = 'image/png'
                            elif '.gif' in url.lower():
                                content_type = 'image/gif'
                            elif 'video' in url.lower() or content_size > 1024 * 1024:  # >1MB 很可能是视频
                                content_type = 'video/mp4'  # 默认为 MP4
                            else:
                                content_type = 'image/jpeg'  # 默认
                        
                        print(f"[PROXY] Content-Type: {content_type}, Size: {content_size}")
                        
                        # 添加缓存头
                        response = Response(content, content_type=content_type)
                        response.headers['Cache-Control'] = 'public, max-age=3600'
                        return response
                    elif resp.status == 429:
                        # 速率限制，指数退避重试
                        retry_after = int(resp.headers.get('Retry-After', 2))
                        wait_time = retry_after * (2 ** attempt)  # 指数退避：2s, 4s, 8s
                        if attempt < retry_count - 1:
                            await asyncio.sleep(min(wait_time, 10))  # 最多等待10秒
                            await rate_limiter.acquire()
                            continue
                    else:
                        # 其他错误，指数退避重试
                        wait_time = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                        if attempt < retry_count - 1:
                            await asyncio.sleep(wait_time)
                            continue
            except asyncio.TimeoutError:
                # 超时，指数退避重试
                wait_time = 1 * (2 ** attempt)  # 1s, 2s, 4s
                if attempt < retry_count - 1:
                    await asyncio.sleep(wait_time)
                    await rate_limiter.acquire()
                    continue
            except Exception as e:
                print(f"Error fetching file (attempt {attempt + 1}): {e}")
                wait_time = 0.5 * (2 ** attempt)  # 指数退避
                if attempt < retry_count - 1:
                    await asyncio.sleep(wait_time)
                    await rate_limiter.acquire()
                    continue
        
        # 所有重试都失败，返回占位图片
        img = Image.new('RGB', (400, 400), color='#f0f0f0')
        draw = ImageDraw.Draw(img)
        bio = io.BytesIO()
        img.save(bio, format='PNG')
        bio.seek(0)
        return Response(bio.getvalue(), content_type='image/png')
    
    return run_async(fetch())

@app.route('/album/<album_id>')
def view_album(album_id):
    """查看相册页面"""
    try:
        print(f"\n[VIEW] ========== 开始处理相册请求 {album_id} ==========")
        
        # 获取访问token
        access_token = request.args.get('token')
        print(f"[VIEW] Token: {access_token[:20] if access_token else 'None'}...")
        
        if not access_token:
            print(f"[ERROR] 访问相册 {album_id} 失败：缺少token")
            abort(404)  # 没有token，拒绝访问
        
        # 验证访问权限
        print(f"[VIEW] 验证访问权限...")
        try:
            has_access = run_async(database.db.verify_album_access(album_id, access_token))
            print(f"[VIEW] 权限验证结果: {has_access}")
        except Exception as e:
            print(f"[ERROR] 权限验证失败: {e}")
            import traceback
            traceback.print_exc()
            abort(500)
        
        if not has_access:
            print(f"[ERROR] 访问相册 {album_id} 失败：token不匹配")
            abort(404)  # token不匹配，拒绝访问
        
        # 获取相册数据
        print(f"[VIEW] 获取相册数据...")
        try:
            album_data = run_async(database.db.get_album_full_data(album_id))
            print(f"[VIEW] 相册数据获取成功: {album_data['album_name'] if album_data else 'None'}")
        except Exception as e:
            print(f"[ERROR] 获取相册数据失败: {e}")
            import traceback
            traceback.print_exc()
            abort(500)
        
        if not album_data:
            print(f"[ERROR] 访问相册 {album_id} 失败：相册不存在")
            abort(404)
        
        if album_data['status'] != 'completed':
            print(f"[ERROR] 访问相册 {album_id} 失败：相册未完成，状态={album_data['status']}")
            abort(404)
        
        # 组织数据供模板使用
        groups_data = []
        groups = album_data.get('groups', [])
        print(f"[DEBUG] 相册 {album_id} 原始组数: {len(groups)}")
        
        if not groups:
            print(f"[WARNING] 相册 {album_id} 没有媒体组数据！")
            return render_template('album.html', 
                                 album_name=album_data['album_name'],
                                 groups=[],
                                 domain=config.DOMAIN)
        
        for group in groups:
            media = group.get('media', [])
            print(f"[DEBUG] 组 {group.get('group_number')} 包含 {len(media)} 个媒体")
            groups_data.append({
                'number': group['group_number'],
                'media': media,
                'text': group.get('text_content', '')
            })
        
        total_media = sum(len(g.get('media', [])) for g in groups_data)
        print(f"[INFO] 加载相册 {album_id}，共 {len(groups_data)} 组，总计 {total_media} 个媒体")
        
        if total_media == 0:
            print(f"[WARNING] 相册 {album_id} 没有媒体文件！")
        
        # 确定域名（本地测试时使用 localhost）
        domain = request.host_url.rstrip('/')
        if 'localhost' in domain or '127.0.0.1' in domain:
            domain = 'http://localhost:5000'
        else:
            domain = config.DOMAIN
        
        # 调试：检查groups_data
        print(f"[DEBUG] groups_data长度: {len(groups_data)}")
        for i, g in enumerate(groups_data):
            text_len = len(g.get('text') or '')
            print(f"[DEBUG] 组{i+1}: number={g.get('number')}, media数量={len(g.get('media', []))}, text长度={text_len}")
        
        print(f"[VIEW] 开始渲染模板...")
        try:
            response = app.make_response(render_template('album.html', 
                                 album_name=album_data['album_name'],
                                 groups=groups_data,
                                 domain=domain))
            # 设置响应头，优化性能
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            response_size = len(response.get_data())
            print(f"[VIEW] 模板渲染成功，响应长度: {response_size} 字节")
            print(f"[VIEW] ========== 请求处理完成 ==========\n")
            return response
        except Exception as e:
            print(f"[ERROR] 渲染模板失败: {e}")
            import traceback
            traceback.print_exc()
            raise
    except Exception as e:
        print(f"[ERROR] 渲染相册页面时出错: {e}")
        import traceback
        traceback.print_exc()
        abort(500)

@app.route('/test-links')
def test_links():
    """测试链接页面"""
    import aiosqlite
    import asyncio
    
    async def get_albums():
        async with aiosqlite.connect(database.db.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT album_id, album_name, access_token
                FROM albums 
                WHERE status = 'completed'
                ORDER BY created_at DESC
                LIMIT 10
            ''') as cursor:
                albums = await cursor.fetchall()
                return [dict(album) for album in albums]
    
    albums = run_async(get_albums())
    
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>测试相册链接</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 900px; margin: 50px auto; padding: 20px; }
            .link-box { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; word-break: break-all; }
            .link-box a { color: #2196F3; text-decoration: none; }
            .link-box a:hover { text-decoration: underline; }
            h2 { color: #333; }
        </style>
    </head>
    <body>
        <h1>测试相册链接</h1>
    '''
    
    for album in albums:
        link = f"http://localhost:5000/album/{album['album_id']}?token={album['access_token']}"
        html += f'''
        <h2>相册: {album['album_name']}</h2>
        <div class="link-box">
            <a href="{link}" target="_blank">{link}</a>
        </div>
        '''
    
    html += '''
        <p><strong>说明：</strong>点击上面的链接测试访问。</p>
    </body>
    </html>
    '''
    
    return html

@app.route('/')
def index():
    """首页"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Telegram 媒体分享 Bot</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 50px auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 { color: #333; }
            .info { 
                background: #e3f2fd;
                padding: 15px;
                border-radius: 5px;
                margin: 20px 0;
            }
            .link {
                display: inline-block;
                margin: 10px 0;
                padding: 10px 20px;
                background: #2196F3;
                color: white;
                text-decoration: none;
                border-radius: 5px;
            }
            .link:hover { background: #1976D2; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎉 Web 服务运行正常！</h1>
            <div class="info">
                <p><strong>服务状态：</strong> ✅ 运行中</p>
                <p><strong>访问地址：</strong> http://localhost:5000</p>
            </div>
            <h2>可用路径：</h2>
            <ul>
                <li><a href="/health" class="link">/health</a> - 健康检查</li>
                <li><a href="/album/你的相册ID" class="link">/album/&lt;相册ID&gt;</a> - 查看相册</li>
            </ul>
            <div class="info">
                <p><strong>💡 使用说明：</strong></p>
                <p>1. 在 Telegram Bot 中创建相册并完成收集</p>
                <p>2. Bot 会返回一个链接，格式如：<code>https://hotbaby.top/album/xxx-xxx-xxx</code></p>
                <p>3. 本地测试时，将域名改为 <code>http://localhost:5000</code></p>
                <p>4. 例如：<code>http://localhost:5000/album/xxx-xxx-xxx</code></p>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/health')
def health():
    """健康检查"""
    return {'status': 'ok'}

@app.route('/test-simple')
def test_simple():
    """简单测试页面"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>简单测试</title>
        <style>
            body {
                background: red;
                color: white;
                font-size: 30px;
                padding: 50px;
            }
        </style>
    </head>
    <body>
        <h1>✓ Web服务正常运行</h1>
        <p>如果看到此页面，说明服务正常</p>
        <p>时间: ''' + time.strftime('%Y-%m-%d %H:%M:%S') + '''</p>
    </body>
    </html>
    '''

@app.route('/log_js_error', methods=['POST'])
def log_js_error():
    """记录JavaScript错误（便于排查问题）"""
    try:
        data = request.get_json()
        print(f"[JS_ERROR] {data.get('msg')} at {data.get('src')}:{data.get('line')}:{data.get('col')}")
        print(f"[JS_ERROR] UserAgent: {data.get('userAgent')}")
        print(f"[JS_ERROR] URL: {data.get('url')}")
        if data.get('error'):
            print(f"[JS_ERROR] Error: {data.get('error')}")
        return {'status': 'ok'}, 200
    except Exception as e:
        print(f"[JS_ERROR] Failed to log error: {e}")
        return {'status': 'error'}, 500

@app.errorhandler(404)
def not_found(error):
    """404错误处理"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>404 - 页面未找到</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
            h1 { color: #666; }
        </style>
    </head>
    <body>
        <h1>404 - 页面未找到</h1>
        <p>相册不存在或链接已过期</p>
    </body>
    </html>
    ''', 404

@app.errorhandler(500)
def internal_error(error):
    """500错误处理"""
    import traceback
    error_msg = traceback.format_exc()
    print(f"[ERROR] 500错误: {error_msg}")
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>500 - 服务器错误</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
            h1 { color: #f44336; }
        </style>
    </head>
    <body>
        <h1>500 - 服务器错误</h1>
        <p>服务器处理请求时出错，请稍后重试</p>
    </body>
    </html>
    ''', 500

if __name__ == '__main__':
    print("[INFO] 等待事件循环就绪...")
    time.sleep(0.5)  # 给事件循环一些启动时间
    
    # 初始化数据库
    print("[INFO] 初始化数据库...")
    try:
        run_async(database.db.init_db())
        print("[INFO] 数据库初始化完成")
    except Exception as e:
        print(f"[ERROR] 数据库初始化失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print(f"[INFO] Web服务启动在端口 {config.WEB_PORT}")
    print(f"[INFO] 域名配置: {config.DOMAIN}")
    
    # 启用详细日志
    import logging
    logging.basicConfig(level=logging.DEBUG)
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.INFO)
    
    print("[INFO] 启动 Flask 应用...")
    app.run(host='0.0.0.0', port=config.WEB_PORT, debug=True, use_reloader=False)

