"""
OFFISCAN - 부동산·물류 뉴스 다이제스트 (언론사 RSS 직접 수집)

구글뉴스 RSS는 제목과 암호화된 중계링크만 준다. 요약도 없고 카톡에서 링크가 안 열린다.
언론사 RSS는 제목·요약문·원문주소를 모두 준다. 그래서 소스를 바꿨다.

사용법:
  py news_report.py feeds    [먼저 이것] 어떤 RSS가 살아있는지 점검
  py news_report.py          수집 후 카카오톡 발송
  py news_report.py dry      카톡 안 보내고 무엇이 뽑히는지만 확인

* API 키 불필요
* news_master.csv 로 중복을 막는다. 지우지 말 것.

--- 2026-07-21 변경 ---
필터를 피드 성격별로 분리했다.
  pure  = 부동산 전용 피드. 이미 걸러진 소스이므로 통과.
  broad = 경제/산업/지역 종합 피드. 부동산 앵커 단어가 있어야 통과.
이전에는 INCLUDE_WORDS 하나로 둘 다 처리해서
  - 종합 피드에서 "물류" 한 단어에 청국장·러시아 기사가 통과했고
  - 부동산 피드에서 공장·창고가 없다는 이유로 오피스·정책 기사가 탈락했다.
"""

import os
import re
import csv
import sys
import html
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests

# ===================== CONFIG =====================

# (이름, 주소, 카테고리, 성격)
#   카테고리: comm 물류·산업 / policy 정책 / house 주택·시장
#             build 건설·금융 / local 경기 / gen 종합
#   성격:     pure  부동산 전용 → 앵커 검사 없음
#             broad 종합 피드   → 앵커 필수
FEEDS = [
    # --- pure : 부동산·물류 전용 ---
    ("한국경제",   "https://www.hankyung.com/feed/realestate", "house", "pure"),
    ("매일경제",   "https://www.mk.co.kr/rss/50300009/", "house", "pure"),
    ("조선비즈",   "https://biz.chosun.com/arc/outboundfeeds/rss/category/real_estate/?outputType=xml", "house", "pure"),
    ("물류신문",   "https://www.klnews.co.kr/rss/allArticle.xml", "comm", "pure"),

    # --- broad : 종합 피드. 앵커 없으면 탈락 ---
    ("매경기업",     "https://www.mk.co.kr/rss/50100032/", "comm", "broad"),
    ("연합뉴스경제", "https://www.yna.co.kr/rss/economy.xml", "policy", "broad"),
    ("연합뉴스산업", "https://www.yna.co.kr/rss/industry.xml", "comm", "broad"),
    ("뉴시스경제",   "https://newsis.com/RSS/economy.xml", "policy", "broad"),
    ("아시아경제",   "https://www.asiae.co.kr/rss/economy.htm", "policy", "broad"),

    # 중앙일보는 RSS 서비스를 종료했다 (2026.07 확인)
    # 이데일리·헤럴드경제·CLO·물류매거진은 2026-07-20 기준 접속 불가
    # 생활경제 계열 피드는 넣지 말 것. 청국장이 거기서 나온다.
]

HOURS_BACK = 24          # 매일 돌리므로 72 -> 24
MAX_SEND_EACH = 10       # 카톡으로 보낼 기사 수 (건당 1통씩 발송됨)
ALWAYS_SEND = True
MAX_PER_SOURCE = 2
SUMMARY_CHARS = 150
REQUEST_DELAY = 0.3

# 카톡 제목 표시 방식.
# 카카오 텍스트 템플릿은 볼드·마크다운·HTML을 지원하지 않는다.
# 기호로 묶는 것이 사실상 유일한 강조 수단이다.
#   "bracket"  【안성 물류센터 공실률 30% 돌파】
#   "angle"    <안성 물류센터 공실률 30% 돌파>
#   "arrow"    ▶ 안성 물류센터 공실률 30% 돌파
#   "plain"    안성 물류센터 공실률 30% 돌파
TITLE_STYLE = "bracket"

