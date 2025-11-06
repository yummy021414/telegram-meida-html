# 快速开始

## 🚀 一键部署（Linux 服务器）

```bash
# 1. 克隆代码
git clone <your-repo-url>
cd meiti

# 2. 配置 Bot Token
cp env.example .env
nano .env
# 设置 TELEGRAM_BOT_TOKEN

# 3. 运行部署脚本
chmod +x deploy-docker.sh
./deploy-docker.sh
```

完成！服务将自动启动并显示日志。

---

## 💻 本地测试（Windows）

1. 双击 `start-local.bat`
2. 或运行 `python run.py`

---

## 📚 详细文档

- `README.md` - 完整说明
- `DEPLOY_GUIDE.md` - 部署指南  
- `ADMIN_GUIDE.md` - 管理员功能

---

## ⚙️ 配置检查

确保 `.env` 文件包含：
```env
TELEGRAM_BOT_TOKEN=你的token  # 必填
DOMAIN=https://你的域名.com    # 生产环境
ADMIN_USER_IDS=你的用户ID     # 超管ID
```

---

## 🐛 故障排查

问题 | 解决方案
---|---
Bot 不响应 | 检查 Token 是否正确
网页打不开 | 检查端口 5000 是否开放
图片不显示 | 检查 DOMAIN 配置

---

## 📞 技术支持

- Telegram: @faziliaobot
- 查看日志: `docker-compose logs -f`


