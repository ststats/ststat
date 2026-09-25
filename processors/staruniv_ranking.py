"""티어 안 순위(티어랭킹)를 계산해 docs/data/h2h/index.json에 적어 넣는다.

build_h2h.py가 index.json을 만든 뒤에 돌린다(그 파일을 읽어서 순위 필드만 덧붙인다).

[왜 그냥 Elo를 돌리지 않는가]
이 판은 티어끼리만 논다. 실측하면 이렇다:

    카드군끼리(갓~스페이드)   105,785경기
    숫자군끼리(0~베이비)      230,232경기
    카드군 ↔ 숫자군             2,009경기   ← 두 덩어리를 잇는 전부

갓·킹·잭·조커는 경기의 99.5~100%를 카드군하고만 치고, 3티어 아래는 카드군과 한 경기도
안 한다. 이 상태에서 전원 1500으로 출발하는 보통 Elo를 돌리면 두 덩어리가 각자 1500
근처에서 퍼질 뿐이라, 경기 수가 많은 숫자군이 위로 떠버린다. 실제로 그렇게 나왔다:

    갓 1758 · 킹 1674 · 잭 1509 · 조커 1410   vs   0티어 1819 · 1티어 1803 · 2티어 1753

각 군 안에서는 순서가 완벽했다(갓>킹>잭>조커, 0>1>…>8>베이비). 어긋난 건 두 군의
절대 높이뿐이다. K값이나 공식을 바꿔서 될 문제가 아니라, 연결망이 끊겨 있어서 생기는
구조적인 문제다.

[휴면·체크 선수를 어떻게 둘 것인가 - 여기서 한 번 크게 틀렸다]
티어가 안 매겨진 사람(체크 380명 + 티어표 밖 143명)도 경기 상대로는 계속 나온다. 처음엔
이들을 '미분류'라는 티어 하나로 묶고 다른 티어와 똑같은 prior를 걸었는데, 미분류는 실력
구간이 아니라 '아직 안 매김'이라 실력이 전 구간에 퍼져 있다. 좁은 prior로 한데 묶으니
이들이 가짜 닻이 되어 두 덩어리를 엉뚱한 자리에 고정시켰다. 실측으로 확인한 값
(스페이드 vs 0티어 1,204경기에서 스페이드 54.1% 승 = 약 +28점)과 비교하면:

    미분류도 좁은 prior(σ=0.5)      스페이드 - 0티어 = -232점   ← 완전히 뒤집힘
    미분류 prior 풀기(σ=3.0)        스페이드 - 0티어 =  -52점
    + 3판 미만 미분류를 다리에서 뺌   스페이드 - 0티어 =   -4점   ← 채택
    + m을 긴 창(2년)으로 따로 맞춤     스페이드 - 0티어 =  +24점   ← 지금 (아래 [두 반감기])
    미분류 경기를 아예 뺌            스페이드 - 0티어 =  +74점   (다리가 2,009경기뿐이라 튄다)

그래서 미분류는 (1) prior를 넓게 풀어 각자 자유롭게 두고, (2) 가중 3판도 안 둔 사람의
경기는 아예 빼고 맞춘다. 판 수가 그것밖에 안 되면 그 선수의 실력 위치를 알 수 없고,
위치를 모르는 사람을 두 덩어리 사이의 다리로 쓰면 없는 정보를 지어내는 셈이 된다.
(이 선택으로 티어 안 순위도 평균 0.5계단, 최대 6계단 움직였다 - 고칠 값어치가 있었다)

[그래서 2단으로 나눈다]

    선수 실력 θ = (티어 기준선 m) + (티어 안 편차 δ)

  · m은 티어 간 맞대결에서 추정한다. 데이터가 티어 사이 간격을 알려주므로(스페이드 vs
    0티어 1,204경기에서 54.1% 등) 두 군이 공중에 뜨지 않고 한 사다리에 묶인다.
  · δ는 주로 같은 티어끼리의 경기에서 정해진다. 화면에 띄우는 티어 안 순위가 이 δ 순서다.
  · δ에는 0으로 당기는 prior를 걸어, 표본이 적은 선수가 티어 꼭대기나 바닥으로 튀지 않고
    가운데에 놓이게 한다(예전 레이팅이 몇 판 안 한 사람 때문에 이상해졌던 자리다).

순위를 티어 안에서만 매기므로, 두 군의 절대 높이에 오차가 남아도 순위에는 영향이 없다.
(그래도 전체 통합 순위는 내보내지 않는다 - 다리가 2,009경기뿐이라 카드군과 숫자군의
경계는 여전히 ±1칸 남짓 흔들린다)

[두 반감기 - m과 δ는 변하는 속도가 다르다]
개인 폼에 맞춰 반감기를 짧게 잡았더니 티어 사이를 잇는 경기가 같이 깎여 나갔다.
다리가 되는 경기는 대부분 옛날 것이라(스페이드 vs 0티어는 통산 1,204경기인데 최근
1년에 17경기뿐이다) 120일을 걸면 그 17경기가 두 군의 높이를 혼자 정해 버린다.
그래서 m은 긴 창(2년)으로, δ는 짧은 창(4개월)으로 따로 맞춘다. 자세한 수치는
HALF_LIFE_TIER_DAYS 주석에 적어뒀다. 이 2단을 넣고서야 티어 기준선 15칸이 처음으로
한 번도 안 뒤집히고 갓 -> 베이비까지 순서대로 섰다.

[순위는 θ로 매긴다 - '보수 추정'을 거쳐 되돌아왔다]
처음엔 θ 순서 그대로 줄을 세웠더니 스페이드티어 1위가 최근 1년에 2판 둔 사람이었다.
그래서 한동안 θ - 1.5·표준오차(보수 추정)로 줄을 세웠는데, 티어 안 편차 폭이 좁아서
(스페이드티어 δ 중앙값 +0.022) 이번엔 표준오차 항이 순서를 정했다 - 실력이 같아도
많이 둔 사람이 위로 갔다(합성 데이터에서 평균 실력·2배 경기량 선수가 16위 -> 7위).
2판짜리가 1위에 서던 문제는 δ prior와 '최근 1년 MIN_RECENT_GAMES판 이상' 문턱이
막으므로, 지금은 θ로 줄 세우고 표준오차는 '데이터 티어' 판정 구간에만 쓴다.

[승급 직후 - 2단은 지금 티어 기준선으로]
티어 기준선 m은 경기 당시 티어로 맞추지만(승급 전 성적이 새 티어 기준선을 끌어올리지
않게), 개인 편차 δ는 지금 티어 기준선으로 맞춘다. 당시 티어로 맞추면 옛 티어에서
번 δ가 새 티어 위에 그대로 얹혀 승급자가 곧바로 새 티어 상위권에 섰다(solve_two_stage 주석).

[종족 상성]
전 선수 공통의 종족 간 유불리 3개(테→저, 저→토, 토→테)를 m과 함께 긴 창에서 맞춘다.

[경기 가중치]
형식마다 중요도가 다르고(개인대회=대학대회 > 대학대전 > 미니대전 > 프로리그=CK > 스폰),
오래된 경기는 지금 실력을 덜 말해준다(반감기는 폼 4개월 / 티어 간격 2년, 아래 참고).
두 가중치를 곱해 경기마다 붙인다.
스폰은 전체의 73%다. 한 판당 가치는 분명히 낮지만(같은 판 수로 재면 대회 0.6710 vs
스폰 0.6790), 예전처럼 0.15까지 누르면 물량을 너무 많이 버려서 중요경기 예측까지
나빠졌다. 지금은 0.4로 완화했다 - CAT_WEIGHT 주석에 근거를 적어뒀다.

[계산량]
37.5만 경기를 (승자, 패자) 쌍으로 접으면 45,862개다. 같은 쌍의 가중치는 그냥 더하면
되므로(로그가능도가 쌍에만 의존한다) 손실이 없다. 선수 1,249명 + 티어 16개짜리 볼록
문제라 scipy L-BFGS로 1초 안에 끝난다. 직접 짠 경사하강으로도 같은 답에 닿지만,
두 덩어리를 잇는 방향은 기울기가 0.2%밖에 안 돼서 수렴을 눈으로 확인하기 어렵다 -
수렴 판정을 옵티마이저에 맡기려고 L-BFGS를 쓴다.
"""