# 카테고리별 정원. 한 분야가 카톡을 독식하지 못하게 한다.
# 합이 MAX_SEND_EACH 보다 커야 자리가 유연하게 채워진다.
# local 을 넉넉히 잡았다. 안성·이천 기사가 내 차별점이고
# 그건 거의 전부 네이버 검색에서 들어온다.
QUOTA = {"comm": 3, "policy": 2, "house": 2, "build": 2, "local": 3, "gen": 1}
USE_NAVER = True         # 네이버 검색 API 병합 여부
CAT_LABEL = {"comm": "물류·산업", "policy": "정책", "house": "주택·시장",
             "build": "건설·금융", "local": "경기", "gen": "종합"}

# broad 피드는 이 중 하나가 있어야 통과한다.
# "물류" "공장" 같은 맨몸 단어는 넣지 않는다. 그게 청국장을 부른 원인이다.
ANCHORS = [
    # 물건
    "부동산", "토지", "부지", "용지", "공장", "창고", "물류센터", "물류창고",
    "물류단지", "물류시설", "물류부동산", "풀필먼트", "3PL",
    "냉동창고", "냉장창고", "저온창고", "콜드체인",
    "산업단지", "지식산업센터", "제조시설", "생산시설", "데이터센터",
    "오피스", "상가", "빌딩", "오피스텔",
    # 행위
    "착공", "준공", "증설", "신설", "매입", "매각", "인수합병", "분양",
    "임대차", "입주", "이전", "개발사업", "재건축", "재개발", "경매",
    # 제도·지표
    "용도지역", "그린벨트", "개발제한", "지구단위", "인허가", "건폐율", "용적률",
    "공시지가", "실거래", "공실률", "임대료", "캡레이트", "리츠", "REITs",
    "브릿지론", "종부세", "취득세", "양도세", "국토교통부", "국토부", "LH",
]

# 앵커가 있어도 버린다. pure/broad 모두 적용.
EXCLUDE_WORDS = [
    "화재", "불길", "진화", "붕괴", "사망", "부상", "실종",
    "폭우", "침수", "태풍", "지진", "산사태", "폭염", "장마",
    # 전쟁·군사 (물류창고가 폭격 기사에 등장한다)
    "미사일", "탄도", "폭격", "공습", "전쟁", "교전", "우크라", "러시아군",
    "이스라엘", "하마스", "드론 공격", "보복전", "휴전",
    # 보도자료성
    "봉사", "기부", "후원", "채용", "공채", "인턴", "진로교육",
    "캠페인", "협약식", "간담회", "위촉", "시상", "수상",
    "의약품 품질", "품질관리 강화", "위생", "식중독", "방역",
    "청약가점", "로또청약", "떴다방", "분양광고",
    "코인", "가상자산", "비트코인",
    "연예인", "결혼", "이혼", "부고",
    "레시피", "맛집", "무료 세미나", "수강생 모집",
]

# 순위용 점수. 걸러내는 용도가 아니라 위로 끌어올리는 용도.
SCORE_WORDS = {
    "원곡면": 10, "모가면": 10, "신갈리": 10,
    "안성": 8, "이천": 8, "여주": 8, "평택": 6, "용인": 5, "화성": 5, "경기남부": 6,
    "저온창고": 7, "냉동창고": 7, "콜드체인": 6, "물류센터": 6, "물류창고": 6,
    "공실률": 6, "실거래": 5, "캡레이트": 5, "임대료": 4,
    "공장": 5, "창고": 5, "산업단지": 5, "물류단지": 5, "지식산업센터": 4,
    "용도지역": 5, "건폐율": 5, "그린벨트": 4, "인허가": 3,
    "매각": 4, "인수": 4, "리츠": 4, "브릿지론": 4, "PF": 4,
    "증설": 4, "착공": 3, "준공": 3,
}

MASTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_master.csv")
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; OFFISCAN/1.0)"}


# ===================== 유틸 =====================

def strip_tags(t):
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt)
            return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
        except ValueError:
            continue
    return None


def domain_of(url):
    try:
        return urllib.parse.urlparse(url).netloc
    except Exception:
        return ""


def display_source(it):
    """
    카톡·리포트에 찍을 출처명.
    네이버 결과는 source 가 'N:안성 물류센터'(검색어)라서 그대로 쓰면
    카톡에 검색어가 노출된다. outlet 필드에 담긴 실제 언론사명을 쓴다.
    """
    return it.get("outlet") or it.get("source") or ""


