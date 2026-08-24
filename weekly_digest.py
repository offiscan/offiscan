"""
weekly_digest.py — 주간 '터치'용 카톡 메시지 + 웹페이지(GitHub Pages) 생성

news_report.py 의 수집·선별·점수 로직을 그대로 재사용한다.
설계 원칙:
  - 카톡을 자동 발송하지 않는다. '복붙용' 메시지 한 덩어리를 만들어 줄 뿐이다.
    핵심 고객에게 개인적으로 보내는 것이 이 채널의 목적이기 때문.
  - 같은 데이터로 docs/index.html(최신호) + docs/YYYY-MM-DD.html(박제호) 둘 다 만든다.
    카톡 맨 밑 '자세히 보기'는 '박제호(날짜 링크)'로 보낸다.
    → 고객이 나중에 열어도, 지인에게 공유해도 그 주 내용이 그대로 유지된다.
  - news_master.csv 는 건드리지 않는다. (일일 뉴스 파이프라인과 완전 분리)

사용법:
  py weekly_digest.py         7일치 수집 → 웹페이지 생성 + 카톡 메시지 출력/저장
  py weekly_digest.py dry     수집·선별 결과만 확인 (파일 안 만듦)

매주 갈아끼울 것 : WEEKLY_LISTINGS, WEEKLY_BRIEF   (아래 두 개만)
최초 1회 설정   : WEB_URL, CONTACT_PHONE
"""

import os
import sys
import html
import io
import csv
import urllib.parse
import urllib.request
from datetime import datetime
from collections import defaultdict

import news_report as nr
from news_report import pick_balanced, score, norm, display_source, CAT_LABEL, KST


# ===================== 매주 수정 =====================

# 이번 주 추천매물 1~2개. 공개 가능한 것만.
#   tag   : "매각" 또는 "임대"
#   title : 이름(주소)  예) "안성 원곡면 물류센터"
#   area  : 평형        예) "609평"
#   temp  : 온도         예) "냉동+상온" / "상온" / "저온"  (없으면 "")
#   note  : 특징 한 줄    예) "안성JC 6분"
#   img   : 사진 주소     (깃허브에 올린 사진 링크. 없으면 "")
#   link  : 상세 브리프 PDF 주소  (없으면 "")
#
# 아래는 '시트를 못 읽을 때만' 쓰는 예비 목록이다. 평소엔 구글시트에서 읽어온다.
WEEKLY_LISTINGS = [
    {"tag": "매각", "title": "안성 죽산면 상온창고", "area": "대지 740평 / 연면적 566평", "temp": "상온",
     "note": "일죽IC 5분 · 2008년 준공",
     "img": "https://github.com/offiscan/offiscan/blob/main/docs/jino.jpg?raw=true",
     "link": "https://offiscan.github.io/offiscan/jino.pdf"},
]

# ===== 매물 구글시트 =====
# 이 시트만 고치면 매물이 자동 반영된다. (구글시트 → 파일 → 웹에 게시 → CSV 주소)
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQaWnz0fH4RUOvbPqUAdZnDwqgM9c2Ly-yMsy_kqplfNJMrEA3ntFKXAIFQd42FvRoCR7BIaxgCifPm/pub?output=csv"
LISTINGS_PER_WEEK = 2       # 매주 내보낼 매물 수

# 웹페이지 상단 '이번 주 시장 한눈에' 한 문단. 30초면 쓴다.
# (나중에 이 부분도 Claude API 로 자동 생성 가능. 지금은 직접 쓰는 게 정확·안전)
WEEKLY_BRIEF = (
    "2026년 상반기 수도권 물류센터 신규 공급은 17.5만 평에 그쳐 전년 동기의 3분의 1 수준으로 급감했고, 상반기 개발 허가는 단 3건에 불과했습니다. 공실률은 상온 12.3%, 저온 33.7%로 각각 1.0%p·2.6%p 낮아지며 2024년 정점 이후 하락세가 이어졌지만, 저온은 여전히 상온의 약 2.7배로 온도차가 뚜렷합니다. 임대료마저 상온·저온 모두 반등해 하락세가 멈춘 지금, 공급이 끊긴 시장에서 입지·스펙을 갖춘 우량 상온 자산의 희소가치가 부각되는 국면입니다."
)


# ===================== 최초 1회 설정 =====================

WEB_URL = "https://offiscan.github.io/offiscan/"   # GitHub Pages 켠 뒤 실제 주소로
CONTACT_PHONE = "010-5728-9911"

