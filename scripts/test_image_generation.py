"""GroupBrief V2 P5 手动测试入口：Codex `$imagegen` 生图 + 落盘验证。

用法（项目根目录）：
    .venv\\Scripts\\python.exe scripts/test_image_generation.py health
    .venv\\Scripts\\python.exe scripts/test_image_generation.py generate \\
        --prompt-file output/test-data/image_prompt_full.txt \\
        --out output/test-data/test_generated.png
    .venv\\Scripts\\python.exe scripts/test_image_generation.py generate --test-data

`--test-data` 使用项目 output/test-data 下的样例 prompt 生成到 output/test-data/。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.config.settings import get_settings
from app.db import repository as repo
from app.image.codex_generator import CodexImageGenerator
from app.image.image_task import verify_image


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex $imagegen 生图测试")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("health", help="检查 codex 是否可用")
    p_gen = sub.add_parser("generate", help="生图")
    p_gen.add_argument("--prompt-file", help="image_prompt.txt 路径")
    p_gen.add_argument("--out", help="输出图片路径")
    p_gen.add_argument("--test-data", action="store_true", help="使用样例数据")
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 1

    settings = get_settings()
    repo.init_db(settings)
    repo.apply_db_settings(settings)
    generator = CodexImageGenerator(settings=settings)

    if args.cmd == "health":
        ok, detail = generator.health_check()
        print(("✅ " if ok else "❌ ") + detail)
        return 0 if ok else 1

    if args.cmd == "generate":
        prompt_file = Path(args.prompt_file) if args.prompt_file else None
        out = Path(args.out) if args.out else None
        if args.test_data:
            prompt_file = prompt_file or Path("output/test-data/image_prompt_full.txt")
            out = out or Path("output/test-data/test_generated.png")
        if not prompt_file or not out:
            print("缺少 --prompt-file / --out，或使用 --test-data")
            return 1

        print(f"读取 Prompt：{prompt_file}")
        print(f"输出图片：{out}")
        result = generator.generate(prompt_file, out)
        if not result.success:
            print(f"❌ 生图失败：{result.error}")
            return 1
        ok, detail = verify_image(out)
        print(f"✅ 图片已落盘 {out}")
        print(f"   验证：{detail}")
        print(f"   来源：{result.detail.get('source')}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