import argparse
import bisect
import datetime as dt
import io
import re
import json
import math
import os
import sys

import numpy as np
from scipy.optimize import minimize

SRC_PATH = os.path.join('data', 'eloboard.json')
INDEX_PATH = os.path.join('docs', 'data', 'h2h', 'index.json')
# 티어별 승급일이 들어 있는 원본. 월별 그래프에서 '그 달의 티어'를 되살리는 데 쓴다.
DB_PATH = os.path.join('data', 'db.json')
# 레이팅 변화 그래프용. 분석 탭에서만 읽으므로 index.json과 따로 둔다(티어표만 보러 온
# 사람이 받지 않게).
HISTORY_PATH = os.path.join('docs', 'data', 'h2h', 'rating.json')
HISTORY_MONTHS = 18         # 월별 전적 그래프와 같은 개월 수

# 티어 사다리. build_h2h.py / core.js의 TIER_ORDER와 같은 순서여야 한다.
TIER_ORDER = ['갓', '킹', '잭', '조커', '스페이드', '0', '1', '2', '3', '4', '5', '6', '7', '8', '베이비']
# 아직 티어를 안 매긴 사람(체크)과 티어표 밖 상대. 순위는 안 내지만 노드로는 넣는다 -
# 이 사람들과의 경기도 실력 정보이고, 티어 간 연결을 조금이나마 더 이어준다.
UNRANKED = '미분류'
# 소속이 이 값이면 지금 쉬는 사람이다. 상대로서는 계산에 그대로 넣되(그 경기도 실력
# 정보다) 순위에는 올리지 않는다 - 지금 뛰는 사람들의 줄 세우기여야 하기 때문.
DORMANT_TEAM = '휴면'

# 형식 가중치. 키는 EloBoard 원본 코드(staruniv_h2h.py의 CAT_LABELS와 같다).
# 값은 시계열 홀드아웃으로 정했다. 두 가지를 따로 물어봤다.
#
# (1) 한 판당 대회가 스폰보다 실력을 더 말해주나? -> 그렇다.
#     각 범주에서 똑같이 26,148판만 뽑아 맞추고 미래의 중요경기를 맞혀봤다:
#       대회+대학대전 0.6710 · 프로리그+CK 0.6736 · 스폰 0.6790
#     순서가 분명하다. 형식을 구분하는 것 자체는 옳다.
#
# (2) 그럼 얼마나 눌러야 하나? -> 예전 0.15는 과했다.
#     스폰이 27.6만판(73.4%)이라 0.15로 누르면 그 정보의 85%를 버린다. 물량이
#     한 판당 가치를 압도해서, 눌렀더니 '중요경기 예측'까지 나빠졌다:
#       스폰 w   전체     중요경기
#         0.15  0.6702   0.6647   <- 예전
#         0.40  0.6653   0.6640
#         1.00  0.6637   0.6634
#     순수 예측만 보면 안 누르는 게 제일 좋지만, 그러면 스폰을 많이 둔 사람이
#     표준오차가 작아져 유리해진다(순위 점수가 θ-SE라서). 그래서 순서는 지키되
#     완화하는 쪽을 택했다. 아래 값으로 다리(스페이드-0티어)도 +24.1 -> +25.4로
#     맞대결 실측(+28.3)에 가까워지고, 표준오차 중앙값은 0.286 -> 0.226으로 준다.
# 2026-09 재검증에서는 형식 간 서열은 유지하되 저가중 범주의 정보 손실을 줄인 완화안
# (미니 0.9 / 프로·CK 0.8 / 스폰 0.6)이 4개 홀드아웃에서 가장 낮은 로그손실을 냈고,
# 최근 승급 61건의 상위권 포착도 유지했다. all-1.0은 티어 다리를 과하게 움직여 쓰지 않았다.
# 2026-09 운영 결정: 대회 > 대학대전 > 미니 > 리그·CK > 스폰을 0.1 간격으로 둔다.
CAT_WEIGHT = {
    'solo_event': 1.0,      # 개인대회
    'college_event': 1.0,   # 대학대회
    'college_war': 0.9,     # 대학대전
    'college_mini': 0.8,    # 미니대전
    'pro_league': 0.7,      # 프로리그
    'team_event': 0.7,      # CK
    'sponsored': 0.6,       # 스폰
    '': 0.6,                # 기타
}
DEFAULT_CAT_WEIGHT = 0.6

# 반감기는 시계열 홀드아웃으로 정했다. 티어표가 1~2달마다 갱신되니 한 번 매긴 순위가
# 버텨야 하는 기간도 그만큼이라, 평가 지평을 30~180일로 나눠 로그손실을 재봤다
# (컷오프 6개 평균). 지평이 무엇이든 120일이 가장 좋았다:
#   지평 30일 - 90일 .6658 · 120일 .6657 · 180일 .6672 · 365일 .6695
#   지평 90일 - 90일 .6683 · 120일 .6680 · 180일 .6690 · 365일 .6705
#   지평180일 - 90일 .6700 · 120일 .6699 · 180일 .6707 · 365일 .6712
# 짧을수록 잘 맞히지만 표본이 얇아져 표준오차가 커진다(365일 0.195 → 180일 0.254 →
# 120일 0.288 → 90일 0.316). 120일은 예측이 가장 좋으면서 90일만큼 출렁이지 않는다.
# 2026-09 재튜닝: 미래 90일 동티어 경기 34,433판(4개 시점)과 최근 실제 승급 61건을
# 함께 검증했다. 75일이 로그손실 기준으로 아주 근소하게 가장 좋았지만 70~90일 구간의
# 차이가 작았고, 운영상 '최근 3개월 폼'으로 설명하기 쉬운 90일을 최종 채택했다.
# 너무 짧은 55일은 다시 성능이 나빠져 최근 데이터 과적합도 피한다.
HALF_LIFE_DAYS = 90.0       # 개인 폼(δ) 반감기(약 3개월)

