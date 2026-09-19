#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_logs.py —— 把老的 exports/.../_logs 会话日志搬到 data/logs/

老方案把 DSH 会话原始 JSON 放在 `exports/u<uid>/p<pid>/_logs/`，后果：
    1. 被 /previews 静态挂载暴露给浏览器；
    2. 被 zip_project 打进用户下载包（几十 MB 的原始会话）。
新方案统一放 `backend/data/logs/u<uid>/p<pid>/`，与产物彻底分开。

默认 dry-run，只打印将要做什么；加 --apply 才真搬。

用法（仓库根目录）:
    PYTHONPATH=backend .venv/bin/python backend/app/storage/migrate_logs.py
    PYTHONPATH=backend .venv/bin/python backend/app/storage/migrate_logs.py --apply
"""
import shutil
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

EXPORTS = _BACKEND_DIR / "exports"
LOGS = _BACKEND_DIR / "data" / "logs"


def plan() -> list[tuple[Path, Path]]:
    """找出所有待搬的 (老目录, 新目录)。"""
    moves = []
    for old_dir in sorted(EXPORTS.glob("u*/p*/_logs")):
        if not old_dir.is_dir():
            continue
        uid, pid = old_dir.parent.parent.name, old_dir.parent.name
        moves.append((old_dir, LOGS / uid / pid))
    return moves


def main() -> None:
    apply = "--apply" in sys.argv
    moves = plan()
    if not moves:
        print("没有需要搬迁的 _logs 目录，已是最新布局。")
        return

    for old_dir, new_dir in moves:
        n = len(list(old_dir.glob("*.json")))
        print(f"  {old_dir.relative_to(_BACKEND_DIR)}  →  {new_dir.relative_to(_BACKEND_DIR)}  ({n} 个 json)")
        if not apply:
            continue
        new_dir.mkdir(parents=True, exist_ok=True)
        for f in old_dir.glob("*.json"):
            shutil.move(str(f), str(new_dir / f.name))
        try:
            old_dir.rmdir()                     # 只删空目录，有别的文件就留着
        except OSError:
            print(f"    ! {old_dir.name} 非空，保留待人工确认")

    print(f"\n共 {len(moves)} 个项目。", "已搬迁。" if apply else "这是 dry-run，加 --apply 执行。")


if __name__ == "__main__":
    main()
