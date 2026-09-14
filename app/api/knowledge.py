"""Knowledge API is optional, independent from V2 delivery routes."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from app.config.settings import Settings, get_settings
from app.knowledge import service
from app.knowledge.db import Conflict, KnowledgeUnavailable, QueryTimedOut
from app.knowledge.jobs import control

router = APIRouter(prefix="/api/v2", tags=["knowledge"])


def invoke(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except QueryTimedOut as exc:
        raise HTTPException(504,detail={'code':'SEARCH_TIMEOUT','message':str(exc)}) from exc
    except KnowledgeUnavailable as exc:
        raise HTTPException(503, detail={"code": "KNOWLEDGE_UNAVAILABLE", "message": str(exc)}) from exc
    except Conflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, detail="记录不存在") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.get('/memories')
def memories(group_id: int | None=None,memory_type: str | None=None,status: str | None=None,
             limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0),settings: Settings=Depends(get_settings)):
    from app.knowledge.memory import list_memories
    return invoke(list_memories,settings.db_path,group_id,memory_type,status,limit,offset)


class MergePreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id: int=Field(gt=0)
    target_id: int=Field(gt=0)


class MergeRequest(MergePreview):
    expected_version: str=Field(min_length=64,max_length=64)


@router.post('/memories/merge-preview')
def memory_merge_preview(body: MergePreview,settings: Settings=Depends(get_settings)):
    from app.knowledge.memory import merge_preview
    return invoke(merge_preview,settings.db_path,**body.model_dump())


@router.post('/memories/merge')
def memory_merge(body: MergeRequest,settings: Settings=Depends(get_settings)):
    from app.knowledge.memory import merge
    return invoke(merge,settings.db_path,**body.model_dump())


@router.get('/memories/{memory_id}')
@router.get('/memories/{memory_id}/entries')
def memory_detail(memory_id: int,offset: int=Query(0,ge=0),limit: int=Query(30,ge=1,le=100),settings: Settings=Depends(get_settings)):
    from app.knowledge.memory import detail
    return invoke(detail,settings.db_path,memory_id,offset,limit)


class MemoryPatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version: int=Field(gt=0)
    title: str | None=Field(None,min_length=1,max_length=120)
    keywords: list[str] | None=Field(None,max_length=15)
    status: Literal['active','review','archived'] | None=None


@router.patch('/memories/{memory_id}')
def memory_patch(memory_id: int,body: MemoryPatch,settings: Settings=Depends(get_settings)):
    from app.knowledge.memory import patch
    return invoke(patch,settings.db_path,memory_id,**body.model_dump())


class UndoMerge(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation_id: int=Field(gt=0)
    expected_version: int=Field(gt=0)


@router.post('/memories/{memory_id}/undo-merge')
def memory_undo(memory_id: int,body: UndoMerge,settings: Settings=Depends(get_settings)):
    from app.knowledge.memory import undo_merge
    return invoke(undo_merge,settings.db_path,memory_id,**body.model_dump())


@router.get('/knowledge/memory/status')
def memory_status(settings: Settings=Depends(get_settings)):
    from app.knowledge.ai_operations import status
    return invoke(status,settings)


class MemoryBackfill(BaseModel):
    model_config=ConfigDict(extra='forbid')
    group_id: int | None=Field(None,gt=0)
    start: str | None=None
    end: str | None=None
    mode: Literal['reuse','supplement']='reuse'


class MemoryBackfillStart(MemoryBackfill):
    expected_version: str=Field(min_length=64,max_length=64)


@router.post('/knowledge/memory/backfill/preview')
def memory_preview(body: MemoryBackfill,settings: Settings=Depends(get_settings)):
    from app.knowledge.memory_pipeline import preview
    return invoke(preview,settings,**body.model_dump())


@router.post('/knowledge/memory/backfill',status_code=202)
def memory_backfill(body: MemoryBackfillStart,settings: Settings=Depends(get_settings)):
    from app.knowledge.memory_pipeline import start_backfill
    return invoke(start_backfill,settings,**body.model_dump())


class UnknownResolution(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation_id: int=Field(gt=0)
    resolution: Literal['confirmed_not_submitted','abandon']
    note: str=Field(min_length=5,max_length=2000)


@router.post('/knowledge/jobs/{job_id}/resolve-unknown')
def unknown_resolution(job_id: int,body: UnknownResolution,settings: Settings=Depends(get_settings)):
    from app.knowledge.ai_operations import resolve_unknown
    return invoke(resolve_unknown,settings.db_path,job_id,**body.model_dump())


@router.get("/knowledge/status")
def knowledge_status(settings: Settings = Depends(get_settings)):
    try:
        return {**service.status(settings.db_path), "worker_enabled": settings.knowledge_enabled}
    except KnowledgeUnavailable as exc:
        return {"available": False, "worker_enabled": False, "reason": str(exc)}


@router.get("/messages")
def messages(group_id: int | None = None, sender_id: str | None = None,
             start: str | None = None, end: str | None = None, message_type: str | None = None,
             include_deleted: bool = False, include_orphans: bool = False,
             limit: int = Query(20, ge=1, le=100), cursor: str | None = None,
             settings: Settings = Depends(get_settings)):
    return invoke(service.list_messages, settings.db_path, group_id=group_id, sender_id=sender_id,
                  start=start, end=end, message_type=message_type, include_deleted=include_deleted,
                  include_orphans=include_orphans, limit=limit, cursor=cursor)


@router.get("/messages/{message_id}")
def message(message_id: int, settings: Settings = Depends(get_settings)):
    return invoke(service.message_detail, settings.db_path, message_id)


@router.get("/messages/{message_id}/sources")
def sources(message_id: int, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), settings: Settings = Depends(get_settings)):
    return invoke(service.message_sources, settings.db_path, message_id, limit=limit, offset=offset)


@router.get("/messages/{message_id}/context")
def context(message_id: int, radius: int = Query(10, ge=1, le=30), settings: Settings = Depends(get_settings)):
    return invoke(service.message_context, settings.db_path, message_id, radius)


@router.get("/knowledge/jobs")
def jobs(limit: int = Query(50, ge=1, le=100), settings: Settings = Depends(get_settings)):
    return invoke(service.list_jobs, settings.db_path, limit)


@router.post("/knowledge/jobs/{job_id}/pause")
def pause(job_id: int, settings: Settings = Depends(get_settings)):
    return invoke(control, settings.db_path, job_id, "pause")


@router.post("/knowledge/jobs/{job_id}/retry")
def retry(job_id: int, settings: Settings = Depends(get_settings)):
    return invoke(control, settings.db_path, job_id, "retry")


class BackfillFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: int | None = Field(None, gt=0)
    start: str | None = None
    end: str | None = None


class BackfillRequest(BackfillFilter):
    expected_version: str = Field(min_length=64, max_length=64)


@router.post("/knowledge/backfill/preview")
def preview(body: BackfillFilter, settings: Settings = Depends(get_settings)):
    return invoke(service.preview_backfill, settings.db_path, settings.output_dir, **body.model_dump())


@router.post("/knowledge/backfill", status_code=202)
def backfill(body: BackfillRequest, settings: Settings = Depends(get_settings)):
    if not settings.knowledge_enabled:
        raise HTTPException(409, detail="知识 worker 未启用，暂不能启动后台导入")
    return invoke(service.start_backfill, settings.db_path, settings.output_dir, **body.model_dump())


@router.get('/insights')
def insights(group_id: int | None = None, kind: Literal['weekly'] | None = None,
             include_history: bool = False, limit: int = Query(30,ge=1,le=100), settings: Settings = Depends(get_settings)):
    from app.knowledge.insights import list_reports
    return invoke(list_reports,settings.db_path,group_id,kind,include_history,limit)


class InsightBuild(BaseModel):
    model_config = ConfigDict(extra='forbid')
    group_id: int = Field(gt=0)
    kind: Literal['weekly'] = 'weekly'
    day: str


@router.post('/insights/build', status_code=202)
def build_insight(body: InsightBuild, settings: Settings = Depends(get_settings)):
    from app.knowledge.insights import period_bounds
    from app.knowledge.jobs import enqueue
    from app.knowledge.db import connect
    from app.knowledge.service import data_version
    invoke(period_bounds,body.kind,body.day,settings.app_timezone)
    if not settings.knowledge_enabled:
        raise HTTPException(409,detail='知识 worker 未启用')
    def queue():
        with connect(settings.db_path) as con:
            version=data_version(con)
            if not con.execute('SELECT 1 FROM groups WHERE id=? AND deleted_at IS NULL',(body.group_id,)).fetchone():
                raise KeyError(body.group_id)
            # Fail before enqueue when the additive report schema is absent.
            con.execute('SELECT id FROM report_insights LIMIT 0')
        job=enqueue(settings.db_path,'insight',{**body.model_dump(),'data_version':version},group_id=body.group_id,priority=5)
        return {'job_id':job['id'],'status':job['status']}
    return invoke(queue)


@router.get('/insights/{report_id}')
def insight(report_id: int, settings: Settings = Depends(get_settings)):
    from app.knowledge.insights import detail
    return invoke(detail,settings.db_path,report_id)


@router.get('/insights/{report_id}/messages')
def insight_messages(report_id: int, offset: int = Query(0,ge=0), limit: int = Query(20,ge=1,le=100),settings: Settings=Depends(get_settings)):
    from app.knowledge.insights import report_messages
    return invoke(report_messages,settings.db_path,report_id,offset,limit)


@router.get('/search')
def search_messages(q: str = Query(min_length=1,max_length=256), object_type: Literal['message','report','memory']='message',
                    sort: Literal['relevance','time']='relevance',group_id: int | None=None,
                    start: str | None=None,end: str | None=None,sender_id: str | None=None,message_type: str | None=None,memory_type: str | None=None,
                    include_deleted: bool=False,include_orphans: bool=False,limit: int=Query(20,ge=1,le=100),cursor: str | None=None,
                    settings: Settings=Depends(get_settings)):
    from app.knowledge.search import search
    extra={'memory_type':memory_type} if object_type=='memory' else {}
    return invoke(search,settings.db_path,settings.output_dir,q,object_type=object_type,sort=sort,limit=limit,cursor=cursor,
                  group_id=group_id,start=start,end=end,sender_id=sender_id,message_type=message_type,
                  include_deleted=include_deleted,include_orphans=include_orphans,**extra)


@router.get('/search/status')
def search_status(settings: Settings=Depends(get_settings)):
    from app.knowledge.search import search_status
    return invoke(search_status,settings.db_path)


@router.get('/search/reports/{ref:path}')
def search_report(ref: str,expected_hash: str | None=None,settings: Settings=Depends(get_settings)):
    from app.knowledge.search import legacy_report
    return invoke(legacy_report,settings.db_path,settings.output_dir,ref,expected_hash)


class IndexRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rebuild: bool=False


@router.post('/knowledge/index',status_code=202)
def update_index(body: IndexRequest,settings: Settings=Depends(get_settings)):
    from app.knowledge.search import enqueue_index
    if not settings.knowledge_enabled:
        raise HTTPException(409,detail='知识 worker 未启用')
    job=invoke(enqueue_index,settings.db_path,settings.output_dir,body.rebuild)
    return {'job_id':job['id'],'status':job['status']}