# 티어 기준선(m)은 개인 폼과 변하는 속도가 다르다. 티어 사다리의 간격은 구조적인
# 값이라 천천히 변하는데, 반감기를 개인 폼에 맞춰 짧게 잡으면 티어 사이를 잇는
# 경기가 같이 깎여 나간다. 카드군(갓~스페이드)과 숫자군(0~베이비)을 잇는 경기는
# 통산 2,009개뿐이고 그나마 대부분 옛날 것이라, 120일을 걸면 다리 가중합이
# 56.6 -> 4.7로 무너지고 두 군의 상대 높이가 통째로 흔들렸다:
#
#   반감기   다리 가중합      스페이드-0티어   간격 불확실성(1시그마)
#    365일   56.6 (0.243%)        -7.1점           ±1.13칸
#    180일   11.6 (0.101%)       -36.8점           ±2.16칸
#    120일    4.7 (0.062%)      -116.9점           ±2.98칸
#
# 그래서 m은 긴 창으로, δ는 짧은 창으로 따로 맞춘다(2단). 맞대결 실측값
# (스페이드 vs 0티어 1,204경기 54.1% = 약 +28점)과 견주면 m의 창이 이렇게 나왔다:
#
#   m=365/δ=120  로그손실 0.6701   스페이드-0티어  -7.1점
#   m=730/δ=120  로그손실 0.6702                 +24.1점   <- 채택
#   m=감쇠없음    로그손실 0.6700                 +43.0점
#
# 예측력은 셋이 사실상 같고(단일 365일 0.6723보다 전부 낫다), 다리만 m=730이
# 실측에 제일 가깝다. 단일 120일(0.6691)보다 0.0011 손해지만 그 값어치가 있다.
# 2026-09 재검증에서는 540일이 730/900일과 예측력이 거의 같거나 조금 낫고,
# 스페이드-0티어 간격이 +28.34점으로 실측(+28.3점)에 가장 가까워 540일을 채택했다.
HALF_LIFE_TIER_DAYS = 540.0  # 티어 기준선(m) 반감기(약 1.5년)
RECENT_DAYS = 365           # 순위를 매길 때 보는 최근 기간
MIN_RECENT_GAMES = 10       # 이 기간에 이보다 적게 뒀으면 순위에서 빼고 '기록 없음'
# 순위는 θ(사후 추정치) 순서로 매긴다. 예전엔 θ - 1.5·표준오차로 줄을 세웠는데,
# 티어 안 편차 폭이 좁아서(δ 중앙값 +0.02) 표준오차 항이 순서를 사실상 정했다.
# 합성 데이터로 재 보니 실력이 정확히 평균인 선수가 남보다 2배 많이 두면 31명 중
# 16위 -> 7위로 올라갔다(θ 순서로는 16위 그대로). 표본이 얇은 선수가 튀는 것은
# δ prior(SIGMA_DELTA)가 이미 0 쪽으로 당겨서 막고, 사실상 안 둔 사람은
# MIN_RECENT_GAMES 문턱이 거른다. 표준오차는 '데이터 티어' 판정 구간에만 쓴다.
DATA_TIER_Z = 1.50          # 데이터 티어: θ ± 이 배수×표준오차가 경계를 완전히 넘을 때만 괴리

# 종족 상성. 선수 개인 실력(δ)과 별개로 전 선수 공통의 종족 간 유불리 3개를 같이 맞춘다
# (테란→저그, 저그→프로토스, 프로토스→테란 방향의 로짓 우위. 반대 방향은 부호만 바뀐다).
# 예전엔 종족 효과가 각자의 δ에 섞여 들어가, 상대 종족 구성이 치우친 선수의 실력이
# 흔들렸다. 엔트리 예상승률도 이 값을 그대로 쓴다.
# 식별 가정: 교차 종족 조합은 셋(TZ·ZP·PT)뿐이라 데이터만으로는 세 상성의 '합'만
# 정해진다. 나머지는 δ prior가 정한다 - 즉 '같은 티어 안에서 종족별 평균 실력은 같다'고
# 본다. 어떤 종족 선수들이 티어 안에서 평균적으로 더 강하다면 그 차이도 상성으로 읽힌다.
RACES = ('T', 'Z', 'P')
RACE_PAIRS = ('TZ', 'ZP', 'PT')
LAMBDA_RACE = 1e-6          # 수치 안정용으로만 아주 약하게 묶는다

# δ에 거는 prior의 폭(표준편차). 로짓 단위에서 티어 한 칸 차이가 약 0.36이고
# (실측 1칸 차 승률 58.8%)이다. 0.4/0.5/0.65를 미래 90일 동티어 경기로 재검증했을 때
# 0.65~0.75 구간을 다시 비교했고, 미래 예측 + 실제 승급자 상위권 포착 + 저표본 억제를
# 전체 구조를 다시 튜닝한 뒤 0.625/0.65/0.675/0.70/0.725를 재비교했고 0.675가
# 로그손실과 승급 포착의 균형이 가장 좋았다. SE 페널티 1.50은 그대로 유지한다.
SIGMA_DELTA = 0.675
# 미분류는 실력 구간이 아니라 '안 매김'이라 전 구간에 퍼져 있다. 좁게 묶으면 가짜 닻이
# 되므로 사실상 자유롭게 둔다(위 주석 참고).
SIGMA_UNRANKED = 3.0
# 미분류 선수 중 가중 경기 수가 이보다 적은 사람의 경기는 맞출 때 뺀다.
MIN_UNRANKED_GAMES = 3.0
# m은 자유롭게 두되(티어 간격을 데이터가 정하게), 수치 안정용으로만 아주 약하게 묶는다.
LAMBDA_M = 1e-6
# 티어 기준선은 계산 본체에서 이 순서를 강제한다. 0이면 인접 티어가 같은 값으로 묶이는
# 것은 허용하되 역전은 불가능하다. 작은 양수는 수치 오차로 인한 역전만 막는다.
MIN_TIER_GAP = 1e-8

# 로짓을 Elo스러운 점수로 바꿀 때 쓰는 배율/기준점. 화면에는 순위만 쓰지만,
# 나중에 점수를 보여주고 싶을 때를 위해 같이 내보낸다.
SCORE_SCALE = 400.0 / math.log(10)
SCORE_BASE = 1500.0


def load_json(path):
    with io.open(path, encoding='utf-8') as f:
        return json.load(f)


def tier_of(entry):
    """index.json의 선수 한 명에서 티어를 꺼낸다. 사다리에 없는 값은 전부 미분류."""
    t = str((entry or {}).get('t') or '').strip()
    return t if t in TIER_ORDER else UNRANKED


def load_ladders(path, players):
    """db.json의 'N티어 승급' 날짜로 선수별 티어 사다리를 만든다.

    월별 그래프는 지금까지 모든 과거 달에 '오늘의 티어'를 붙여 계산했다. 작년에
    8티어였던 사람의 작년 점수를 지금 7티어 기준선으로 재던 셈이라, 승급한 사람의
    선이 과거까지 통째로 들려 올라갔다. 승급일이 있으면 그 달의 티어로 되돌릴 수 있다.

    'N티어 승급' 칸은 그 티어가 된 날짜다. 강등으로 내려간 날짜도 같은 칸에 적으므로(여러 번이면
    쉼표로 이어 적는다) 날짜순으로 티어가 도로 내려가는 사다리도 그대로 쓴다.
    사다리 마지막 티어가 지금 티어와 다른 사람만 오늘의 티어를 그대로 쓴다(기록 안 된 변동이 있어
    '언제 바뀌었는지'를 알 수 없고, 고치려 들면 오히려 없는 정보를 지어낸다).

    반환: 선수id -> [(날짜, 티어), ...] (날짜 오름차순). 여기 없으면 오늘 티어를 쓴다.
    """
    if not os.path.exists(path):
        print(f'   ℹ️ {path} 가 없어 월별 그래프는 오늘 티어를 그대로 씁니다.')
        return {}
    rows = (load_json(path) or {}).get('tierMembers') or []
    cols = [(str(i), f'{i}티어 승급') for i in range(9)]
    out = {}
    skipped_mismatch = 0
    for m in rows:
        pid = str(m.get('ELO ID') or '').strip()
        entry = players.get(pid)
        if not pid or entry is None:
            continue
        events = []
        for tier, col in cols:
            # 한 칸에 날짜가 여럿일 수 있다(강등 뒤 다시 그 티어가 됨: '2021-07-13, 2021-10-26')
            for day in re.split(r'[,\s/]+', str(m.get(col) or '').strip()):
                day = day[:10]
                if not day:
                    continue
                try:
                    dt.date.fromisoformat(day)
                except ValueError:
                    continue
                events.append((day, tier))
        if not events:
            continue
        # 같은 날 두 티어가 적힌 경우가 있다(티어표 첫 등재분). 더 센 쪽을 남긴다.
        events.sort(key=lambda e: (e[0], int(e[1])))
        merged = []
        for day, tier in events:
            if merged and merged[-1][0] == day:
                continue
            merged.append((day, tier))
        if merged[-1][1] != tier_of(entry):
            skipped_mismatch += 1
            continue
        out[pid] = merged
    demoted = sum(1 for lad in out.values()
                  if [int(t) for _, t in lad] != sorted((int(t) for _, t in lad), reverse=True))
    print(f'   승급일 사다리 {len(out):,}명 사용(강등 포함 {demoted}명) · 지금 티어와 어긋남 {skipped_mismatch}명 제외')
    return out


