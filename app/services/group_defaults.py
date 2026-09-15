"""新增群沿用现有启用群最常用的完整工作流配置。"""

from collections import Counter

from app.db.models import Group


# 群标识、人工发送目标、专属 Prompt 和限次图片主题属于各群，不参与继承。
WORKFLOW_FIELDS = (
    "provider_preference",
    "schedule_rule",
    "summary_provider",
    "prompt_provider",
    "summary_model",
    "prompt_model",
    "strict_image_fact_check",
    "image_enabled",
    "ranking_template",
    "ranking_count_policy",
    "sender_name_policy",
    "image_prompt_template",
    "wechat_send_enabled",
)


def inherited_group_workflow(groups: list[Group]) -> dict:
    """整套选择，避免混搭 Provider/模型；同票沿用最早建立的配置。"""
    profiles = [
        tuple(getattr(group, field) for field in WORKFLOW_FIELDS)
        for group in sorted(groups, key=lambda group: group.id or 0)
        if group.enabled and group.deleted_at is None and group.wechat_group_id.strip()
    ]
    if not profiles:
        return {}
    profile, _ = Counter(profiles).most_common(1)[0]
    return dict(zip(WORKFLOW_FIELDS, profile))
