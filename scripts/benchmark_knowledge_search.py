"""Synthetic-only FTS benchmark; never opens the configured production database."""
from __future__ import annotations
import argparse
import json
import platform
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.knowledge.db import canonical,install_schema as install_messages,digest,now_iso
from app.knowledge.insights import install_schema as install_insights
from app.knowledge.search import install_schema,build_index,search


def benchmark(count):
    if shutil.disk_usage(tempfile.gettempdir()).free<2*1024**3:
        raise RuntimeError('临时目录可用空间不足 2GB，停止合成测试')
    with tempfile.TemporaryDirectory(prefix='groupbrief-search-benchmark-') as temporary:
        root=Path(temporary).resolve()
        if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise RuntimeError('合成测试目录边界错误')
        path=root/'synthetic.db'; output=root/'output';output.mkdir()
        base=sqlite3.connect(path)
        try:
            base.executescript('''PRAGMA journal_mode=WAL; PRAGMA user_version=1;
              CREATE TABLE groups(id INTEGER PRIMARY KEY,wechat_group_id TEXT,deleted_at TEXT);
              INSERT INTO groups VALUES(1,'benchmark',NULL);
              CREATE TABLE schema_migrations(migration_id TEXT PRIMARY KEY,applied_at TEXT,checksum TEXT);''')
            install_messages(path);install_insights(path);install_schema(path)
            base.execute('''INSERT INTO source_batches VALUES(1,'synthetic',1,'group:1','synthetic','synthetic','synthetic',
              'synthetic','{}','2026-08-31T16:00:00.000000+00:00','2026-09-01T16:00:00.000000+00:00','complete',?,'{}','complete',?)''',(count,now_iso()))
            for start in range(0,count,5000):
                messages=[];sources=[]
                for index in range(start,min(start+5000,count)):
                    content=('推荐 Claude Code 编程工具，讨论苹果手机和显示器。' if index%10==0 else '今天交流项目进展、技术方案与群聊日常。')+f'记录{index%1000}'
                    stamp=f'2026-09-01T04:{(index//60)%60:02}:{index%60:02}.000000+00:00'
                    messages.append((index+1,1,'group:1','synthetic',str(index),'upstream',str(index),f'u{index%200}',f'成员{index%200}','reliable',stamp,'text',content,digest(content.encode()),str(index),'messages-1',now_iso(),'valid'))
                    sources.append((1,index,index+1,'group:1','synthetic','{}',str(index),'valid'))
                base.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',messages)
                base.executemany('INSERT INTO message_sources VALUES(?,?,?,?,?,?,?,?)',sources)
                base.commit()
            started=time.perf_counter()
            build_index(path,output)
            index_seconds=time.perf_counter()-started
            results=[]
            for query in ('手机','Claude Code','显示器','项目','不存在的短语'):
                times=[];failures=[]
                for _ in range(12):
                    started=time.perf_counter()
                    try:
                        response=search(path,output,query)
                        assert response['ai_calls']==0
                    except Exception as exc:
                        failures.append(type(exc).__name__+': '+str(exc))
                    times.append((time.perf_counter()-started)*1000)
                results.append({'query':query,'p95_ms':round(sorted(times)[-1],2),'median_ms':round(statistics.median(times),2),'failures':failures})
            return {'messages':count,'index_seconds':round(index_seconds,2),'db_bytes':path.stat().st_size,
                'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'platform':platform.platform(),
                'processor':platform.processor(),'queries':results}
        finally:
            base.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--counts',type=int,nargs='+',default=[200000,1000000])
    args=parser.parse_args()
    for count in args.counts:
        if count<=0 or count>1000000:
            parser.error('合成样本须为 1—1000000 条')
        print(json.dumps(benchmark(count),ensure_ascii=True),flush=True)
