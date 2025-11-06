#!/bin/bash
#========================================
# Telegram 相册 Bot - 一键 Docker 部署
#========================================

set -e

clear
echo "========================================="
echo " Telegram 相册 Bot - Docker 一键部署"
echo "========================================="
echo ""

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker 未安装"
    echo ""
    echo "安装 Docker:"
    echo "  curl -fsSL https://get.docker.com | sh"
    exit 1
fi

echo "✓ Docker 已安装"

# 检查 Docker Compose
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose 未安装"
    exit 1
fi

echo "✓ Docker Compose 已安装"
echo ""

# 检查配置
if [ ! -f .env ]; then
    if [ -f env.example ]; then
        cp env.example .env
        # 转换换行符
        sed -i 's/\r$//' .env 2>/dev/null || true
        echo "✓ .env 文件已创建"
        echo ""
        echo "⚠️  请编辑 .env 设置 Bot Token:"
        echo "   nano .env"
        echo ""
        echo "然后重新运行: ./deploy-docker.sh"
        exit 0
    else
        echo "❌ env.example 不存在"
        exit 1
    fi
fi

# 转换 .env 文件的换行符（Windows CRLF -> Linux LF）
echo "修复文件换行符..."
if command -v dos2unix &> /dev/null; then
    dos2unix .env 2>/dev/null || true
elif command -v sed &> /dev/null; then
    sed -i 's/\r$//' .env 2>/dev/null || true
else
    # 使用 tr 作为后备方案
    tr -d '\r' < .env > .env.tmp && mv .env.tmp .env
fi

echo "✓ 换行符已修复"
echo ""

# 读取配置（使用更安全的方式）
set -a
source <(grep -v '^#' .env | grep -v '^$')
set +a

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ "$TELEGRAM_BOT_TOKEN" = "your_bot_token_here" ]; then
    echo "❌ Bot Token 未设置"
    echo ""
    echo "编辑 .env:"
    echo "  nano .env"
    exit 1
fi

echo "✓ 配置检查通过"
echo ""

# 创建目录
mkdir -p data qrcodes
chmod 777 data qrcodes

# 停止旧服务
echo "停止旧服务..."
docker-compose down 2>/dev/null || docker compose down 2>/dev/null || true

# 构建启动
echo "构建并启动..."
if command -v docker-compose &> /dev/null; then
    docker-compose up -d --build
else
    docker compose up -d --build
fi

echo ""
sleep 8

# 检查状态
if docker ps | grep -q telegram-album-bot; then
    echo "========================================="
    echo "  ✓ 部署成功"
    echo "========================================="
    echo ""
    echo "访问: http://localhost:5000"
    echo "日志: docker-compose logs -f"
    echo ""
    echo "按 Ctrl+C 退出日志查看"
    echo ""
    if command -v docker-compose &> /dev/null; then
        docker-compose logs -f
    else
        docker compose logs -f
    fi
else
    echo "❌ 启动失败"
    if command -v docker-compose &> /dev/null; then
        docker-compose logs --tail=30
    else
        docker compose logs --tail=30
    fi
    exit 1
fi

