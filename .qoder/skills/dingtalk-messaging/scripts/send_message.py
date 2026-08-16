"""钉钉消息发送测试脚本。

通过本 skill 自包含的 dingtalk_lib 发送工作通知消息（文本或 Markdown），
凭证从环境变量 / 项目根目录 .env 读取，不依赖项目源码。

用法（在项目根目录执行）：
    python .qoder/skills/dingtalk-messaging/scripts/send_message.py --users user001,user002 --text "测试消息"
    python .qoder/skills/dingtalk-messaging/scripts/send_message.py --users user001 --title "标题" --markdown "## 正文"
"""

import argparse
import sys
from pathlib import Path

# 同目录的 dingtalk_lib 加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dingtalk_lib import send_markdown, send_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="钉钉工作通知消息发送测试")
    parser.add_argument("--users", required=True, help="接收人 userid，逗号分隔")
    parser.add_argument("--text", help="文本消息内容")
    parser.add_argument("--title", help="Markdown 消息标题")
    parser.add_argument("--markdown", help="Markdown 消息正文")
    args = parser.parse_args()

    user_ids = [u.strip() for u in args.users.split(",") if u.strip()]
    if not user_ids:
        print("错误: --users 不能为空")
        return 1

    if args.text:
        result = send_text(user_ids, args.text)
    elif args.markdown and args.title:
        result = send_markdown(user_ids, args.title, args.markdown)
    else:
        print("错误: 需指定 --text 或 (--title + --markdown)")
        return 1

    print(f"发送结果: {result}")
    return 0 if result.get("errcode") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
