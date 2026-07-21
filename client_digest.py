"""
client_digest.py — 고객에게 보낼 주간 요약 만들기

카톡에 통째로 붙여넣을 텍스트 한 덩어리를 만든다.
일일 뉴스(news_report.py)와 목적이 다르다.
  news_report.py  : 나만 보는 것. 10건, 매일, 넓게.
  client_digest.py: 고객이 보는 것. 6~8건, 주 1회, 해석이 붙는다.

실행:
  py client_digest.py            최근 7일 → client_YYYYMMDD.txt
  py client_digest.py 14         최근 14일

만들어지는 파일을 메모장으로 열어
  1) [ ] 로 비워둔 해석 칸을 채우고
  2) 전체 복사해서 카톡에 붙여넣는다

해석은 발행당 한 번만 쓴다 (COMMENT_MODE = "top").
기사마다 쓰게 하면 매주 8줄이 되고, 그건 3주면 그만두게 된다.
맨 위 2~3줄이 차별화의 대부분을 만든다. 그건 사람이 써야 한다.

--- 보내기 전에 확인 ---
사업자가 보내는 뉴스레터는 내용이 뉴스뿐이어도
정보통신망법상 '영리목적 광고성 정보'로 본다.
  - 사전 수신동의를 받은 상대에게만 보낼 것
  - 오후 9시 ~ 오전 8시 발송은 별도 동의가 또 필요
  - 명함 교환은 수신동의가 아니다
동의를 받지 않았다면 블로그에 올리고 링크만 공유하는 편이 안전하다.
"""

import sys
from datetime import datetime, timedelta, timezone

import news_report as nr

KST = timezone(timedelta(hours=9))

# ===================== 설정 =====================

DAYS_BACK = 7
MAX_ITEMS = 8              # 카톡 한 화면에 들어가는 한계
BRAND = "OFFISCAN"
TAGLINE = "발품은 제가 팝니다"
BLOG = "blog.naver.com/dadaissue"
CONTACT = "메이트플러스부동산중개 정소장"
PHONE = ""                 # 넣으면 하단에 표시. 비우면 생략

# 고객에게 보여줄 분야만. 순서가 곧 표시 순서다.
SECTIONS = [
    ("local", "경기남부 현장"),
    ("comm",  "물류·산업 시장"),
    ("policy", "정책·제도"),
    ("build", "자본·금융"),
]

# 분야별 최대 건수. 합이 MAX_ITEMS 보다 커야 유연하게 찬다.
SECTION_MAX = {"local": 3, "comm": 3, "policy": 2, "build": 2}

# 해석을 어디에 넣을지
#   "top"  발행당 한 번, 맨 위에만  (권장)
#   "each" 기사마다              (매주 8줄은 못 쓴다. 3주면 그만두게 된다)
#   "none" 넣지 않음
COMMENT_MODE = "top"


# ===================== 본문 =====================

def pick_for_client(items, max_items=MAX_ITEMS):
    """분야별로 점수 높은 순. 같은 언론사 2건까지."""
    ranked = sorted(items, key=nr.score, reverse=True)
    picked, used = [], {}

    for cat, _ in SECTIONS:
        n = 0
        for it in ranked:
            if n >= SECTION_MAX.get(cat, 2) or len(picked) >= max_items:
                break
            if it["cat"] != cat:
                continue
            o = nr.outlet_of(it["source"])
            if used.get(o, 0) >= 2:
                continue
            picked.append(it)
            used[o] = used.get(o, 0) + 1
            n += 1
    return picked


def draft_opening(picked):
    """
    '이번 주 한마디' 초안.
    빈칸보다 뭐라도 있는 편이 손이 덜 간다.
    사실만 조립하고 해석은 비워둔다. 해석이 정소장 상품이라 대신 못 쓴다.
    """
    locals_ = [i for i in picked if i["cat"] == "local"]
    head = locals_[0] if locals_ else (picked[0] if picked else None)
    if not head:
        return "[ 이번 주 한마디 ]"

    keys = [w for w in ("매각", "공실", "인허가", "준공", "착공", "분양",
                        "임대", "거래", "증설", "지연")
            if w in head["title"]]
    topic = keys[0] if keys else "동향"

    josa = "가" if topic == "동향" else "이"
    return (f"이번 주는 '{head['title'][:28]}' 건이 눈에 띕니다.\n"
            f"[ 이 {topic}{josa} 우리 시장에 뜻하는 바를 2~3줄로 ]")


def build_client_text(picked, days):
    today = datetime.now(KST)
    start = today - timedelta(days=days)

    L = []
    L.append(f"[{BRAND}] 물류·산업 부동산 주간 브리핑")
    L.append(f"{start:%m.%d} ~ {today:%m.%d}")
    L.append("")

    if COMMENT_MODE == "top":
        L.append("▶ 이번 주 한마디")
        L.append(draft_opening(picked))
        L.append("")

    by_cat = {}
    for it in picked:
        by_cat.setdefault(it["cat"], []).append(it)

    for cat, label in SECTIONS:
        rows = by_cat.get(cat)
        if not rows:
            continue
        L.append(f"■ {label}")
        for it in rows:
            L.append(f"· {it['title']}")
            if COMMENT_MODE == "each":
                L.append("  [ 해석 한 줄 ]")
        L.append("")

    L.append("―――――――――")
    L.append(f"{TAGLINE}")
    L.append(f"{CONTACT}")
    if PHONE:
        L.append(PHONE)
    L.append(f"전체 기사 {BLOG}")
    L.append("")
    L.append("※ 수신을 원하지 않으시면 알려주세요.")
    return "\n".join(L)


def build_link_sheet(picked):
    """카톡 본문에는 링크를 안 넣는다. 필요할 때 찾아 쓰도록 따로 둔다."""
    L = ["", "=" * 46, "참고용 원문 링크 (카톡에는 붙이지 말 것)", "=" * 46]
    for it in picked:
        L.append(f"· {it['title']}")
        L.append(f"  {nr.display_source(it)} · {it['link']}")
    return "\n".join(L)


def main():
    days = DAYS_BACK
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            pass

    # news_report 의 수집기를 그대로 쓰되 기간만 늘린다
    nr.HOURS_BACK = days * 24
    print(f"최근 {days}일 수집\n")
    items = nr.collect()
    if not items:
        print("수집된 기사가 없습니다.")
        return

    picked = pick_for_client(items)
    if not picked:
        print("고객용으로 뽑을 기사가 없습니다. 기간을 늘려보세요.")
        return

    text = build_client_text(picked, days)
    path = f"client_{datetime.now(KST):%Y%m%d}.txt"
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text)
        f.write(build_link_sheet(picked))

    print("\n" + "=" * 46)
    print(text)
    print("=" * 46)
    print(f"\n저장: {path}")
    print("메모장으로 열어 맨 위 [ ] 두세 줄만 채운 뒤 카톡에 붙여넣으세요.")
    print(f"글자수 {len(text)}자 (카톡 한 화면 기준 900자 이내 권장)")


if __name__ == "__main__":
    main()
