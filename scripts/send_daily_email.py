"""GroupBrief 手动邮件发送脚本（V2 文件链路 → SMTP）。

用法（在项目根目录打开终端）：
    .venv\\Scripts\\python.exe scripts/send_daily_email.py --run-date 2026-08-19
    .venv\\Scripts\\python.exe scripts/send_daily_email.py --run-date 2026-08-19 --dry-run   # 只预览不发

说明：
    --run-date 与 run_daily_pipeline.py 的 --date 一致（执行日，统计前一天）。
    自动读取 output/{群名}/{run-date}/ 下启用群的 ranking.txt、run.json 和日报图片，
    按群分别发到数据库设置的 email_recipient。
"""

from __future__ import annotations

import argparse
import json
import smtplib  # 兼容旧测试注入；真实发送实现在 app.services.email_delivery
import sys
import time  # 兼容旧测试注入；真实退避实现在 app.services.email_delivery
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from sqlmodel import Session

from app.config.settings import get_settings
from app.db import repository as repo
from app.image.image_task import detect_image_format, verify_image
from app.scheduler.outcome import ProcessExitCode
from app.services.email_delivery import (
    EmailDeliveryLedger,
    EmailDeliveryResult,
    deliver_email,
)
from app.services.email_service import email_delivery_config_error
from app.services.handoff_service import safe_dir_name


@dataclass(frozen=True)
class GroupMailInput:
    """单个群的邮件输入，避免在发送阶段重新拼接或猜测群数据。"""

    group_name: str
    ranking_text: str
    period_start: str = ""
    period_end: str = ""
    image_path: Path | None = None
    image_enabled: bool = False


_IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
}


def _date_text(value: str | None, fallback: str) -> str:
    value = (value or "").strip()
    return value[:10] if value else fallback[:10]


def _subject_for_block(block: GroupMailInput, fallback_date: str = "") -> str:
    start_date = _date_text(block.period_start, fallback_date)
    end_date = _date_text(block.period_end, start_date)
    period = start_date if start_date == end_date else f"{start_date}～{end_date}"
    return f"群报 GroupBrief｜{block.group_name}｜{period}"


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def build_message(block: GroupMailInput, settings) -> EmailMessage:
    """按单群输入构造一封邮件；正文只使用 ranking.txt 原文。"""
    message = EmailMessage()
    message["Subject"] = _subject_for_block(block)
    message["From"] = settings.email_from or settings.email_smtp_user
    message["To"] = settings.email_recipient
    message.set_content(block.ranking_text.strip())

    if block.image_enabled and block.image_path is not None:
        ok, detail = verify_image(block.image_path)
        if not ok:
            raise ValueError(detail)
        image_format = detect_image_format(block.image_path)
        subtype = _IMAGE_MIME_TYPES.get(image_format or "")
        if subtype is None:
            raise ValueError(f"无法确定图片 MIME 类型：{block.image_path}")
        try:
            image_data = block.image_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"图片读取失败：{block.image_path}") from exc
        filename = f"{safe_dir_name(block.group_name)}-日报图片.{image_format}"
        message.add_attachment(
            image_data,
            maintype="image",
            subtype=subtype.removeprefix("image/"),
            filename=filename,
        )
    return message


def build_email(blocks: list[GroupMailInput] | list[tuple]) -> tuple[str, str]:
    """兼容旧调用的预览构造器；多群仅拼接原始排行榜，不添加包装。"""
    if not blocks:
        return "", ""
    if isinstance(blocks[0], tuple):
        # 旧脚本的五元组为：群名/排行榜/Prompt/统计起/统计止；Prompt 忽略。
        blocks = [
            GroupMailInput(
                group_name=item[0],
                ranking_text=item[1],
                period_start=item[3] if len(item) > 3 else "",
                period_end=item[4] if len(item) > 4 else "",
            )
            for item in blocks
        ]
    subject = _subject_for_block(blocks[0])
    body = "\n\n".join(block.ranking_text.strip() for block in blocks)
    return subject, body


