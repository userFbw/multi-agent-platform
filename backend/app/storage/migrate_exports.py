#!/usr/bin/env python3
"""一次性数据迁移：产物目录改名为「只用不可变主键」的新布局。

旧：exports/<用户名>/project_<项目id>_<标题>/        + exports/<用户名>/project_<id>.zip
新：exports/u<用户id>/p<项目id>/                    + exports/u<用户id>/p<项目id>.zip
    会话日志也一并搬进项目目录：<项目目录>/_logs/

为什么要迁：
    旧路径含用户名与项目标题 —— 改用户名 / 改标题 / 删号后重建同名账号，都会导致
    「文件还在但接口找不到」。新路径只由主键组成，彻底免疫这三种情况。

用法（默认只预览，不动任何东西）：
    python3 app/storage/migrate_exports.py            # 预览将执行的动作
    python3 app/storage/migrate_exports.py --apply    # 真正执行（含 UPDATE projects.zip_path）
"""
import shutil
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BACKEND_DIR / "data" / "project.db"
EXPORTS_DIR = BACKEND_DIR / "exports"


def _plan():
    """扫描磁盘 + 数据库，产出 (动作列表, 未匹配的孤儿目录)。"""
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        projects = con.execute(
            "select id, user_id, title, zip_path from projects order by id"
        ).fetchall()
    finally:
        con.close()

    moves, zip_updates, matched_dirs, matched_zips = [], [], set(), set()

    for pid, uid, title, zip_path in projects:
        # 1) 项目目录：按 "project_<id>_" 前缀在任意用户名目录下找
        for old_dir in EXPORTS_DIR.glob(f"*/project_{pid}_*"):
            new_dir = EXPORTS_DIR / f"u{uid}" / f"p{pid}"
            moves.append((old_dir, new_dir))
            matched_dirs.add(old_dir)
            # 项目内的 _logs：<用户名>/_logs/project_<id> → <项目目录>/_logs
            old_logs = old_dir.parent / "_logs" / f"project_{pid}"
            if old_logs.is_dir():
                moves.append((old_logs, new_dir / "_logs"))

        # 2) 打包 ZIP
        for old_zip in EXPORTS_DIR.glob(f"*/project_{pid}.zip"):
            new_zip = EXPORTS_DIR / f"u{uid}" / f"p{pid}.zip"
            moves.append((old_zip, new_zip))
            matched_zips.add(old_zip)
            want = f"exports/u{uid}/p{pid}.zip"
            if zip_path != want:
                zip_updates.append((pid, zip_path, want))

    # 3) 孤儿：磁盘上存在、但数据库里没有对应项目的目录 / ZIP / 日志
    orphans = []
    for d in EXPORTS_DIR.glob("*"):
        if not d.is_dir() or d.name.startswith("u") and d.name[1:].isdigit():
            continue
        for child in sorted(d.glob("project_*")):
            if child.is_dir() and child not in matched_dirs:
                orphans.append(child)
            elif child.is_file() and child not in matched_zips:
                orphans.append(child)
        logs = d / "_logs"
        if logs.is_dir():
            for lg in sorted(logs.glob("project_*")):
                if not any(m[0] == lg for m in moves):
                    orphans.append(lg)

    return moves, zip_updates, orphans


def main() -> int:
    apply = "--apply" in sys.argv
    if not DB_PATH.exists():
        print(f"❌ 找不到数据库: {DB_PATH}")
        return 1

    moves, zip_updates, orphans = _plan()

    print(f"═══ 目录/ZIP 搬迁（{len(moves)} 项）═══")
    for old, new in moves:
        print(f"  {old.relative_to(BACKEND_DIR)}  →  {new.relative_to(BACKEND_DIR)}")
    if not moves:
        print("  （无）")

    print(f"\n═══ zip_path 更新（{len(zip_updates)} 条）═══")
    for pid, old, new in zip_updates:
        print(f"  project {pid}: {old}  →  {new}")
    if not zip_updates:
        print("  （无）")

    if orphans:
        print(f"\n⚠️  孤儿产物（{len(orphans)} 项，数据库里已无对应项目，本次不动）")
        for o in orphans:
            print(f"  {o.relative_to(BACKEND_DIR)}")

    if not apply:
        print("\n（预览模式，未做任何改动。加 --apply 真正执行）")
        return 0

    print("\n═══ 执行 ═══")
    for old, new in moves:
        new.parent.mkdir(parents=True, exist_ok=True)
        if new.exists():                       # 目标已存在 → 合并目录内容
            if old.is_dir():
                for item in old.iterdir():
                    shutil.move(str(item), str(new / item.name))
                old.rmdir()
            else:
                old.unlink()
        else:
            shutil.move(str(old), str(new))
        print(f"  ✅ {old.relative_to(BACKEND_DIR)} → {new.relative_to(BACKEND_DIR)}")

    if zip_updates:
        con = sqlite3.connect(DB_PATH)
        try:
            for pid, _old, new in zip_updates:
                con.execute("update projects set zip_path = ? where id = ?", (new, pid))
            con.commit()
        finally:
            con.close()
        print(f"  ✅ projects.zip_path 已更新 {len(zip_updates)} 条")

    print("\n🎉 迁移完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
