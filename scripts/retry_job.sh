#!/usr/bin/env bash
# Supabase 일시 오류로 실패한 단계를 1분 뒤 한 번 더 돌린다. 두 번 돌려도 결과가 같은 작업에만 쓴다.
# usage: scripts/retry_job.sh <job_name>
set -u
python scripts/run_job.py "$1" && exit 0
echo "::warning::$1 실패 - 60초 뒤 한 번 더 시도합니다"
sleep 60
exec python scripts/run_job.py "$1"
