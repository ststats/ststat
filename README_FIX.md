# ststat Synergy 1000-limit + archive duplicate fix

수정 파일:
- `repositories/synergy_stats.py`
- `scripts/import_synergy_archives.py`

## 고친 내용

1. `tier_members` 전체 로스터를 1000행씩 페이지네이션해서 읽습니다.
   - 현재 일별 통계가 정확히 1000명에서 잘리던 문제 수정.

2. 과거 archive에서 같은 SOOP ID가 하루에 여러 번 나온 경우 먼저 병합합니다.
   - 메타데이터는 비어있지 않은 값 보존.
   - balloons / 방송시간 / 누적시청자 / sponsor W/L은 합산하지 않고 의미 있는 최대값을 보존.
   - 원본에 없던 과거 멤버를 새로 만들지는 않습니다.

## 적용

ststat 루트에 ZIP 내용을 덮어쓴 뒤:

```cmd
git add -A
git commit -m "Fix Synergy roster pagination and archive merge"
git pull --rebase origin main
git push origin main
```

그 다음, 복구한 Synergy archive가 아직 로컬에 있는 상태에서:

```cmd
python scripts\import_synergy_archives.py "C:\Users\ys2ok\OneDrive\문서\synergy"
```

그 다음 GitHub Actions에서 ststat 메인 workflow를 한 번 실행합니다.

## 검증 SQL

```sql
select
  stat_date,
  count(*) as members
from public.daily_member_stats
group by stat_date
order by stat_date;
```

최신 날짜는 더 이상 1000에 고정되지 않아야 합니다.
과거 날짜는 원본 archive 당시 인원 수를 유지하며, 중복 SOOP ID의 분리된 통계가 하나로 보존됩니다.
