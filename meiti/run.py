#!/usr/bin/env python3
"""
统一启动脚本 - 同时运行 Bot 和 Web 服务
适用于开发环境
"""
import subprocess
import sys
import os
import signal
import time

def signal_handler(sig, frame):
    """处理退出信号"""
    print('\n正在关闭服务...')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def main():
    """主函数"""
    # 检查 .env 文件
    if not os.path.exists('.env'):
        print("错误: 未找到 .env 文件")
        print("请复制 .env.example 为 .env 并配置")
        sys.exit(1)
    
    # 创建数据目录
    os.makedirs('data', exist_ok=True)
    
    print("=" * 50)
    print("Telegram 媒体分享 Bot")
    print("=" * 50)
    print("\n正在启动服务...")
    print("按 Ctrl+C 停止服务\n")
    
    try:
        # 启动 Bot（直接输出到stdout，不使用PIPE）
        bot_process = subprocess.Popen(
            [sys.executable, 'bot.py'],
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        
        # 启动 Web 服务（直接输出到stdout，不使用PIPE）
        web_process = subprocess.Popen(
            [sys.executable, 'web_server.py'],
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        
        print(f"\n✓ Bot 进程已启动 (PID: {bot_process.pid})")
        print(f"✓ Web 服务已启动 (PID: {web_process.pid})")
        print("\n按 Ctrl+C 停止服务\n")
        
        # 等待进程
        while True:
            bot_status = bot_process.poll()
            web_status = web_process.poll()
            
            if bot_status is not None:
                print(f"\n[错误] Bot 进程已退出 (退出码: {bot_status})")
                break
            if web_status is not None:
                print(f"\n[错误] Web 服务进程已退出 (退出码: {web_status})")
                break
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n正在关闭服务...")
        bot_process.terminate()
        web_process.terminate()
        bot_process.wait()
        web_process.wait()
        print("服务已关闭")

if __name__ == '__main__':
    main()


