"""Qwen 国际版 (chat.qwen.ai) 登录态一键生成与打包脚本 (`scripts/login_qwen.py`).

使用方法:
    python scripts/login_qwen.py

功能:
    1. 自动以有头模式 (Headed Mode) 打开 Playwright 浏览器并访问 https://chat.qwen.ai/
    2. 等待您在浏览器中完成账号登录 (Google / Email)
    3. 登录完成后在终端按回车，脚本将自动保存 Session，并将 data/profiles/qwen_acc1 打包为 qwen_acc1.zip
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT_DIR / "data" / "profiles" / "qwen_acc1"
ZIP_OUTPUT = ROOT_DIR / "qwen_acc1.zip"


def main():
    print("=" * 60)
    print("🚀 正在启动 Qwen 国际版 (chat.qwen.ai) 登录辅助脚本...")
    print("=" * 60)

    os.makedirs(PROFILE_DIR, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ 错误: 未安装 playwright。请先运行: pip install playwright")
        sys.exit(1)

    with sync_playwright() as p:
        print(f"\n🌐 正在打开浏览器 (存储路径: {PROFILE_DIR})...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 800},
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        page = context.new_page()
        page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded")

        print("\n" + "!" * 60)
        print("👉 请在弹出的浏览器窗口中完成 Qwen 国际版账号登录。")
        print("👉 登录成功并进入对话主界面后，返回本终端，按下 [Enter 回车键] 继续...")
        print("!" * 60 + "\n")

        input("按下回车键以保存登录态并打包 >>> ")

        context.close()
        print("\n✅ 浏览器已关闭，Session 登录态已保存至 data/profiles/qwen_acc1。")

    # 打包 zip
    if ZIP_OUTPUT.exists():
        ZIP_OUTPUT.unlink()

    shutil.make_archive(str(ROOT_DIR / "qwen_acc1"), "zip", str(PROFILE_DIR))
    print(f"\n🎉 打包完成！ZIP 文件位置: {ZIP_OUTPUT}")
    print("📦 您现在可以直接将该 qwen_acc1.zip 上传至 Railway Volume 目录下解压！\n")


if __name__ == "__main__":
    main()