def tier_at(pid, day, ladders, players):
    """day 시점의 티어. 사다리가 없으면 오늘 티어를 그대로 쓴다."""
    lad = ladders.get(pid)
    if not lad:
        return tier_of(players.get(pid))
    iso = day.isoformat()
    tier = lad[0][1]        # 첫 등재 전이면 그때 티어로 본다
    for d, t in lad:
        if d > iso:
            break
        tier = t
    return tier


def build_pairs(rows, cats, today, players, ladders, t_pos, category_weights=None):
    """경기 행을 (승자, 패자) 쌍별 가중치 합으로 접는다.

    반감기가 둘이라 가중치도 둘을 한 번에 만든다(같은 쌍 목록을 공유해야 하므로
    두 번 돌지 않는다). ww는 개인 폼용(짧은 창), ww_tier는 티어 간격용(긴 창)이다.

    반환: (승자 인덱스, 패자 인덱스, 폼 가중치, 티어 가중치,
           경기 당시 승자/패자 티어, 선수id 목록,
           선수별 가중 경기 수(폼/티어), 선수별 최근 경기일)
    """
    weights = category_weights or CAT_WEIGHT
    fallback_weight = weights.get('', DEFAULT_CAT_WEIGHT)
    cat_w = [weights.get(c, fallback_weight) for c in cats]
    pair_w = {}
    seen = {}            # 선수id -> 노드 번호
    order = []
    weight_sum = {}      # 노드 번호 -> 가중 경기 수
    last_day = {}        # 노드 번호 -> 최근 경기 날짜(문자열)

    def node(pid):
        key = str(pid)
        if key not in seen:
            seen[key] = len(order)
            order.append(key)
        return seen[key]

    for r in rows:
        if len(r) < 6:
            continue
        _, date, win, lose, _map, cat = r[:6]
        try:
            day = dt.date.fromisoformat(str(date)[:10])
        except ValueError:
            continue
        age = (today - day).days
        if age < 0:
            age = 0
        cw = cat_w[cat] if isinstance(cat, int) and 0 <= cat < len(cat_w) else fallback_weight
        w = cw * 0.5 ** (age / HALF_LIFE_DAYS)
        wt = cw * 0.5 ** (age / HALF_LIFE_TIER_DAYS)
        if w <= 0 and wt <= 0:
            continue
        win_key, lose_key = str(win), str(lose)
        i, j = node(win_key), node(lose_key)
        # 승급 전 경기는 당시 티어 기준선에 놓는다. 개인 편차는 같은 선수 노드에 남아
        # 과거 경기의 정보는 보존하지만, 승급 전 성적이 새 티어 기준선을 끌어올리지 않는다.
        win_tier = t_pos[tier_at(win_key, day, ladders, players)]
        lose_tier = t_pos[tier_at(lose_key, day, ladders, players)]
        pair_key = (i, j, win_tier, lose_tier)
        cur = pair_w.get(pair_key)
        if cur is None:
            pair_w[pair_key] = [w, wt]
        else:
            cur[0] += w
            cur[1] += wt
        for n in (i, j):
            s = weight_sum.get(n)
            if s is None:
                weight_sum[n] = [w, wt]
            else:
                s[0] += w
                s[1] += wt
            if str(date) > last_day.get(n, ''):
                last_day[n] = str(date)

    keys = list(pair_w.keys())
    wi = np.fromiter((k[0] for k in keys), dtype=np.int64, count=len(keys))
    li = np.fromiter((k[1] for k in keys), dtype=np.int64, count=len(keys))
    win_tier_idx = np.fromiter((k[2] for k in keys), dtype=np.int64, count=len(keys))
    lose_tier_idx = np.fromiter((k[3] for k in keys), dtype=np.int64, count=len(keys))
    ww = np.fromiter((pair_w[k][0] for k in keys), dtype=np.float64, count=len(keys))
    ww_tier = np.fromiter((pair_w[k][1] for k in keys), dtype=np.float64, count=len(keys))
    n = len(order)
    wsum = np.zeros(n)
    wsum_tier = np.zeros(n)
    for k, v in weight_sum.items():
        wsum[k], wsum_tier[k] = v
    return (wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
            order, wsum, wsum_tier, last_day)


def race_code(value):
    """선수 종족을 T/Z/P 한 글자로. 모르면 빈 문자열."""
    text = str(value or '').strip().upper()
    if text in RACES:
        return text
    return {'테란': 'T', '저그': 'Z', '프로토스': 'P', '토스': 'P'}.get(str(value or '').strip(), '')


def race_terms(node_race, wi, li):
    """경기마다 종족 상성 파라미터 번호와 부호. 같은 종족/모르는 종족이면 부호 0.

    반환: (idx, sign) - 승자 기준 로짓 차이에 sign * race[idx]를 더한다.
    """
    lookup = {}
    for k, pair in enumerate(RACE_PAIRS):
        lookup[(pair[0], pair[1])] = (k, 1.0)
        lookup[(pair[1], pair[0])] = (k, -1.0)
    wr = [node_race[i] for i in wi]
    lr = [node_race[j] for j in li]
    idx = np.zeros(len(wi), dtype=np.int64)
    sign = np.zeros(len(wi), dtype=np.float64)
    for n, key in enumerate(zip(wr, lr)):
        hit = lookup.get(key)
        if hit:
            idx[n], sign[n] = hit
    return idx, sign


# 최적화 결과를 게시해도 되는지의 기준. 실제 경기 37만 건으로 잰 36번의 최적화는 모두 수렴했고
# 남은 기울기는 최대 0.0038이었다. 수렴 보고가 없어도 기울기가 이 값(그 10배 넘게 여유) 안이면
# 경고만 하고 쓰고, 넘거나 값이 유한하지 않으면 멈춘다 - 파생 작업이 실패로 끝나 지금 게시된
# 스냅샷이 그대로 남는다.
MAX_UNCONVERGED_GRAD = 0.05


def accept_solution(res, label, bounds=None):
    x = np.asarray(res.x, dtype=float)
    g = np.array(res.jac, dtype=float)
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(g))):
        raise RuntimeError(f'{label}: 해에 유한하지 않은 값이 있어 결과를 쓰지 않습니다 ({res.message})')
    if res.success:
        return
    if bounds:
        # 경계에 붙은 변수는 경계 바깥쪽을 향하는 기울기가 남는 게 정상이라 뺀다(사영 기울기)
        for i, (lo, hi) in enumerate(bounds):
            if lo is not None and x[i] <= lo + 1e-9 and g[i] > 0:
                g[i] = 0.0
            if hi is not None and x[i] >= hi - 1e-9 and g[i] < 0:
                g[i] = 0.0
    worst = float(np.max(np.abs(g))) if g.size else 0.0
    if worst > MAX_UNCONVERGED_GRAD:
        raise RuntimeError(f'{label}: 수렴하지 않았고 남은 기울기 {worst:.4f}가 커서 결과를 쓰지 않습니다 ({res.message})')
    print(f'   ⚠️ {label}: 수렴 보고는 없지만 남은 기울기 {worst:.4f}로 기준 안이라 사용합니다 ({res.message})')