def decorate_title(t, style=None):
    style = style or TITLE_STYLE
    if style == "bracket":
        return f"【{t}】"
    if style == "angle":
        return f"<{t}>"
    if style == "arrow":
        return f"▶ {t}"
    return t


def outlet_of(source):
    """
    물류신문 / 물류신문2 를 한 언론사로 묶는다.
    네이버 결과('N:안성 물류센터')는 검색어 단위로 상한을 건다.
    한 키워드가 카톡을 독점하지 못하게 하기 위해서다.
    """
    s = source or ""
    if s.startswith("N:"):
        return s
    return re.sub(r"\d+$", "", s)


# ===================== 필터 =====================

def is_apartment_brief(title):
    """
    '용인 풍덕천동 수지현대아파트 84㎡ 12억5000만원에 거래' 같은
    자동생성 실거래 단신. 매일 수십 건씩 나오고 내 시장과 무관하다.
    면적 표기 + 거래/신고 조합으로 판별한다.
    """
    return ("㎡" in title or "m2" in title) and \
           any(w in title for w in ("거래", "신고", "매매", "실거래", "최고가"))


def relevant(it):
    """
    pure  : 제외어만 통과하면 OK
    broad : 제외어 + 앵커 단어가 '제목에' 있어야 함

    앵커를 요약문에서까지 찾으면 곁다리 언급에 걸린다.
    실제로 "[쇼츠] 러 물류창고 터지자…키이우에 탄도미사일 보복전" 이
    요약문의 '물류창고' 때문에 물류·산업 1위로 올라왔다.
    """
    title = it["title"]
    text = title + " " + it["desc"]

    if any(w in text for w in EXCLUDE_WORDS):
        return False

    if is_apartment_brief(title):
        return False

    if it.get("mode") == "pure":
        return True

    return any(a in title for a in ANCHORS)


def score(it):
    text = it["title"] + " " + it["desc"]
    s = sum(v for w, v in SCORE_WORDS.items() if w in text)
    s += sum(2 for w in SCORE_WORDS if w in it["title"])   # 제목 가산
    return s


# ===================== 수집 =====================

def read_feed(name, url, cat="gen", mode="broad"):
    try:
        r = requests.get(url, timeout=20, headers=UA)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        return None, f"실패: {str(e)[:70]}"

    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue

        def pick(*names):
            for n in names:
                for ch in it:
                    if ch.tag.split("}")[-1] != n:
                        continue
                    if ch.text and ch.text.strip():
                        return ch.text.strip()
                    if ch.attrib.get("href"):
                        return ch.attrib["href"]
            return ""

        title = strip_tags(pick("title"))
        link = pick("link", "guid")
        desc = strip_tags(pick("description", "summary", "content"))
        pub = pick("pubDate", "published", "date")
        if not title or not link.startswith("http"):
            continue
        items.append({"source": name, "cat": cat, "mode": mode,
                      "title": title, "link": link,
                      "desc": desc, "when": parse_date(pub)})
    return items, f"{len(items)}건"


def check_feeds():
    print("=== RSS 점검 ===\n")
    ok = []
    for name, url, cat, mode in FEEDS:
        items, msg = read_feed(name, url, cat, mode)
        print(f"  {'OK  ' if items else '실패'} {name:<10} [{mode:<5}] {msg}")
        if items:
            ok.append((name, items[0]["title"][:38], domain_of(items[0]["link"])))
        time.sleep(REQUEST_DELAY)

    if not ok:
        print("\n작동하는 RSS가 없습니다.")
        return

    print("\n--- 작동하는 언론사 ---")
    doms = set()
    for name, sample, dom in ok:
        print(f"  {name:<10} {dom:<22} 예: {sample}")
        if dom:
            doms.add(dom)

    print("\n--- 카카오에 등록할 웹 도메인 ---")
    print("  앱 > 제품 링크 관리 > 웹 도메인 수정")
    print("  등록해야 카톡 '자세히 보기'가 열립니다. 최대 10개.\n")
    for d in sorted(doms):
        print(f"    https://{d}")
    print("\n실패한 언론사는 FEEDS 목록에서 지우세요.")