WEEK_HOURS = 168        # 7일
WEB_MAX = 30            # 웹페이지에 실을 기사 수 (카톡은 아래 TOUCH_NEWS 로 별도)
TOUCH_NEWS = 3          # 카톡에 넣을 헤드라인 수 (짧게 유지 — 3개 권장)
WEB_MAX_PER_SOURCE = 4  # 웹페이지에서 한 언론사가 차지할 수 있는 최대 기사 수

# 로그인/유료벽이 있어 고객이 클릭해도 안 열리는 사이트. 여기 뉴스는 아예 제외.
# 나중에 또 걸리는 곳 생기면 따옴표로 감싸 한 줄 추가하면 됩니다.
BLOCKED_DOMAINS = [
    "dealsite.co.kr",     # 딜사이트 (로그인 필요)
]

# 알찬 소스는 위로 끌어올린다. 숫자가 클수록 상위.
SOURCE_BOOST = {
    "물류신문": 6,          # 물류 정보가 제일 많은 소스
}


# ===================== 라벨/순서 =====================

# 웹페이지 섹션명 (news_report 의 카테고리를 고객용 이름으로)
WEB_SECTION_LABEL = {
    "local":  "우리 지역 · 이천 / 안성 / 여주 / 평택",
    "comm":   "물류·산업 시장",
    "house":  "주택·시장",
    "build":  "건설·금융",
    "policy": "정책·제도",
    "gen":    "종합",
}
WEB_ORDER = ["local", "comm", "house", "build", "policy", "gen"]

NAVY = "#1F2A52"
GOLD = "#C9A227"


# ===================== 매물 시트 =====================

