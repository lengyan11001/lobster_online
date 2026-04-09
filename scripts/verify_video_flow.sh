#!/bin/bash
# 验证速推生成视频流程：登录 -> 发「生成视频」-> 收集 SSE 事件
set -e
BASE="${LOBSTER_BASE:-http://127.0.0.1:8000}"
echo "[verify] 登录..."
TOKEN=$(curl -s -X POST "$BASE/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=user@lobster.local&password=lobster123" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")
if [ -z "$TOKEN" ]; then echo "[verify] 登录失败"; exit 1; fi
echo "[verify] 发起生成视频对话 (stream, 最多等 180s)..."
curl -s -N -X POST "$BASE/chat/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"用速推生成一个5秒的测试视频，提示词：一只猫在草地上跑"}' \
  --max-time 200 |
while IFS= read -r line; do
  if [[ "$line" == data:* ]]; then
    body="${line#data:}"
    echo "$body" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read().strip())
    t = d.get('type','')
    if t == 'tool_start': print('[SSE] tool_start:', d.get('name'), d.get('args',[]))
    elif t == 'tool_end': print('[SSE] tool_end:', d.get('name'), (d.get('preview') or '')[:120])
    elif t == 'done': print('[SSE] done, reply_len:', len(d.get('reply','')), 'error:', d.get('error'))
    elif t == 'heartbeat': pass
    else: print('[SSE]', t, list(d.keys()))
except Exception as e: print('[SSE parse]', e)
" 2>/dev/null || true
  fi
done
echo "[verify] 结束"
