from app.ai.prompt_safety import enforce_prompt_budget, sanitize_prompt_text


def test_sanitize_prompt_text_removes_controls_and_normalizes_newlines():
    value = "群\u202e名\r\n第一行\x00\n\n\n\n第二行"

    cleaned, removed = sanitize_prompt_text(value)

    assert "\u202e" not in cleaned
    assert "\x00" not in cleaned
    assert "\r" not in cleaned
    assert removed == 2
    assert "\n\n\n\n" not in cleaned


def test_enforce_prompt_budget_preserves_all_section_headings():
    prompt = "\n".join(
        [
            "【任务】",
            "A" * 5000,
            "【主标题】",
            "今日热聊 2026-08-18",
            "【分镜一】",
            "B" * 5000 + " 票房 500 万",
            "【硬约束】",
            "C" * 5000 + " 1024×1536",
        ]
    )

    result, meta = enforce_prompt_budget(prompt, max_chars=1800, max_bytes=4000)

    assert len(result) <= 1800
    assert len(result.encode("utf-8")) <= 4000
    assert meta["prompt_compacted"] is True
    for heading in ("【任务】", "【主标题】", "【分镜一】", "【硬约束】"):
        assert heading in result
    assert "2026-08-18" in result
    assert "1024×1536" in result


def test_enforce_prompt_budget_is_deterministic():
    prompt = "【任务】\n" + ("群聊内容🙂" * 3000)

    first, first_meta = enforce_prompt_budget(prompt, max_chars=2000, max_bytes=5000)
    second, second_meta = enforce_prompt_budget(prompt, max_chars=2000, max_bytes=5000)

    assert first == second
    assert first_meta == second_meta