def collect():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    print(f"언론사 {len(FEEDS)}곳 / 최근 {HOURS_BACK}시간\n")
    out = []
    dropped = defaultdict(int)

    for name, url, cat, mode in FEEDS:
        items, msg = read_feed(name, url, cat, mode)
        if not items:
            print(f"  {name:<12} {msg}")
            time.sleep(REQUEST_DELAY)
            continue
        keep = []
        for it in items:
            if it["when"] and it["when"] < cutoff:
                continue
            if not relevant(it):
                dropped[name] += 1
                continue
            it["when"] = (it["when"] or datetime.now(timezone.utc)).astimezone(KST)
            keep.append(it)
        print(f"  {name:<12} [{mode:<5}] 전체 {len(items):>3}건 "
              f"→ 해당 {len(keep):>2}건 (탈락 {dropped[name]})")
        out.extend(keep)
        time.sleep(REQUEST_DELAY)

    # 네이버 검색 API 병합.
    # RSS는 언론사가 쓰는 걸 받고, 네이버는 내 키워드로 전 언론사를 훑는다.
    # 안성·이천 지역지 기사는 사실상 여기서만 들어온다.
    if USE_NAVER:
        try:
            from naver_news import collect_naver
            nv = collect_naver(hours_back=HOURS_BACK)
            seen = {norm(i["title"]) for i in out}
            added = 0
            for it in nv:
                if norm(it["title"]) in seen:
                    continue
                if not relevant(it):
                    continue
                seen.add(norm(it["title"]))
                it["when"] = it["when"].astimezone(KST)
                out.append(it)
                added += 1
            print(f"  네이버 병합 {added}건 (RSS 중복 제외)")
        except ImportError:
            print("  [네이버] naver_news.py 없음. RSS만 사용")
        except Exception as e:
            print(f"  [네이버] 오류로 건너뜀: {str(e)[:60]}")

    by_cat = defaultdict(int)
    for i in out:
        by_cat[i["cat"]] += 1
    print("\n  분야별 수집: " + ", ".join(
        f"{CAT_LABEL.get(k, k)} {v}" for k, v in sorted(by_cat.items())))
    return out


# ===================== 중복 =====================

def norm(t):
    return "".join(c for c in t if c.isalnum()).lower()[:60]