def load_listings_from_sheet():
    """구글시트(게시된 CSV)에서 '광고중' 매물을 읽어온다. 25초 안에 응답 없으면 None.
    별도 스레드로 받아 오므로, 구글이 느리거나 멈춰도 발송은 절대 지연되지 않는다."""
    import threading
    box = {}

    def _fetch():
        try:
            req = urllib.request.Request(SHEET_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
            box["data"] = urllib.request.urlopen(req, timeout=15).read().decode("utf-8-sig")
        except Exception as e:
            box["err"] = str(e)[:60]

    print("  [매물시트] 읽는 중...")
    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(25)          # 최대 25초만 기다린다

    if "data" not in box:
        why = box.get("err", "25초 내 응답 없음")
        print(f"  [매물시트] 실패({why}) → 파일 예비목록 사용")
        return None

    try:
        rows = list(csv.DictReader(io.StringIO(box["data"])))
        out = []
        for r in rows:
            if (r.get("노출여부") or "").strip() != "광고중":
                continue
            name = (r.get("이름") or "").strip()
            if not name or name.startswith("(예시)"):   # 빈 줄·예시 줄 제외
                continue
            out.append({
                "tag":   (r.get("종류") or "").strip(),
                "title": name,
                "area":  (r.get("평형") or "").strip(),
                "temp":  (r.get("온도") or "").strip(),
                "note":  (r.get("특징") or "").strip(),
                "img":   (r.get("사진주소") or "").strip(),
                "link":  (r.get("브리프주소") or "").strip(),
            })
        return out or None
    except Exception as e:
        print(f"  [매물시트] 해석 실패 → 파일 예비목록 사용: {str(e)[:60]}")
        return None


def pick_listings():
    """이번 주 추천매물 선택. 주(week)마다 순번이 돌아가며 전체를 훑는다.
    매물 N개면 매주 LISTINGS_PER_WEEK개씩 → 약 N/2주 만에 한 바퀴(≈3개월)."""
    pool = load_listings_from_sheet()
    if not pool:
        print("  [매물] 시트 대신 파일 예비목록 사용")
        return WEEKLY_LISTINGS
    n = len(pool)
    print(f"  [매물] 시트에서 광고중 {n}건 읽음")
    if n <= LISTINGS_PER_WEEK:
        return pool
    week = datetime.now(KST).isocalendar()[1]        # 연중 주차
    start = (week * LISTINGS_PER_WEEK) % n
    return [pool[(start + k) % n] for k in range(LISTINGS_PER_WEEK)]


# ===================== 수집 =====================

def _domain(url):
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def _blocked(it):
    dom = _domain(it.get("link", ""))
    return any(b in dom for b in BLOCKED_DOMAINS)


# --- 소스 가점: pick_balanced 가 참조하는 점수 함수를 '가점 포함'으로 교체 ---
_orig_score = nr.score


def boosted_score(it):
    src = it.get("outlet") or nr.outlet_of(it.get("source", ""))
    return _orig_score(it) + SOURCE_BOOST.get(src, 0)


nr.score = boosted_score            # pick_balanced 내부 정렬이 이 점수를 쓰게 됨
score = boosted_score               # weekly_digest 내부 정렬도 같은 기준
nr.MAX_PER_SOURCE = WEB_MAX_PER_SOURCE   # 웹페이지는 풍성하게 (일일 파이프라인은 별도 실행이라 무관)


def gather():
    """news_report 로직 재사용. 7일 창으로 수집 → 차단도메인 제외 → 중복 제거."""
    nr.HOURS_BACK = WEEK_HOURS          # 일일(24h) 대신 주간(168h)
    items = nr.collect()

    # 로그인/유료벽 사이트 제외
    kept, blocked = [], 0
    for it in items:
        if _blocked(it):
            blocked += 1
            continue
        kept.append(it)
    if blocked:
        print(f"  차단 사이트 제외: {blocked}건 (BLOCKED_DOMAINS)")

    # run 내 중복 제거 (master 는 건드리지 않으므로 여기서 직접)
    seen, uniq = set(), []
    for it in sorted(kept, key=score, reverse=True):
        k = norm(it["title"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    return uniq


# ===================== 카톡 터치 메시지 =====================

def build_kakao_touch(picked, issue_url):
    """카톡 복붙용 메시지. 맨 밑 링크는 '이번 호 박제 링크(issue_url)'를 쓴다.
    → 메인(index)이 아니라 날짜 고정 링크라, 고객이 나중에 열거나 지인에게 공유해도
      그 주의 뉴스·매물이 그대로 유지된다."""
    today = datetime.now(KST)
    top = sorted(picked, key=score, reverse=True)[:TOUCH_NEWS]

    L = [f"[이천·안성 부동산 소식] {today:%-m/%-d}",
         "메이트플러스 정미경 매니저입니다.",
         "",
         "■ 이번 주 눈에 띄는 뉴스"]
    for it in top:
        t = it["title"]
        t = t[:38] + "…" if len(t) > 40 else t
        L.append(f"▶ {t}")

    if WEEKLY_LISTINGS:
        L += ["", "■ 이번 주 추천매물"]
        for m in WEEKLY_LISTINGS:
            bits = [b for b in (m.get("area"), m.get("temp"), m.get("note")) if b]
            L.append(f"· [{m['tag']}] {m['title']} — " + " · ".join(bits))

    L += ["", "전체 뉴스·매물 한눈에 보기 ↓", "정미경 공인중개사 · 메이트플러스 부동산중개", issue_url]
    return "\n".join(L)


# ===================== 웹페이지 =====================

def _e(s):
    return html.escape(str(s or ""))


def _section(cat, items):
    rows = []
    n = len(items)
    for i, it in enumerate(items):
        border = "" if i == n - 1 else "border-bottom:1px solid #EDEFF3;"
        src = display_source(it)
        rows.append(
            f'<tr><td style="padding:11px 0;{border}">'
            f'<a href="{_e(it["link"])}" style="text-decoration:none;color:{NAVY};font-size:15px;line-height:1.5;">'
            f'<span style="color:#2F5FD0;">&#10132;</span>&nbsp; {_e(it["title"])} '
            f'<span style="color:#8A93A8;font-weight:bold;">| {_e(src)}</span></a></td></tr>'
        )
    label = WEB_SECTION_LABEL.get(cat, CAT_LABEL.get(cat, "뉴스"))
    return (
        f'<tr><td style="padding:26px 24px 0 24px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{NAVY};">'
        f'<tr><td width="5" style="background:{GOLD};font-size:0;line-height:0;">&nbsp;</td>'
        f'<td style="padding:11px 16px;color:#fff;font-size:16px;font-weight:bold;">{label}</td></tr></table></td></tr>'
        f'<tr><td style="padding:0 24px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(rows)}</table></td></tr>'
    )


def _listing_card(m, full):
    """full=True 면 가로 전체 카드(사진도 크게), False 면 반쪽 카드."""
    badge = NAVY if m["tag"] == "매각" else GOLD
    tcol = "#fff" if m["tag"] == "매각" else "#3d3208"
    ph_h = 240 if full else 150

    photo = ""
    if m.get("img"):
        photo = (f'<div style="border-radius:10px;overflow:hidden;margin-bottom:10px;">'
                 f'<img src="{_e(m["img"])}" alt="{_e(m["title"])}" '
                 f'style="width:100%;height:{ph_h}px;object-fit:cover;display:block;"></div>')

    spec = " · ".join([b for b in (m.get("area"), m.get("temp")) if b])
    inner = (
        f'{photo}'
        f'<span style="display:inline-block;background:{badge};color:{tcol};font-size:11px;font-weight:bold;padding:2px 8px;border-radius:20px;">{_e(m["tag"])}</span>'
        f'<div style="color:{NAVY};font-size:15px;font-weight:bold;margin:9px 0 4px;">{_e(m["title"])}</div>'
        f'<div style="color:#1F2A52;font-size:13px;font-weight:bold;">{_e(spec)}</div>'
        f'<div style="color:#5A6472;font-size:13px;line-height:1.5;margin-top:2px;">{_e(m.get("note",""))}</div>'
    )
    if m.get("link"):
        detail = f'<div style="color:{GOLD};font-size:12px;font-weight:bold;margin-top:8px;">상세 보기 &#10132;</div>'
        inner = f'<a href="{_e(m["link"])}" style="text-decoration:none;display:block;">{inner}{detail}</a>'

    w = "100%" if full else "50%"
    return (f'<td width="{w}" valign="top" style="padding:5px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #E5E8EE;border-radius:12px;">'
            f'<tr><td style="padding:14px;">{inner}</td></tr></table></td>')


def _listings_html():
    if not WEEKLY_LISTINGS:
        return ""
    # 매물이 1개든 2개든 카드는 항상 반쪽(50%) 크기. 홀수 끝자리는 오른쪽을 빈칸으로.
    rows, i = "", 0
    while i < len(WEEKLY_LISTINGS):
        chunk = WEEKLY_LISTINGS[i:i + 2]
        left = _listing_card(chunk[0], full=False)
        if len(chunk) == 2:
            right = _listing_card(chunk[1], full=False)
        else:
            right = '<td width="50%">&nbsp;</td>'   # 빈칸
        rows += f"<tr>{left}{right}</tr>"
        i += 2
    return (
        f'<tr><td style="padding:26px 24px 0 24px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{NAVY};">'
        f'<tr><td width="5" style="background:{GOLD};font-size:0;line-height:0;">&nbsp;</td>'
        f'<td style="padding:11px 16px;color:#fff;font-size:16px;font-weight:bold;">정매니저의 추천매물</td></tr></table></td></tr>'
        f'<tr><td style="padding:8px 19px 0 19px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>'
    )


def build_web_html(picked):
    today = datetime.now(KST)
    by_cat = defaultdict(list)
    for it in picked:
        by_cat[it["cat"]].append(it)

    sections = ""
    for cat in WEB_ORDER:
        if by_cat.get(cat):
            sections += _section(cat, by_cat[cat])

    brief = ""
    if WEEKLY_BRIEF.strip():
        brief = (
            f'<tr><td style="padding:18px 24px 4px 24px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F5F6F8;">'
            f'<tr><td width="4" style="background:{GOLD};font-size:0;line-height:0;">&nbsp;</td>'
            f'<td style="padding:14px 16px;">'
            f'<div style="color:{NAVY};font-size:14px;font-weight:bold;padding-bottom:6px;">이번 주 시장 한눈에</div>'
            f'<div style="color:#5A6472;font-size:14px;line-height:1.6;">{_e(WEEKLY_BRIEF)}</div>'
            f'</td></tr></table></td></tr>'
        )

    # 카톡 미리보기 대표사진(og:image) = 이번 주 첫 매물 사진
    og_image = "https://github.com/offiscan/offiscan/blob/main/docs/jino.jpg?raw=true"
    for m in WEEKLY_LISTINGS:
        if m.get("img"):
            og_image = m["img"]
            break

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>물류·산업 부동산 뉴스레터 · 정미경 공인중개사</title>
<meta property="og:type" content="website">
<meta property="og:title" content="물류·산업 부동산 뉴스레터 · 정미경 공인중개사">
<meta property="og:description" content="이천·안성·여주·평택 산업·물류 부동산 소식과 추천매물 | 메이트플러스 부동산중개">
<meta property="og:image" content="{og_image}">
<meta property="og:url" content="{WEB_URL}">
<meta name="description" content="이천·안성·여주·평택 산업·물류 부동산 소식과 추천매물 | 정미경 공인중개사">
</head>
<body style="margin:0;padding:0;background:#E9ECF1;font-family:'Malgun Gothic',Apple SD Gothic Neo,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#E9ECF1;padding:24px 12px;"><tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:640px;max-width:640px;background:#fff;border-radius:12px;overflow:hidden;">

  <tr><td style="background:{NAVY};padding:24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td valign="bottom">
        <div style="width:34px;height:4px;background:{GOLD};margin-bottom:12px;font-size:0;line-height:0;">&nbsp;</div>
        <div style="color:#fff;font-size:23px;font-weight:bold;">물류·산업 부동산 뉴스레터</div>
        <div style="color:{GOLD};font-size:14px;padding-top:6px;">{today:%Y. %m. %d.} · 이천 / 안성 / 여주 / 평택</div>
      </td>
      <td valign="bottom" align="right" style="white-space:nowrap;">
        <div style="color:#fff;font-size:17px;font-weight:bold;letter-spacing:1px;">MatePlus Realty</div>
        <div style="color:{GOLD};font-size:12px;padding-top:3px;">정미경 매니저</div>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:22px 24px 0 24px;">
    <div style="color:{NAVY};font-size:15px;line-height:1.7;">
      안녕하세요. <b>메이트플러스 부동산중개 정미경 매니저</b>입니다.<br>
      매주, 이천·안성·여주·평택 등 경기권역의 뉴스와 추천매물을 정리해 전해드립니다.
    </div>
  </td></tr>

  {brief}
  {_listings_html()}
  {sections}

  <tr><td style="background:{NAVY};padding:24px;margin-top:26px;">
    <div style="color:#fff;font-size:15px;font-weight:bold;padding-bottom:6px;">MatePlus Realty 제공 서비스</div>
    <div style="color:{GOLD};font-size:13px;line-height:1.6;">매입매각 · 임대차 · 리테일 · 물류 · 컨설팅 · 리서치 · 자산관리</div>
    <div style="color:#fff;font-size:14px;padding-top:12px;">정미경 매니저 &nbsp;·&nbsp; T. {CONTACT_PHONE}</div>
  </td></tr>

</table></td></tr></table>
</body></html>"""


# ===================== 실행 =====================

def main():
    dry = len(sys.argv) > 1 and sys.argv[1] == "dry"

    # 이번 주 추천매물을 구글시트에서 선택 (실패 시 파일 예비목록)
    global WEEKLY_LISTINGS
    WEEKLY_LISTINGS = pick_listings()

    items = gather()
    if not items:
        print("\n수집된 기사가 없습니다. 'py news_report.py feeds' 로 RSS 점검하세요.")
        return

    picked = pick_balanced(items, total=WEB_MAX)

    # 이번 호 '박제' 링크 먼저 확정 (카톡·웹 동일하게 사용)
    today_kst = datetime.now(KST)
    issue_name = f"{today_kst:%Y-%m-%d}.html"     # 예: 2026-08-18.html
    issue_url = WEB_URL + issue_name

    touch = build_kakao_touch(picked, issue_url)

    print("\n" + "=" * 46)
    print("복붙용 카톡 메시지 (아래를 그대로 복사)")
    print("=" * 46)
    print(touch)
    print("=" * 46)

    if dry:
        print("\n[dry] 파일은 만들지 않았습니다.")
        return

    # 카톡 메시지 저장
    ktxt = f"weekly_kakao_{today_kst:%Y%m%d}.txt"
    with open(ktxt, "w", encoding="utf-8-sig") as f:
        f.write(touch)

    # 웹페이지 저장 (GitHub Pages: main 브랜치 /docs)
    docs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    os.makedirs(docs, exist_ok=True)
    web_html = build_web_html(picked)

    # (1) 최신호 — 메인 링크용 (기존처럼 덮어쓰기)
    index_path = os.path.join(docs, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(web_html)

    # (2) 이번 호 영구 박제 — 날짜 파일 (고객이 받는 링크는 이걸로)
    issue_path = os.path.join(docs, issue_name)
    with open(issue_path, "w", encoding="utf-8") as f:
        f.write(web_html)

    print(f"\n저장: {ktxt}")
    print(f"저장: docs/index.html      →  최신호 (메인 {WEB_URL})")
    print(f"저장: docs/{issue_name}  →  이번 호 영구 링크")
    print(f"\n▶ 이번 주 고객에게 보낼 링크: {issue_url}")
    print(f"  git push 하면 위 두 파일이 함께 올라갑니다.")


if __name__ == "__main__":
    main()
