#!/usr/bin/env bash
# ============================================================================
# 平台提供的「一键真跑」脚本 —— 由 workflow_engine 自动放进 Agent 工作目录。
#
# 用法:  bash .selftest.sh <代码目录> [要探测的路径 ...]
# 例子:  bash .selftest.sh backend /api/todos /        # 后端（自动识别 uvicorn 模式）
#        bash .selftest.sh frontend                    # 前端（自动识别静态模式）
#        bash .selftest.sh .                           # 简单链路（产物直接在当前目录）
#
# 为什么必须是「一次 bash 调用内跑完」：
#   Agent 沙箱里**后台进程不跨 bash 调用存活**（实测：上一调用起的服务，下一次就查不到了），
#   所以本脚本在单次调用内完成：选空闲端口 → 起服务 → 等就绪 → 探测 → 打印真实日志
#   → 杀掉服务（trap EXIT，任何退出路径都不留孤儿）。
#
# 两种模式自动识别：
#   web    ：目录里有 main.py → 起 `uvicorn main:app`，打接口看状态码（5xx/404/连不上即失败）；
#            除了命令行给的路径，还会从 /openapi.json 把**所有不带参数的 GET 路由**自动挖出来一起探
#            —— 免得 Agent 只挑自己写对的那个接口报上来
#   static ：没有 main.py → 起 `http.server` 静态托管，并把目录里所有
#            .html/.js/.css 逐个探一遍（路径写错/文件漏产出会直接 404），
#            同时用 `node --check` 真解析每个 .js
#
# 结束时**由脚本自己**打印结论行 —— 不要自己手写这一行，实测 Agent 会照着格式虚报：
#   [SELFTEST] runtime=PASS passed=<通过数> failed=0 detail=...
#   [SELFTEST] runtime=FAIL passed=<通过数> failed=<失败数> detail=...
# 退出码：0 = 全通过，1 = 有失败（失败时上方一定是真实报错，那才是要修的东西）。
# ============================================================================
set -u

DIR="${1:-.}"
shift 2>/dev/null || true
PROBES=("$@")
if [ "${#PROBES[@]}" -eq 0 ]; then
    PROBES=("/")
fi

if [ ! -d "$DIR" ]; then
    echo "❌ 目录不存在: $DIR"
    echo "[SELFTEST] runtime=FAIL passed=0 failed=1 detail=代码目录不存在:$DIR"
    exit 1
fi

WORKROOT="$(pwd)"          # 运行记录写在代码目录之外的工作目录根

cd "$DIR" || exit 1

if [ -f main.py ]; then
    MODE=web
else
    MODE=static
fi

# ---- 1) 选一个真正空闲的端口（平台后端自己占着 8000，不能写死） ----
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
LOG="$(pwd)/run_check.log"

# ---- 2) 起服务 ----
if [ "$MODE" = web ]; then
    echo "▶ 模式=web（uvicorn main:app，端口 $PORT）"
    python3 -m uvicorn main:app --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
else
    echo "▶ 模式=static（http.server 静态托管，端口 $PORT）"
    python3 -m http.server "$PORT" --bind 127.0.0.1 >"$LOG" 2>&1 &
fi
PID=$!
trap 'kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null' EXIT

# ---- 3) 等就绪（最多 30 秒；进程提前死掉就立刻停等） ----
READY=0
for _ in $(seq 1 60); do
    kill -0 "$PID" 2>/dev/null || break
    if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then READY=1; break; fi
    sleep 0.5
done

if [ "$READY" -ne 1 ]; then
    echo "❌ 服务没能起来（端口 $PORT）"
    echo "=== 服务端原始日志 ==="
    tail -40 "$LOG"
    echo "[SELFTEST] runtime=FAIL passed=0 failed=1 detail=服务启动失败，真实报错见上方日志"
    exit 1
fi

PASS=0
FAIL=0
DETAIL=""