def fit_delta(wi, li, ww, win_tier_idx, lose_tier_idx, lam, n_players, m,
              race=None, race_idx=None, race_sign=None):
    """티어 기준선 m(과 종족 상성)을 고정한 채 티어 안 편차 δ만 맞춘다(2단 중 2단).

    m은 긴 창에서 이미 정해졌으므로 여기서는 건드리지 않는다. 같은 볼록 문제에서
    m 블록만 빠진 꼴이라 수렴은 더 쉽다.
    """
    base_diff = m[win_tier_idx] - m[lose_tier_idx]
    if race is not None:
        base_diff = base_diff + race_sign * race[race_idx]

    def fun_grad(delta):
        d = np.clip(base_diff + delta[wi] - delta[li], -60, 60)
        f = float(np.sum(ww * np.logaddexp(0.0, -d)) + 0.5 * np.sum(lam * delta ** 2))
        resid = ww / (1.0 + np.exp(d))
        g = np.zeros(n_players)
        np.add.at(g, wi, -resid)
        np.add.at(g, li, resid)
        return f, g + lam * delta

    res = minimize(fun_grad, np.zeros(n_players), jac=True, method='L-BFGS-B',
                   options={'maxiter': 20000, 'maxfun': 40000, 'ftol': 1e-14, 'gtol': 1e-9})
    accept_solution(res, 'δ 최적화')
    return res.x


def solve_two_stage(wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
                    current_tier_idx, lam, n, n_tiers, wsum, wsum_tier, node_race=None):
    """1단으로 티어 간격 m과 종족 상성(긴 창), 2단으로 개인 폼 δ(짧은 창)를 맞춘다.

    [2단은 '지금 티어' 기준선으로 맞춘다 - 승급 직후 과대평가를 막기 위해]
    1단(m)은 경기 당시 티어로 놓아야 승급 전 성적이 새 티어 기준선을 끌어올리지 않는다.
    그런데 2단까지 당시 티어로 놓으면, 5티어에서 잘해서 번 δ가 승급 뒤 4티어 기준선
    위에 그대로 얹혀 '승급하는 날 실력이 한 칸 뛰었다'고 가정하는 셈이 된다.
    합성 데이터(1년 내내 4티어 평균 실력, 20일 전 승급)로 재 보니 θ가 +0.28로짓
    (티어 한 칸의 78%) 부풀어 31명 중 5위에 섰다. 2단을 지금 티어로 맞추면
    오차가 +0.04로 줄고 16위 근처로 돌아온다. δ는 90일 창의 '지금 폼'이라, 그 창
    안에서는 실력이 일정하다고 보는 편이 맞다.

    반환: (theta, standard_error, keep, m, race)
      keep은 짧은 창 기준으로 살아남은 쌍 마스크, race는 RACE_PAIRS 순서의 로짓 우위.
    """
    if node_race is None:
        node_race = [''] * n
    race_idx, race_sign = race_terms(node_race, wi, li)
    unranked = lam < 1.0 / SIGMA_DELTA ** 2      # prior가 넓으면 미분류
    # 판 적은 미분류는 두 단 모두에서 다리로 쓰지 않는다. 다만 문턱을 각자의 창으로
    # 재야 한다 - 긴 창에서는 충분히 둔 사람이 짧은 창에서만 얇아지는 일이 흔하다.
    thin_t = unranked & (wsum_tier < MIN_UNRANKED_GAMES)
    keep_t = ~(thin_t[wi] | thin_t[li])
    m, _, race = fit(
        wi[keep_t], li[keep_t], ww_tier[keep_t],
        win_tier_idx[keep_t], lose_tier_idx[keep_t], lam, n, n_tiers,
        race_idx=race_idx[keep_t], race_sign=race_sign[keep_t])

    thin = unranked & (wsum < MIN_UNRANKED_GAMES)
    keep = ~(thin[wi] | thin[li])
    cur_w = current_tier_idx[wi]
    cur_l = current_tier_idx[li]
    delta = fit_delta(
        wi[keep], li[keep], ww[keep], cur_w[keep], cur_l[keep], lam, n, m,
        race=race, race_idx=race_idx[keep], race_sign=race_sign[keep])
    theta = m[current_tier_idx] + delta
    match_diff = (theta[wi[keep]] - theta[li[keep]]
                  + race_sign[keep] * race[race_idx[keep]])
    standard_error = standard_errors(lam, wi[keep], li[keep], ww[keep], match_diff)
    return theta, standard_error, keep, m, race


def standard_errors(lam, wi, li, ww, match_diff):
    """선수별 표준오차 = 1 / sqrt(prior 정밀도 + Σ w·p(1-p))."""
    d = np.clip(match_diff, -60, 60)
    p_hat = 1.0 / (1.0 + np.exp(-d))
    info = ww * p_hat * (1.0 - p_hat)
    prec = lam.copy()
    np.add.at(prec, wi, info)
    np.add.at(prec, li, info)
    return 1.0 / np.sqrt(prec)


def monotone_tier_levels(values, weights=None):
    """티어 기준선을 강한 티어부터 단조 감소하도록 보정한다."""
    vals = [float(v) for v in values]
    raw_weights = [1.0] * len(vals) if weights is None else weights
    ws = [max(float(w), 1.0) for w in raw_weights]
    blocks = []
    for idx, (value, weight) in enumerate(zip(vals, ws)):
        blocks.append([idx, idx, value, weight])
        while len(blocks) >= 2 and blocks[-2][2] < blocks[-1][2]:
            right = blocks.pop()
            left = blocks.pop()
            total = left[3] + right[3]
            mean = (left[2] * left[3] + right[2] * right[3]) / total
            blocks.append([left[0], right[1], mean, total])
    out = [0.0] * len(vals)
    for start, end, mean, _weight in blocks:
        for idx in range(start, end + 1):
            out[idx] = mean
    return np.asarray(out, dtype=np.float64)


def infer_data_tier(current_index, theta, standard_error, levels, max_steps=2):
    """불확실성 구간이 인접 티어 경계를 완전히 넘을 때만 괴리로 판정한다."""
    idx = int(current_index)
    lower = float(theta) - DATA_TIER_Z * float(standard_error)
    upper = float(theta) + DATA_TIER_Z * float(standard_error)
    for _ in range(max_steps):
        if idx > 0 and lower > (levels[idx - 1] + levels[idx]) / 2.0:
            idx -= 1
            continue
        if idx + 1 < len(levels) and upper < (levels[idx] + levels[idx + 1]) / 2.0:
            idx += 1
            continue
        break
    return idx


def tier_parameters_to_levels(params, n_tiers):
    """기준점+양수 간격 파라미터를 강한 티어부터 단조 감소하는 기준선으로 바꾼다."""
    ranked_count = len(TIER_ORDER)
    anchor = float(params[0])
    gaps = np.asarray(params[1:ranked_count], dtype=np.float64)
    ranked = anchor - np.concatenate(([0.0], np.cumsum(gaps)))
    if n_tiers == ranked_count:
        return ranked
    return np.concatenate((ranked, np.asarray(params[ranked_count:n_tiers], dtype=np.float64)))


def level_gradient_to_parameters(level_gradient, n_tiers):
    """티어 기준선 기울기를 기준점+간격 파라미터 기울기로 변환한다."""
    ranked_count = len(TIER_ORDER)
    g = np.asarray(level_gradient, dtype=np.float64)
    out = np.zeros(n_tiers, dtype=np.float64)
    out[0] = np.sum(g[:ranked_count])
    for gap_index in range(1, ranked_count):
        out[gap_index] = -np.sum(g[gap_index:ranked_count])
    if n_tiers > ranked_count:
        out[ranked_count:n_tiers] = g[ranked_count:n_tiers]
    return out


