"""P2.2 Pipeline 阶段协议测试。"""

import pytest

from app.pipeline.stage_result import StageDisposition, StageResult


def test_stage_result_proceed_exposes_only_next_value():
    result = StageResult.proceed({"messages": 3})

    assert result.disposition is StageDisposition.CONTINUE
    assert result.is_terminal is False
    assert result.next_value() == {"messages": 3}
    with pytest.raises(RuntimeError, match="没有终止响应"):
        result.terminal_response()


def test_stage_result_stop_exposes_only_terminal_response():
    response = {"status": "held", "error_type": "RESULT_UNKNOWN"}
    result = StageResult.stop(response)

    assert result.disposition is StageDisposition.TERMINAL
    assert result.is_terminal is True
    assert result.terminal_response() == response
    with pytest.raises(RuntimeError, match="没有下一阶段输入"):
        result.next_value()
