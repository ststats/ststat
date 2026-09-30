#!/usr/bin/env bash
# 파이프라인 단계를 한 번 더 시도한다: Supabase가 잠깐 느리거나 끊겨 실패한 회차(2026-09-30 derived 시간 초과)를
# 다음 4시간을 기다리지 않고 1분 뒤 다시 돌린다. 두 번 돌려도 결과가 같은 작업에만 쓴다
# (sync_eloboard는 '두 번 연속 안 보이면 삭제' 판정이 앞 실행 기록에 기대므로 쓰지 않는다).
# usage: scripts/retry_job.sh <job_name>
set -u
python scripts/run_job.py "$1" && exit 0
echo "::warning::$1 실패 - 60초 뒤 한 번 더 시도합니다"
sleep 60
exec python scripts/run_job.py "$1"
