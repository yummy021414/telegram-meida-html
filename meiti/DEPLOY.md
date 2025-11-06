# 部署指南

## 服务器上执行（已上传后）

### 一键部署

```bash
cd ~/meiti
sed -i 's/\r$//' .env *.sh
chmod +x *.sh
./deploy-docker.sh
```

### 预期输出

```
✓ Docker 已安装
✓ Docker Compose 已安装
修复文件换行符...
✓ 换行符已修复
✓ 配置检查通过
构建并启动...
✓ 部署成功
访问: http://localhost:5000
```

---

## Docker 命令

```bash
# 查看日志
docker-compose logs -f

# 重启
docker-compose restart

# 停止
docker-compose stop

# 删除
docker-compose down
```

---

## 故障排查

### 权限错误
```bash
chmod +x *.sh
```

### 换行符错误
```bash
sed -i 's/\r$//' .env *.sh
```

### Bot Token 未设置
```bash
nano .env
# 设置 TELEGRAM_BOT_TOKEN
```

---

更多信息见 `README.md`


