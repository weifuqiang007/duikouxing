#!/usr/bin/env bash
# 6 步 / 8 步对比轮：改 steps + filename_prefix 后串行提交
set -u
cd /root/siton-tmp/aigc
PY=venv-comfy/bin/python
for S in 6 8; do
  $PY - <<EOF
import json
wf = json.load(open('workflow_api_wlh004_6s.json'))
wf['10']['inputs']['steps'] = $S
wf['46']['inputs']['filename_prefix'] = 'it_wlh004_6s_s$S'
json.dump(wf, open('workflow_api_wlh004_6s_s$S.json', 'w'), ensure_ascii=False)
print('wrote workflow_api_wlh004_6s_s$S.json')
EOF
  echo "=== [$(date +%H:%M:%S)] submitting steps=$S ==="
  $PY submit_and_wait.py workflow_api_wlh004_6s_s$S.json || echo "SUBMIT_FAIL s$S"
done
echo STEPS_RUNS_DONE