# ---- 4a) 静态模式：目录里已有的资源 + HTML 里**引用**的资源，都探一遍 ----
# 后者才是关键：引用了 style.css 却没产出，读代码看不出来，一打开页面就是 404。
if [ "$MODE" = static ]; then
    EXIST=$(find . -maxdepth 3 -type f \( -name '*.html' -o -name '*.js' -o -name '*.css' \) | sed 's|^\./||')
    REFER=$(while IFS= read -r html; do
                d=$(dirname "$html"); d=${d#.}
                grep -oE '(src|href)="[^"]+"' "$html" 2>/dev/null \
                    | sed -E 's/.*="([^"]+)"/\1/' \
                    | grep -vE '^(https?:)?//|^#|^data:|^mailto:|^/api' \
                    | sed "s|^|$d/|"
            done < <(find . -maxdepth 3 -type f -name '*.html'))
    while IFS= read -r f; do
        [ -n "$f" ] && PROBES+=("/$f")
    done < <(printf '%s\n%s\n' "$EXIST" "$REFER" | sed 's|^\./||; s|^/||' | grep -v '^$' | sort -u)
fi

# ---- 4a-2) web 模式：从 /openapi.json 把**所有不带参数**的 GET 路由挖出来逐个探 ----
# 只探 Agent 自己列的那几个接口是不够的：它很可能漏写，或只挑自己写对的那个。
if [ "$MODE" = web ]; then
    OPENAPI="$WORKROOT/.openapi.json"
    curl -s "http://127.0.0.1:$PORT/openapi.json" -o "$OPENAPI"
    AUTO=$(python3 - "$OPENAPI" <<'PYEOF2'
import json, pathlib, sys
try:
    spec = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    raise SystemExit            # 取不到 openapi.json 就退回只探传进来的路径
for path, ops in (spec.get("paths") or {}).items():
    if "get" in ops and "{" not in path:      # 带路径参数的不给值没法探
        print(path)
PYEOF2
)
    rm -f "$OPENAPI"
    while IFS= read -r p; do
        [ -n "$p" ] && PROBES+=("$p")
    done <<< "$AUTO"
fi

# ---- 4b) 逐个探测（同一路径只探一次）----
mapfile -t PROBES < <(printf '%s\n' "${PROBES[@]}" | awk '!seen[$0]++')

for p in "${PROBES[@]}"; do
    CODE=$(curl -s -o resp.tmp -w '%{http_code}' "http://127.0.0.1:$PORT$p")
    BODY=$(head -c 200 resp.tmp | tr -s "\n" " ")     # 压成一行，别把整页 HTML 糊到输出里
    if [ "${CODE:0:1}" = "5" ] || [ "$CODE" = "000" ] || [ "$CODE" = "404" ]; then
        FAIL=$((FAIL + 1))
        DETAIL="$DETAIL ${p}->HTTP${CODE}"
        echo "❌ GET $p -> HTTP $CODE"
        echo "   $BODY"
    else
        PASS=$((PASS + 1))
        echo "✅ GET $p -> HTTP $CODE"
        echo "   $BODY"
    fi
done
rm -f resp.tmp

# ---- 4c) JS 真解析（node --check 是真语法解析，比"读代码文本"可靠） ----
while IFS= read -r js; do
    [ -n "$js" ] || continue
    if ERR=$(node --check "$js" 2>&1); then
        PASS=$((PASS + 1))
        echo "✅ node --check $js"
    else
        FAIL=$((FAIL + 1))
        DETAIL="$DETAIL ${js}->JS语法错"
        echo "❌ node --check $js"
        echo "$ERR" | head -15
    fi
done < <(find . -maxdepth 3 -type f -name '*.js' | sed 's|^\./||')

# ---- 5) 记一笔运行记录（平台脚本自己写，Agent 说的话不算数）----
# 多次运行会往后追加，平台据此产出「项目BUG/第 N 次运行」。
RUNS="$WORKROOT/.selftest_runs.md"
record_run() {                                # $1 = PASS / FAIL
    local n
    n=$(grep -c '^### 第' "$RUNS" 2>/dev/null || true)
    n=$(( ${n:-0} + 1 ))
    {
        echo "### 第 $n 次运行 · $(date '+%Y-%m-%d %H:%M:%S')"
        echo "- 模式：$MODE　命令：\`bash .selftest.sh $DIR ${PROBES[*]}\`"
        echo "- 结论：\`[SELFTEST] runtime=$1 passed=$PASS failed=$FAIL detail=$DETAIL\`"
        if [ "$1" = FAIL ]; then
            echo "- 真实报错："
            echo '  ```'
            grep -v "site-packages" "$LOG" | tail -12 | sed 's/^/  /'
            echo '  ```'
        fi
        echo
    } >> "$RUNS"
}

# ---- 6) 出结论 ----
if [ "$FAIL" -eq 0 ]; then
    DETAIL="全部探测通过"
    record_run PASS
    echo "[SELFTEST] runtime=PASS passed=$PASS failed=0 detail=$DETAIL"
    exit 0
fi
record_run FAIL

echo "=== 服务端原始日志（真实报错与行号在这里）==="
tail -30 "$LOG"
if [ "$MODE" = web ]; then
    echo
    echo "=== 只保留你自己代码的报错帧（site-packages 已滤掉，要修的就是这几行）==="
    grep -v "site-packages" "$LOG" | tail -20
fi
echo "[SELFTEST] runtime=FAIL passed=$PASS failed=$FAIL detail=$DETAIL"
exit 1
