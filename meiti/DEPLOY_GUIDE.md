# 部署指南

## Docker 部署（推荐）

### 快速部署

```bash
# 1. 配置 Bot Token
cp env.example .env
nano .env  # 设置 TELEGRAM_BOT_TOKEN

# 2. 运行部署脚本
chmod +x deploy-docker.sh
./deploy-docker.sh
```

### 手动部署

```bash
# 构建
docker-compose build

# 启动
docker-compose up -d

# 查看日志
docker-compose logs -f
```

---

## 传统部署（无 Docker）

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境
cp env.example .env
nano .env

# 启动
python run.py
```

---

## 生产环境（Nginx + SSL）

### 1. 准备 SSL 证书

```bash
mkdir ssl
# 复制证书文件到 ssl/ 目录
```

### 2. 修改 nginx.conf

取消 HTTPS 部分注释，修改域名

### 3. 启动

```bash
docker-compose -f docker-deploy-production.yml up -d
```

---

## 更新部署

```bash
# 拉取最新代码
git pull

# 重新部署
docker-compose up -d --build
```

---

## 常用操作

### 查看日志
```bash
docker-compose logs -f
```

### 重启服务
```bash
docker-compose restart
```

### 停止服务
```bash
docker-compose stop
```

### 备份数据
```bash
cp data/albums.db data/albums.db.backup
```

---

## 故障排查

### Bot 不响应
- 检查 Token: `grep TELEGRAM_BOT_TOKEN .env`
- 查看日志: `docker-compose logs | grep BOT`

### 网页打不开
- 检查端口: `docker ps | grep 5000`
- 测试健康: `curl http://localhost:5000/health`

### 图片不显示
- 检查域名配置 `DOMAIN`
- 查看代理日志: `docker-compose logs | grep PROXY`

---

更多信息见 `README.md` 和 `ADMIN_GUIDE.md`