def solve_at(rows, cats, as_of, players, t_pos, n_tiers, ladders):
    """as_of 시점까지의 경기만으로 한 번 맞춘다. 반환: (선수id -> 레이팅 θ) 사전.

    최근 가중치의 기준일도 as_of로 잡고, 티어 기준선도 그 시점의 티어로 붙인다 -
    그래야 '그때 기준의 실력'이 나온다.
    """
    (wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
     order, wsum, wsum_tier, last_day) = build_pairs(
        rows, cats, as_of, players, ladders, t_pos)
    if not len(ww):
        return {}
    n = len(order)
    tiers_then = [tier_at(pid, as_of, ladders, players) for pid in order]
    tier_idx = np.fromiter((t_pos[t] for t in tiers_then), dtype=np.int64, count=n)
    unranked = tier_idx == t_pos[UNRANKED]
    lam = np.where(unranked, 1.0 / SIGMA_UNRANKED ** 2, 1.0 / SIGMA_DELTA ** 2)
    node_race = [race_code((players.get(pid) or {}).get('r')) for pid in order]
    theta, _se, _keep, _m, _race = solve_two_stage(
        wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
        tier_idx, lam, n, n_tiers, wsum, wsum_tier, node_race)

    cutoff = (as_of - dt.timedelta(days=RECENT_DAYS)).isoformat()
    out = {}
    for k, pid in enumerate(order):
        if players.get(pid) is None or tiers_then[k] == UNRANKED:
            continue
        if last_day.get(k, '') < cutoff:
            continue
        # 그래프는 실력 추정치 θ를 그린다. 예전 θ-1.5·표준오차는 쉬는 동안 표준오차가
        # 커져 실력은 그대로인데 선이 떨어져 보였다.
        out[pid] = round(float(theta[k]) * SCORE_SCALE + SCORE_BASE, 1)
    return out


def month_ends(last_day, count):
    """마지막 경기일부터 거슬러 올라가며 각 달의 말일을 count개 만든다(오름차순)."""
    y, mth = last_day.year, last_day.month
    out = []
    for _ in range(count):
        nxt = dt.date(y + (mth == 12), 1 if mth == 12 else mth + 1, 1)
        out.append(min(nxt - dt.timedelta(days=1), last_day))
        mth -= 1
        if mth == 0:
            y, mth = y - 1, 12
    return list(reversed(out))


def build_history(rows, cats, players, t_pos, n_tiers, last_day, ladders,
                  cached_payload=None, current_scores=None):
    """달마다 그 시점까지의 경기로 다시 맞춰 '그때의 점수'를 모은다.

    입력 지문이 같은 마감 월은 이전 활성 스냅샷을 재사용하고, 현재 월은 이미
    계산한 현재 랭킹을 공유한다. 캐시가 무효면 마감 월만 전체 재계산한다.
    """
    dated = sorted(rows, key=lambda r: str(r[1])[:10])
    days = [str(r[1])[:10] for r in dated]
    months = month_ends(last_day, HISTORY_MONTHS)
    series = {}
    keys = []
    cached_months = list((cached_payload or {}).get('months') or [])
    cached_players = (cached_payload or {}).get('players') or {}
    cached_by_player = {
        str(pid): dict(zip(cached_months, values or []))
        for pid, values in cached_players.items()
    }
    current_key = last_day.strftime('%Y-%m')
    reused = recalculated = 0
    for i, end in enumerate(months):
        key = end.strftime('%Y-%m')
        keys.append(key)
        if key == current_key and current_scores is not None:
            for pid, value in current_scores.items():
                series.setdefault(pid, {})[key] = value
            reused += 1
            continue
        # 닫힌 달은 입력 해시가 같을 때만 이전 활성 스냅샷의 결과를 쓴다.
        if key != current_key and key in cached_months:
            for pid, values in cached_by_player.items():
                value = values.get(key)
                if value is not None:
                    series.setdefault(pid, {})[key] = value
            reused += 1
            continue
        cut = bisect.bisect_right(days, end.isoformat())
        if cut < 100:
            continue
        scores = solve_at(dated[:cut], cats, end, players, t_pos, n_tiers, ladders)
        for pid, sc in scores.items():
            series.setdefault(pid, {})[key] = sc
        recalculated += 1
    # 달마다 값이 없을 수 있으므로(그 달에 쉬었으면) 자리를 null로 채워 길이를 맞춘다
    out = {pid: [vals.get(k) for k in keys] for pid, vals in series.items()}
    print(f'   월별 이력: 캐시 재사용 {reused}개월 · 재계산 {recalculated}개월')
    return keys, out


def fit(wi, li, ww, win_tier_idx, lose_tier_idx, lam, n_players, n_tiers,
        race_idx=None, race_sign=None):
    """θ = m[티어] + δ 를 가중 로지스틱 최대가능도로 맞춘다(볼록 문제).

    P(i가 j를 이김) = sigmoid(θ_i - θ_j + 종족 상성)
    최소화: -Σ w·log sigmoid(d) + (1/2)Σ λ_i·δ_i² + (λ_m/2)Σm² + (λ_r/2)Σr²

    lam은 선수별 prior 정밀도(1/σ²)다 - 티어가 있는 사람은 좁게, 미분류는 넓게 준다.
    race_idx/race_sign을 주면 종족 상성 3개(RACE_PAIRS)를 같이 맞춘다.
    반환: (m, delta, race)
    """
    n_race = len(RACE_PAIRS)
    if race_idx is None:
        race_idx = np.zeros(len(wi), dtype=np.int64)
        race_sign = np.zeros(len(wi), dtype=np.float64)

    def fun_grad(x):
        delta = x[:n_players]
        m = tier_parameters_to_levels(x[n_players:n_players + n_tiers], n_tiers)
        race = x[n_players + n_tiers:]
        d = np.clip(
            m[win_tier_idx] + delta[wi] - m[lose_tier_idx] - delta[li]
            + race_sign * race[race_idx],
            -60, 60)
        # -log sigmoid(d) = log(1 + e^-d). logaddexp로 넘침 없이 계산한다.
        f = float(np.sum(ww * np.logaddexp(0.0, -d))
                  + 0.5 * np.sum(lam * delta ** 2)
                  + 0.5 * LAMBDA_M * np.sum(m ** 2)
                  + 0.5 * LAMBDA_RACE * np.sum(race ** 2))
        # 1 - sigmoid(d) = sigmoid(-d)
        resid = ww / (1.0 + np.exp(d))
        g_theta = np.zeros(n_players)
        np.add.at(g_theta, wi, -resid)
        np.add.at(g_theta, li, resid)
        g_m = (
            np.bincount(win_tier_idx, weights=-resid, minlength=n_tiers)
            + np.bincount(lose_tier_idx, weights=resid, minlength=n_tiers)
            + LAMBDA_M * m
        )
        g_race = np.bincount(race_idx, weights=-resid * race_sign, minlength=n_race) + LAMBDA_RACE * race
        return f, np.concatenate([
            g_theta + lam * delta,
            level_gradient_to_parameters(g_m, n_tiers),
            g_race,
        ])

    # 첫 기준점과 미분류 기준점은 자유롭고, 실제 티어 사이 간격만 0 이상으로 묶는다.
    x0 = np.zeros(n_players + n_tiers + n_race)
    x0[n_players + 1:n_players + len(TIER_ORDER)] = 0.1
    bounds = [(None, None)] * (n_players + n_tiers + n_race)
    for idx in range(1, len(TIER_ORDER)):
        bounds[n_players + idx] = (MIN_TIER_GAP, None)
    res = minimize(fun_grad, x0, jac=True, method='L-BFGS-B', bounds=bounds,
                   options={'maxiter': 20000, 'maxfun': 40000, 'ftol': 1e-14, 'gtol': 1e-9})
    accept_solution(res, '티어 기준선 최적화', bounds)
    delta = res.x[:n_players].copy()
    m = tier_parameters_to_levels(res.x[n_players:n_players + n_tiers], n_tiers)
    race = res.x[n_players + n_tiers:].copy()
    # θ는 전체를 같이 밀어도 똑같다(차이만 의미가 있다). 티어 기준선의 평균을 0에 묶어
    # 값이 매번 다른 자리에 서지 않게 한다(미분류는 실력 구간이 아니라 평균에서 뺀다).
    m -= m[:len(TIER_ORDER)].mean()
    return m, delta, race