def filter_new(items):
    seen = set()
    if os.path.exists(MASTER):
        with open(MASTER, encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if row:
                    seen.add(row[0])
    first = not seen
    news, batch = [], set()
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    for it in items:
        k = norm(it["title"])
        if k in seen or k in batch:
            continue
        batch.add(k)
        if not first:
            news.append(it)
    with open(MASTER, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for k in batch:
            w.writerow([k, now])
    if first:
        print(f"\n[기준선 생성] {len(batch)}건 기록. 다음 실행부터 새 기사만 보냅니다.")
        return [], True
    print(f"\n[새 기사] {len(news)}건")
    return news, False


# ===================== 선별 =====================

def pick_balanced(news, total=MAX_SEND_EACH):
    """
    분야별로 자리를 '예약'한다.

    이전에는 점수 높은 순으로 훑다가 10칸이 차면 멈췄다.
    그러면 네이버 기사(이미 걸러져서 20점대)가 상위를 다 먹고
    주택·시장 기사(0점대)는 정원이 남아 있어도 순번이 안 왔다.
    실제로 59건을 수집해놓고 한 건도 못 뽑았다.

    그래서 분야별로 먼저 각자의 몫을 채우고, 남는 자리만 점수순으로 준다.
    """
    ranked = sorted(news, key=score, reverse=True)
    picked, out_n = [], defaultdict(int)
    order = ["comm", "policy", "house", "build", "local", "gen"]

    # 1차: 분야별 예약분을 각 분야 안에서 점수순으로 채운다
    for cat in order:
        n = 0
        for it in ranked:
            if n >= QUOTA.get(cat, 1) or len(picked) >= total:
                break
            if it["cat"] != cat:
                continue
            o = outlet_of(it["source"])
            if out_n[o] >= MAX_PER_SOURCE:
                continue
            picked.append(it)
            out_n[o] += 1
            n += 1

    # 2차: 남은 자리는 분야 상관없이 점수순
    for it in ranked:
        if len(picked) >= total:
            break
        if it in picked:
            continue
        o = outlet_of(it["source"])
        if out_n[o] >= MAX_PER_SOURCE:
            continue
        picked.append(it)
        out_n[o] += 1

    cat_n = defaultdict(int)
    for it in picked:
        cat_n[it["cat"]] += 1

    picked.sort(key=lambda x: order.index(x["cat"]) if x["cat"] in order else 99)

    if picked:
        print("\n  분야 배분: " + ", ".join(
            f"{CAT_LABEL.get(k, k)} {v}건" for k, v in cat_n.items() if v))
        rss_n = sum(1 for i in picked if not str(i.get("source", "")).startswith("N:"))
        print(f"  출처 배분: RSS {rss_n}건 / 네이버 {len(picked) - rss_n}건")
    return picked


# ===================== 출력 =====================

def build_report(items, news):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    new_links = {i["link"] for i in news}
    out = [f"OFFISCAN 부동산·물류 뉴스  ({now})",
           f"최근 {HOURS_BACK}시간 · 수집 {len(items)}건 · 새 기사 {len(news)}건", ""]

    order = ["comm", "policy", "house", "build", "local", "gen"]
    items = sorted(items, key=lambda x: (
        order.index(x["cat"]) if x["cat"] in order else 99, -score(x)))

    shown, last_cat = set(), None
    for it in items:
        k = norm(it["title"])
        if k in shown:
            continue
        shown.add(k)
        if it["cat"] != last_cat:
            out.append(f"===== {CAT_LABEL.get(it['cat'], '종합')} =====")
            out.append("")
            last_cat = it["cat"]
        tag = "[NEW] " if it["link"] in new_links else "      "
        out.append(f"{tag}{it['title']}")
        if it["desc"]:
            out.append(f"       {it['desc'][:200]}")
        out.append(f"       {display_source(it)} · {it['when']:%m/%d %H:%M} · {score(it)}점")
        out.append(f"       {it['link']}")
        out.append("")
    return "\n".join(out)


def save_report(text):
    path = f"news_{datetime.now(KST):%Y%m%d}.txt"
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text)
    print(f"저장: {path}")


# ===================== 실행 =====================

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "feeds":
        check_feeds()
        raise SystemExit

    items = collect()
    if not items:
        print("\n조건에 맞는 기사가 없습니다. 'py news_report.py feeds' 로 RSS를 점검하세요.")
        raise SystemExit

    news, first = filter_new(items)
    save_report(build_report(items, news))

    if first:
        print("기준선만 만들었습니다. 다음 실행부터 카톡을 보냅니다.")
        raise SystemExit

    if not news:
        if not ALWAYS_SEND:
            print("새 기사가 없습니다.")
            raise SystemExit
        print("새 기사는 없지만 수집분을 보냅니다.")
        seen_t, uniq = set(), []
        for it in items:
            k = norm(it["title"])
            if k not in seen_t:
                seen_t.add(k)
                uniq.append(it)
        news = uniq

    picked = pick_balanced(news)

    if arg == "dry":
        print("\n=== dry run · 카톡 발송 안 함 ===")
        for i, it in enumerate(picked, 1):
            src = "네이버" if str(it.get("source", "")).startswith("N:") else "RSS "
            print(f"({i:>2}) {src} [{CAT_LABEL.get(it['cat'])}] {score(it):>2}점 "
                  f"{display_source(it)} · {it['title'][:42]}")
        raise SystemExit

    try:
        from kakao_send import send
    except ImportError:
        print("[카카오] kakao_send.py 가 같은 폴더에 없습니다.")
        raise SystemExit

    for i, it in enumerate(picked, 1):
        lines = [f"[OFFISCAN] {CAT_LABEL.get(it['cat'], '부동산')}",
                 "",
                 decorate_title(it["title"])]
        if it["desc"]:
            lines += ["", it["desc"][:SUMMARY_CHARS]]
        lines += ["", f"{display_source(it)} · {it['when']:%m/%d %H:%M}"]
        print(f"\n({i}) {it['title'][:40]}")
        send("\n".join(lines), link=it["link"])
        time.sleep(1)
