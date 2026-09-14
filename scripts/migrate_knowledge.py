"""Install additive knowledge schema into a NEW database copy, never in place."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.knowledge.db import CHECKSUM, MIGRATION_ID, connect, digest, install_schema
from app.knowledge.insights import CHECKSUM as INSIGHT_CHECKSUM, MIGRATION_ID as INSIGHT_ID, install_schema as install_insights
from app.knowledge.search import CHECKSUM as SEARCH_CHECKSUM, MIGRATION_ID as SEARCH_ID, install_schema as install_search


def migrate(source: Path, output: Path | None = None) -> dict:
    source = source.resolve()
    with connect(source, require_schema=False) as con:
        con.execute('BEGIN')  # Hold a consistent source snapshot through backup.
        core = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                for t in ('groups','runs','group_runs','reports','settings')}
        fingerprints = {t: digest([tuple(row) for row in con.execute(f'SELECT * FROM "{t}" ORDER BY rowid')]) for t in core}
        if con.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('核心 Schema 尚未升级')
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or con.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('源库完整性检查失败')
        extensions = {MIGRATION_ID: CHECKSUM, INSIGHT_ID: INSIGHT_CHECKSUM, SEARCH_ID: SEARCH_CHECKSUM}
        result = {"migration": MIGRATION_ID, "checksum": CHECKSUM, "extensions": extensions,
                  "core_counts": core, "core_hashes": fingerprints, "dry_run": output is None}
        if output is None:
            return result
        output = output.resolve()
        if output == source or output.exists() or output.with_suffix(output.suffix+'.manifest.json').exists():
            raise ValueError('必须使用不存在的新输出路径')
        output.parent.mkdir(parents=True, exist_ok=True)
        # Atomically reserve ownership; never overwrite a raced-in user file.
        output.open('xb').close()
        try:
            with closing(sqlite3.connect(output)) as destination:
                con.backup(destination)
            install_schema(output)
            install_insights(output)
            install_search(output)
            with connect(output) as after:
                for table, count in core.items():
                    if after.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] != count:
                        raise ValueError('核心数据计数发生变化')
                    if digest([tuple(row) for row in after.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]) != fingerprints[table]:
                        raise ValueError('核心数据内容发生变化')
                if after.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('目标库完整性校验失败')
                if after.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('目标库外键校验失败')
                installed = dict(after.execute('SELECT migration_id,checksum FROM schema_migrations'))
                if any(installed.get(key) != value for key, value in extensions.items()):
                    raise ValueError('扩展迁移版本校验失败')
            result.update(output=str(output), output_sha256=digest(output.read_bytes()), integrity='ok')
            output.with_suffix(output.suffix+'.manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            return result
        except BaseException:
            # This call exclusively created this file; source is never touched.
            output.unlink(missing_ok=True)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--dry-run', action='store_true')
    action.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.output), ensure_ascii=False, indent=2))