def main():
    ap = argparse.ArgumentParser(description='티어 안 순위(티어랭킹) 계산')
    ap.add_argument('--src', default=SRC_PATH, help=f'전적 아카이브 (기본 {SRC_PATH})')
    ap.add_argument('--index', default=INDEX_PATH, help=f'상대전적 index.json (기본 {INDEX_PATH})')
    ap.add_argument('--db', default=DB_PATH, help=f'승급일이 든 db.json (기본 {DB_PATH})')
    ap.add_argument('--dry-run', action='store_true', help='파일에 쓰지 않고 결과만 찍는다')
    ap.add_argument('--top', type=int, default=0, help='티어별 상위 N명을 찍어본다')
    ap.add_argument('--no-history', action='store_true',
                    help='레이팅 변화(월별 스냅샷) 계산을 건너뛴다')
    ap.add_argument('--reuse-closed-history', action='store_true',
                    help='기존 rating.json의 마감 월 결과를 재사용한다')
    args = ap.parse_args()

    if not os.path.exists(args.src) or not os.path.exists(args.index):
        print(f'ℹ️ {args.src} 또는 {args.index} 가 없어 티어랭킹을 건너뜁니다.')
        return 0

    store = load_json(args.src)
    index = load_json(args.index)
    rows = store.get('rows') or []
    cats = store.get('cats') or []
    players = index.get('players') or {}
    if not rows or not players:
        print('ℹ️ 전적이나 선수 목록이 비어 있어 티어랭킹을 건너뜁니다.')
        return 0

    # 기준일은 아카이브의 마지막 경기일로 잡는다. 오늘 날짜로 하면 수집이 며칠 밀렸을 때
    # 최근 가중치가 통째로 깎여서, 코드를 안 고쳤는데 순위가 흔들린다.
    today = max(dt.date.fromisoformat(str(r[1])[:10]) for r in rows if len(r) > 1)

    tiers = TIER_ORDER + [UNRANKED]
    t_pos = {t: i for i, t in enumerate(tiers)}
    ladders = load_ladders(args.db, players)

    (wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
     order, wsum, wsum_tier, last_day) = build_pairs(
        rows, cats, today, players, ladders, t_pos)
    n = len(order)
    tier_idx = np.fromiter(
        (t_pos[tier_of(players.get(pid))] for pid in order), dtype=np.int64, count=n)

    # 미분류 중 판 수가 너무 적은 사람의 경기는 뺀다. 실력 위치를 모르는 사람을
    # 티어 사이의 다리로 쓰면 없는 정보를 지어내게 된다(맨 위 주석의 표 참고).
    unranked = tier_idx == t_pos[UNRANKED]
    lam = np.where(unranked, 1.0 / SIGMA_UNRANKED ** 2, 1.0 / SIGMA_DELTA ** 2)

    # 1단 티어 간격(긴 창) -> 2단 개인 폼(짧은 창). 표준오차는 짧은 창 기준이다 -
    # '지금 이 선수를 얼마나 아는가'를 재는 값이라 폼과 같은 창이어야 한다.
    node_race = [race_code((players.get(pid) or {}).get('r')) for pid in order]
    theta, standard_error, keep, m, race = solve_two_stage(
        wi, li, ww, ww_tier, win_tier_idx, lose_tier_idx,
        tier_idx, lam, n, len(tiers), wsum, wsum_tier, node_race)
    dropped_pairs = int((~keep).sum())
    wi, li, ww = wi[keep], li[keep], ww[keep]
    # 맞춘 경기가 한 판이라도 남은 선수(판 적은 미분류는 빠졌다)
    fitted = np.bincount(wi, minlength=n) + np.bincount(li, minlength=n) > 0

    # 최근 RECENT_DAYS 안에 실제로 몇 판 뒀는지(가중치 없는 날것). 눈에 보이는 문턱이라
    # 가중치가 아니라 판 수 그대로 센다.
    cutoff_day = (today - dt.timedelta(days=RECENT_DAYS)).isoformat()
    history_current_scores = {
        pid: round(float(theta[k]) * SCORE_SCALE + SCORE_BASE, 1)
        for k, pid in enumerate(order)
        if players.get(pid) is not None
        and tier_of(players.get(pid)) != UNRANKED
        and last_day.get(k, '') >= cutoff_day
    }
    recent_games = {}
    for r in rows:
        if len(r) < 6 or str(r[1])[:10] < cutoff_day:
            continue
        for pid in (str(r[2]), str(r[3])):
            recent_games[pid] = recent_games.get(pid, 0) + 1

    # 순위 대상: 티어가 매겨져 있고(=지금 티어표에 있고), 최근 RECENT_DAYS 안에
    # MIN_RECENT_GAMES판 이상 둔 선수. 휴면인 사람은 맞출 때는 상대로 쓰지만 순위에는 안 올린다.
    ranked = {}          # 티어 -> [(θ, 선수id)]
    skipped_recent = skipped_thin = skipped_dormant = 0
    for k, pid in enumerate(order):
        entry = players.get(pid)
        if entry is None:
            continue                       # 티어표 밖 상대(others)
        t = tier_of(entry)
        if t == UNRANKED:
            continue
        if str(entry.get('tm') or '').strip() == DORMANT_TEAM:
            skipped_dormant += 1
            continue
        n_recent = recent_games.get(pid, 0)
        if n_recent == 0:
            skipped_recent += 1
            continue
        if n_recent < MIN_RECENT_GAMES:
            skipped_thin += 1
            continue
        ranked.setdefault(t, []).append((theta[k], pid))
        # 순위 기준이 θ라 rawRating과 rating은 같은 값이다(기존 소비자 호환용으로 둘 다 둔다).
        # 정렬에는 반올림 전 θ를 그대로 쓴다.
        entry['rawRating'] = round(float(theta[k]) * SCORE_SCALE + SCORE_BASE, 1)
        entry['rating'] = entry['rawRating']

    tier_sizes = {}
    for t, lst in ranked.items():
        lst.sort(key=lambda x: -x[0])
        tier_sizes[t] = len(lst)
        for rank, (_score, pid) in enumerate(lst, start=1):
            players[pid]['k'] = rank

    # 다시 돌릴 때 옛 순위가 남지 않게, 이번에 순위를 못 받은 사람은 지운다.
    ranked_ids = {pid for lst in ranked.values() for _s, pid in lst}

    # 어드민 랭킹의 기간 필터가 선수 샤드 100여 개를 매번 내려받지 않도록,
    # 순위에 오른 선수만 최근 1년/90일/30일 전적을 index.json에 아주 작게 같이 넣는다.
    # 값은 [경기수, 승수]. 전체 전적은 기존 m/w 필드를 그대로 쓴다.
    period_days = (365, 90, 30)
    cutoffs = {days: today - dt.timedelta(days=days - 1) for days in period_days}
    period_stats = {pid: {days: [0, 0] for days in period_days} for pid in ranked_ids}
    for r in rows:
        if len(r) < 6:
            continue
        _, date, win, lose, _map, _cat = r[:6]
        try:
            day = dt.date.fromisoformat(str(date)[:10])
        except ValueError:
            continue
        for pid, won in ((str(win), True), (str(lose), False)):
            if pid not in period_stats:
                continue
            for days in period_days:
                if day >= cutoffs[days] and day <= today:
                    period_stats[pid][days][0] += 1
                    if won:
                        period_stats[pid][days][1] += 1

    for pid, entry in players.items():
        entry.pop('rankScore', None)
        if pid in ranked_ids:
            entry['periodStats'] = {str(days): period_stats[pid][days] for days in period_days}
        else:
            entry.pop('k', None)
            entry.pop('rawRating', None)
            entry.pop('rating', None)
            entry.pop('periodStats', None)

    # 사람이 매긴 실제 티어와 전적 데이터가 가리키는 적합 티어의 차이를 저장한다.
    # 어드민 '티어 괴리' 표시는 이 값을 사용하고, 실제 순위는 기존처럼 현재 티어 안에서만 매긴다.
    raw_levels = np.array([m[t_pos[t]] for t in TIER_ORDER])
    level_weights = [max(tier_sizes.get(t, 0), 1) for t in TIER_ORDER]
    levels = monotone_tier_levels(raw_levels, level_weights)
    pos = {pid: k for k, pid in enumerate(order)}
    for t, lst in ranked.items():
        for _score, pid in lst:
            k = pos[pid]
            current_idx = TIER_ORDER.index(t)
            fit_idx = infer_data_tier(current_idx, theta[k], standard_error[k], levels)
            fit_t = TIER_ORDER[fit_idx]
            gap = TIER_ORDER.index(t) - TIER_ORDER.index(fit_t)
            players[pid]['dataTier'] = fit_t
            players[pid]['tierGap'] = int(gap)
    for pid, entry in players.items():
        if pid not in ranked_ids:
            entry.pop('dataTier', None)
            entry.pop('tierGap', None)

    # 순위와 상관없이 맞춘 모든 선수의 θ와 표준오차(Elo 점수 단위). 엔트리 예상승률이
    # 순위 밖 선수(10판 미만, 미분류)에게도 티어 평균 대신 실제 추정치를 쓰게 한다.
    for k, pid in enumerate(order):
        entry = players.get(pid)
        if entry is None:
            continue
        if fitted[k]:
            entry['theta'] = round(float(theta[k]) * SCORE_SCALE + SCORE_BASE, 1)
            entry['thetaSE'] = round(float(standard_error[k]) * SCORE_SCALE, 1)
        else:
            entry.pop('theta', None)
            entry.pop('thetaSE', None)

    index['ranking'] = {
        'asOf': today.isoformat(),
        'halfLifeDays': int(HALF_LIFE_DAYS),
        'halfLifeTierDays': int(HALF_LIFE_TIER_DAYS),
        'recentDays': RECENT_DAYS,
        'minRecentGames': MIN_RECENT_GAMES,
        # 티어별 순위 인원. 뱃지의 '3위/16명'에서 분모로 쓴다.
        'tierCounts': {t: tier_sizes[t] for t in TIER_ORDER if t in tier_sizes},
        # 티어 기준선(로짓). 화면에는 안 쓰지만 간격이 뒤집혔는지 확인할 때 본다.
        'tierLevels': {t: round(float(m[t_pos[t]]) * SCORE_SCALE + SCORE_BASE, 1) for t in TIER_ORDER},
        # 종족 상성(Elo 점수 단위). 'TZ'가 +10이면 테란이 저그에게 10점 우위, 반대는 -10.
        'raceMatchup': {pair: round(float(race[k]) * SCORE_SCALE, 1) for k, pair in enumerate(RACE_PAIRS)},
    }

    total = sum(tier_sizes.values())
    print(f'✅ 티어랭킹: {total:,}명 순위 매김 '
          f'(휴면 {skipped_dormant:,}명 · 최근 {RECENT_DAYS}일 경기 없음 {skipped_recent:,}명 · '
          f'{MIN_RECENT_GAMES}판 미만 {skipped_thin:,}명)')
    print(f'   기준일 {today} · 반감기 폼 {int(HALF_LIFE_DAYS)}일 / 티어 {int(HALF_LIFE_TIER_DAYS)}일'
          f' · 쌍 {len(ww):,}개'
          f' (판 적은 미분류 제외 {dropped_pairs:,}쌍)')
    print('   종족 상성(Elo 점수): ' + ' · '.join(
        f'{pair[0]}→{pair[1]} {race[k] * SCORE_SCALE:+.1f}' for k, pair in enumerate(RACE_PAIRS)))
    print('   티어 기준선(높을수록 강함):')
    for t in TIER_ORDER:
        if t in tier_sizes:
            print(f'     {t:>4}티어 {tier_sizes[t]:4d}명  {m[t_pos[t]] * SCORE_SCALE + SCORE_BASE:7.1f}')

    # 사람이 매긴 티어와 데이터가 가리키는 티어가 다른 사람을 알려준다.
    # 갓·킹은 '지금 실력 상위 N명'이 아니라 '대회에 올라간 명단'이라 어긋남이 많다
    # (실측: 킹 51.9% · 갓 37.5% vs 잭 3.4% · 1티어 0%). 순위는 사람이 매긴 티어
    # 안에서 그대로 매기고, 이 목록은 티어표를 갱신할 때 참고하라고 로그로만 남긴다.
    off = []
    for t, lst in ranked.items():
        for _s, pid in lst:
            fit_t = players[pid].get('dataTier', t)
            if fit_t != t:
                off.append((TIER_ORDER.index(t) - TIER_ORDER.index(fit_t),
                            players[pid].get('n', pid), t, fit_t))
    if off:
        off.sort(key=lambda x: -abs(x[0]))
        print(f'   ℹ️ 사람이 매긴 티어와 데이터가 어긋난 선수 {len(off)}명 (상위 10명):')
        for gap, nm, t, fit_t in off[:10]:
            arrow = '↑' if gap > 0 else '↓'
            print(f'      {nm} : {t}티어 → 데이터는 {fit_t}티어 {arrow}{abs(gap)}칸')

    if args.top:
        name = lambda pid: players[pid].get('n', pid)
        print()
        for t in TIER_ORDER:
            if t not in ranked:
                continue
            head = ' · '.join(f'{i}위 {name(pid)}' for i, (_s, pid) in enumerate(ranked[t][:args.top], 1))
            print(f'   {t:>4}티어: {head}')

    if args.dry_run:
        print('   (--dry-run: 파일에 쓰지 않았습니다)')
        return 0

    tmp = args.index + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, args.index)

    if not args.no_history:
        cached_payload = None
        if args.reuse_closed_history and os.path.exists(HISTORY_PATH):
            try:
                cached_payload = load_json(HISTORY_PATH)
            except (OSError, ValueError, TypeError) as exc:
                print(f'   ⚠️ 월별 이력 캐시를 읽지 못해 전체 재계산합니다: {exc}')
        months, series = build_history(
            rows, cats, players, t_pos, len(tiers), today, ladders,
            cached_payload, history_current_scores)
        payload = {'asOf': today.isoformat(), 'months': months, 'players': series}
        tmp = HISTORY_PATH + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, HISTORY_PATH)
        size = os.path.getsize(HISTORY_PATH) / 1024
        print(f'✅ 레이팅 변화: {len(series):,}명 · {len(months)}개월 · {size:.0f}KB'
              f' → {HISTORY_PATH}')
    return 0


if __name__ == '__main__':
    # ✅·ℹ️ 같은 글자를 출력한다. Windows 기본 인코딩(cp949)에서는 출력만으로 죽으므로 UTF-8로 고정한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
