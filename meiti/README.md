# Telegram 相册分享 Bot

一个 Telegram Bot，用于收集媒体（照片、视频、文字）并生成在线相册页面和二维码，方便分享到微信等平台。

## 核心功能

- 📸 收集多组媒体（每组最多10个，最多50组）
- 🌐 生成在线相册网页（响应式设计）
- 📱 生成二维码（方便微信分享）
- ⏰ 相册3天后自动焚毁
- 🔐 用户隔离（access_token）
- 👑 超管功能（用户授权、群发消息）
- 💾 数据持久化（SQLite）
- 🔄 Bot重启后自动恢复（media_buffer）

---

## 快速部署

### Docker 部署（推荐）

```bash
# 1. 配置环境变量
cp env.example .env
nano .env  # 填写 TELEGRAM_BOT_TOKEN

# 2. 一键部署
chmod +x deploy-docker.sh
./deploy-docker.sh
```

### 传统部署

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境
cp env.example .env
nano .env

# 3. 运行
python run.py
```

---

## 环境要求

- Python 3.11+
- Docker（推荐）或 Linux/Windows
- Telegram Bot Token

---

## 配置说明

### 必填项

```env
TELEGRAM_BOT_TOKEN=你的bot_token  # 从 @BotFather 获取
DOMAIN=https://your-domain.com    # 生产环境域名
ADMIN_USER_IDS=710485715         # 超管ID（@userinfobot 获取）
```

### 可选项

```env
WEB_PORT=5000                    # Web 服务端口
ALBUM_EXPIRE_DAYS=3              # 相册自毁天数
MAX_MEDIA_GROUPS=50              # 最大组数
```

---

## 使用说明

### 普通用户

1. 发送 `/start` 启动 Bot
2. 点击「📸 创建新相册」
3. 发送媒体（照片、视频）
4. 每10个自动分组，或点击「✅ 确认收集完毕」
5. 获取网页链接和二维码

### 超级管理员

- `🔐 用户授权` - 授权用户使用（1或3个月）
- `📢 群发消息` - 向所有授权用户群发
- 查看授权列表
- 撤销用户授权

---

## 项目结构

```
meiti/
├── bot.py              # Telegram Bot 逻辑
├── web_server.py       # Flask Web 服务
├── database.py         # 数据库操作
├── config.py           # 配置管理
├── run.py              # 启动脚本
├── requirements.txt    # Python 依赖
├── Dockerfile          # Docker 镜像
├── docker-compose.yml  # Docker 编排
├── deploy-docker.sh    # 一键部署脚本
├── templates/          # HTML 模板
│   └── album.html      # 相册页面
├── data/               # 数据目录
│   └── albums.db       # SQLite 数据库
└── .env                # 环境变量（需创建）
```

---

## 技术栈

- **Bot**: python-telegram-bot
- **Web**: Flask + aiohttp
- **Database**: SQLite (aiosqlite)
- **UI**: HTML/CSS/JavaScript (响应式)
- **部署**: Docker + Nginx (可选)

---

## 常用命令

### Docker

```bash
# 查看日志
docker-compose logs -f

# 重启服务
docker-compose restart

# 停止服务
docker-compose stop

# 删除容器
docker-compose down
```

### 直接运行

```bash
# 启动服务
python run.py

# 仅启动 Web
python web_server.py

# 仅启动 Bot
python bot.py
```

---

## 故障排查

### Bot 无响应
- 检查 `TELEGRAM_BOT_TOKEN` 是否正确
- 确保只有一个 Bot 实例在运行

### 网页打不开
- 检查 Web 服务是否启动（端口 5000）
- 检查防火墙是否开放端口
- 访问 `http://localhost:5000/health` 测试

### 图片不显示
- 检查 `DOMAIN` 配置
- 查看服务器日志是否有错误
- 清除浏览器缓存

---

## 安全建议

1. 定期备份数据库（`data/albums.db`）
2. 使用 HTTPS（生产环境）
3. 限制超管ID（`ADMIN_USER_IDS`）
4. 定期查看日志
5. 及时更新依赖

---

## 管理员功能

详见 `ADMIN_GUIDE.md`

---

## 更新日志

### v1.0.0
- ✓ 基础相册收集功能
- ✓ 网页展示和二维码生成
- ✓ 用户授权系统
- ✓ 群发消息功能
- ✓ 并发安全保护
- ✓ 数据持久化（media_buffer）
- ✓ Windows 兼容性修复

---

## 许可证

MIT License

---

## 联系方式

- Telegram: @faziliaobot
- Issues: 项目 GitHub Issues

---

**注意**: 相册链接3天后自动失效，请及时保存重要内容。
