# 전적 전체 수집 (한 번 쓰고 버리는 저장소)

eloboard 전 경기를 한 번에 받아 스타대학 사이트에 넣을 파일 두 가지를 만든다.
다 받고 나면 이 저장소는 지워도 된다. 이후 갱신은 본 저장소의 `Update Site`가 증분(요청 1~2회)으로 한다.

| 만들어지는 것 | 본 저장소에서 놓을 자리 | 크기 |
| --- | --- | --- |
| `data/eloboard.json` | `data/eloboard.json` | 약 14MB |
| `docs/data/h2h/` | `docs/data/h2h/` | 선수별 파일 수백 개 |

## 순서

1. 새 저장소(비공개여도 됨)를 만들고 **이 폴더의 파일 3개를 그대로 올린다**
   - `.github/workflows/collect.yml`
   - `scripts/sync_eloboard.py`
   - `scripts/build_h2h.py`
2. **Actions 탭 → 전적 전체 수집 → Run workflow**
   - `delay` 는 기본값 2 그대로 두는 것을 권한다(eloboard robots.txt의 Crawl-delay).
   - 약 1시간 걸린다. 창을 닫아도 계속 돈다.
3. 끝나면 실행 화면 아래 **Artifacts → `eloboard-data`** 를 내려받는다.
4. 압축을 풀어 본 저장소에 그대로 넣고 커밋한다.
   ```
   data/eloboard.json
   docs/data/h2h/
   ```
5. 이 저장소는 삭제.

## 참고

- 시크릿도 토큰도 필요 없다. 결과는 아티팩트로만 받으면 된다.
  (이 저장소에도 커밋해두고 싶으면 Run workflow 때 `commit` 을 켜고, Settings → Actions → General →
  Workflow permissions 를 `Read and write` 로 바꾼다.)
- 중간에 끊겨도 **거기까지 받은 것**이 아티팩트로 올라온다. 다시 돌리면 처음부터 받는다.
- 남의 서버에 1,974요청은 가벼운 양이 아니다. 간격을 줄이지 말고, 가능하면 운영자에게 미리 알려두자.
- `build_h2h.py` 는 시너지 명단(`ststats.github.io/synergy`)을 읽어 티어표 선수와 이어붙인다.
  이름이 서로 다른 선수는 본 저장소의 `docs/data/h2h_alias.json` 에
  `{"시너지 닉네임": "eloboard 이름"}` 으로 적어주면 다음 갱신부터 반영된다.
