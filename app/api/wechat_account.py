"""账号头像预览、显式绑定及不发送消息的核验接口。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.sender.wechat_account import BINDING_KEY, binding_status, candidate_previews, inspect_account, prepare_binding
from app.sender.wechat_native import WindowsWechatDriver, _desktop_mutex

router = APIRouter(prefix="/api/wechat-sender/account", tags=["微信发送账号"])


class AccountBindingRequest(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64)
    account_name: str = Field(min_length=1, max_length=80)


@router.get("")
def status(settings: Settings = Depends(get_settings)):
    result = binding_status(settings)
    if result["bound"]:
        result["verification"] = inspect_account(WindowsWechatDriver(settings))
    return result


@router.post("/candidates")
def candidates(settings: Settings = Depends(get_settings)):
    try:
        with _desktop_mutex(settings.wechat_native_mutex_timeout_seconds):
            return candidate_previews(WindowsWechatDriver(settings))
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/binding")
def bind(payload: AccountBindingRequest, settings: Settings = Depends(get_settings), session: Session = Depends(repo.get_session)):
    if not payload.account_name.strip():
        raise HTTPException(422, "请输入发送账号名称")
    try:
        with _desktop_mutex(settings.wechat_native_mutex_timeout_seconds):
            record = prepare_binding(WindowsWechatDriver(settings), payload.candidate_id, payload.account_name)
            repo.set_setting_value(session, BINDING_KEY, record)
            settings.wechat_sender_account_binding = record
            return status(settings)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/verify")
def verify(settings: Settings = Depends(get_settings)):
    with _desktop_mutex(settings.wechat_native_mutex_timeout_seconds):
        driver = WindowsWechatDriver(settings)
        report = inspect_account(driver, restore=True)
        if report["ok"] and not driver._activate(report["identity"]["hwnd"]):
            report.update(ok=False, detail="已匹配头像，但微信窗口无法激活")
        elif report["ok"]:
            after = inspect_account(driver)
            if not after["ok"] or after.get("identity") != report["identity"]:
                report.update(ok=False, detail="激活后账号窗口或头像发生变化，请重新核验")
        return report