def collect_group_inputs(settings, groups, run_date: str) -> tuple[list[GroupMailInput], list[str]]:
    """读取每个群的排行榜、run.json 和正式日报图片。"""
    out_root = Path(settings.output_dir)
    blocks: list[GroupMailInput] = []
    skipped: list[str] = []
    for group in groups:
        name = group.display_name or group.wechat_group_name
        group_dir = out_root / safe_dir_name(name) / run_date
        ranking_path = group_dir / "ranking.txt"
        if not ranking_path.is_file():
            detail = f"{name}：ranking.txt 不存在（{group_dir}）"
            print(f"  [跳过] {detail}")
            skipped.append(detail)
            continue
        try:
            ranking_text = ranking_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            detail = f"{name}：ranking.txt 无法读取（{exc}）"
            print(f"  [跳过] {detail}")
            skipped.append(detail)
            continue
        if not ranking_text:
            detail = f"{name}：ranking.txt 为空"
            print(f"  [跳过] {detail}")
            skipped.append(detail)
            continue

        run_meta: dict = {}
        run_json = group_dir / "run.json"
        if run_json.is_file():
            try:
                parsed = json.loads(run_json.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    run_meta = parsed
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                # 旧输出可能没有 run.json；损坏的元数据也按兼容模式回退，
                # 但不因此读取 Prompt 文件或临时图片。
                print(f"  [提示] {name}：run.json 无法读取，使用兼容默认值（{str(exc)[:120]}）")

        period_start = str(run_meta.get("period_start") or run_date)
        period_end = str(run_meta.get("period_end") or period_start)
        # 群配置可能在日报文件生成后才开启图片；此时 run.json 仍是旧快照。
        # 邮件发送应与生图自动化一致，优先采用数据库中的当前群配置，
        # 只有当前配置缺失或无法识别时才回退到 run.json。
        snapshot_image_enabled = _coerce_bool(run_meta.get("image_enabled"), default=False)
        image_enabled = _coerce_bool(
            getattr(group, "image_enabled", None),
            default=snapshot_image_enabled,
        )
        image_path = group_dir / "daily_image.png"
        if image_enabled:
            image_ok, image_detail = verify_image(image_path)
            if not image_ok:
                detail = f"{name}：生图已开启，daily_image.png 无效（{image_detail}）"
                print(f"  [跳过] {detail}")
                skipped.append(detail)
                continue

        blocks.append(
            GroupMailInput(
                group_name=name,
                ranking_text=ranking_text,
                period_start=period_start,
                period_end=period_end,
                image_path=image_path if image_enabled else None,
                image_enabled=image_enabled,
            )
        )
    return blocks, skipped


def _send_with_retry(
    message: EmailMessage,
    settings,
    max_attempts: int = 2,
    *,
    ledger: EmailDeliveryLedger | None = None,
) -> EmailDeliveryResult:
    """兼容旧函数名；真实重试所有权统一到 email_delivery。"""
    return deliver_email(
        message,
        settings,
        ledger=ledger,
        max_attempts=max_attempts,
    )


def _delivery_exit_code(
    *,
    sent_count: int,
    already_sent_count: int,
    failed_count: int,
    unknown_count: int,
    skipped_count: int,
) -> int:
    """把逐群交付结果聚合为稳定退出码；跳过项不能伪装成全量成功。"""
    if unknown_count:
        return int(ProcessExitCode.BLOCKED)
    completed_count = sent_count + already_sent_count
    unsent_count = failed_count + skipped_count
    if completed_count > 0 and unsent_count == 0:
        return int(ProcessExitCode.SUCCESS)
    if completed_count > 0 and unsent_count > 0:
        return int(ProcessExitCode.PARTIAL)
    return int(ProcessExitCode.FAILED)


def main() -> int:
    parser = argparse.ArgumentParser(description="GroupBrief 手动邮件发送")
    parser.add_argument("--run-date", help="执行日 YYYY-MM-DD（默认今天）")
    parser.add_argument(
        "--group",
        action="append",
        dest="group_names",
        help="只发送指定完整群名；可重复传入（默认发送全部启用群）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只预览不发送")
    args = parser.parse_args()

    settings = get_settings()
    repo.init_db(settings)
    repo.apply_db_settings(settings)  # 数据库设置优先（收件人/发件人/SMTP 等）

    if not args.dry_run:
        config_error = email_delivery_config_error(settings)
        if config_error:
            print(f"❌ {config_error}（请检查数据库/环境设置）")
            return 1

    run_date = args.run_date or datetime.now().date().isoformat()

    with Session(repo.engine) as session:
        groups = [g for g in repo.list_groups(session, only_enabled=True)]
    if args.group_names:
        requested_names = {name.strip() for name in args.group_names if name.strip()}
        groups = [
            group
            for group in groups
            if (group.display_name or group.wechat_group_name) in requested_names
        ]

    blocks, skipped = collect_group_inputs(settings, groups, run_date)

    if not blocks:
        print(f"❌ 没有可发送的群报告（{run_date}）")
        return 1

    print(f"收件人: {settings.email_recipient}")
    print(f"发件人: {settings.email_from or settings.email_smtp_user}")
    print("主题（各群独立）:")
    print(f"群数: {len(blocks)}")
    for block in blocks:
        print(f"  - {block.group_name}：{_subject_for_block(block)}")

    if args.dry_run:
        for block in blocks:
            print(f"\n===== {block.group_name} 邮件正文预览（前 1200 字）=====")
            print(block.ranking_text[:1200])
            if block.image_enabled and block.image_path:
                image_format = detect_image_format(block.image_path) or "unknown"
                print(f"附件: {block.image_path.name} ({image_format}, {block.image_path.stat().st_size} 字节)")
            else:
                print("附件: 无")
        print("\n[dry-run] 未发送。加 --dry-run 去掉即可真正发送。")
        return 0

    sent_count = 0
    already_sent_count = 0
    failed_count = 0
    unknown_count = 0
    summary: list[str] = []
    ledger = EmailDeliveryLedger(Path(settings.output_dir) / ".email-delivery")
    for block in blocks:
        try:
            message = build_message(block, settings)
        except Exception as exc:
            failed_count += 1
            detail = f"{block.group_name}：邮件构造失败（{str(exc)[:200]}）"
            summary.append(detail)
            print(f"❌ {detail}")
            continue
        delivery = _send_with_retry(message, settings, ledger=ledger)
        if delivery.status == "sent":
            sent_count += 1
            summary.append(f"{block.group_name}：发送成功")
            print(f"✅ {block.group_name} 邮件发送成功")
        elif delivery.status == "already_sent":
            already_sent_count += 1
            summary.append(f"{block.group_name}：已发送，幂等跳过")
            print(f"✅ {block.group_name} 邮件已确认发送，本次跳过")
        elif delivery.outcome_unknown:
            unknown_count += 1
            summary.append(f"{block.group_name}：结果未知（{delivery.detail[:200]}）")
            print(f"⏸️ {block.group_name} 邮件结果未知，已禁止自动重发: {delivery.detail[:300]}")
        else:
            failed_count += 1
            summary.append(f"{block.group_name}：提交前失败（{delivery.detail[:200]}）")
            print(f"❌ {block.group_name} 邮件提交前失败: {delivery.detail[:300]}")

    print("\n===== 逐群发送汇总 =====")
    for item in summary:
        print(f"- {item}")
    if skipped:
        print(f"- 跳过 {len(skipped)} 个不可发送群（不建立 SMTP 发送尝试）")
    return _delivery_exit_code(
        sent_count=sent_count,
        already_sent_count=already_sent_count,
        failed_count=failed_count,
        unknown_count=unknown_count,
        skipped_count=len(skipped),
    )


if __name__ == "__main__":
    raise SystemExit(main())
