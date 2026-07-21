"""
naver_news.py — 네이버 뉴스 검색 API 수집기 (news_report.py 보조)

RSS와 역할이 다르다.
  RSS       : 언론사가 발행하는 것을 전부 받는다 → 넓지만 내 지역이 안 잡힌다
  검색 API  : 내가 정한 키워드로 전 언론사를 훑는다 → 안성·이천 기사가 잡힌다

RSS 목록에 없는 지역지·전문지가 여기서 나온다. 그게 이 파일의 존재 이유다.

준비:
  developers.naver.com > 애플리케이션 등록 > 검색 API 사용
  keys.txt 에 두 줄 추가
      NAVER_CLIENT_ID=...
      NAVER_CLIENT_SECRET=...

단독 실행:
  py naver_news.py          키워드별로 몇 건 잡히는지 점검
"""

import os
import re
import html
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
API = "https://openapi.naver.com/v1/search/news.json"

# ── 키워드 (검색어, 카테고리, 필수단어, 택일단어) ──────────────
# 네이버 검색은 단어를 쪼개 느슨하게 매칭한다.
# "산업단지 분양"으로 검색하면 '분양'만 걸린 지자체 보도자료가 딸려온다.
# 그래서 네이버에는 넓게 묻고, 받아온 결과를 여기서 조인다.
#
#   must_all : 제목+요약에 이 단어가 "전부" 있어야 통과
#   must_any : 이 중 "하나 이상" 있어야 통과 (빈 리스트면 검사 안 함)
#
# 지역 키워드는 must_all 에 지역명을 넣는 것이 핵심이다.
# 그래야 '안성'만 스친 기사가 떨어진다.
# (검색어, 카테고리, 필수단어, 택일단어, 택일단어 검사범위)
#
#   필수단어 : 반드시 제목에 있어야 한다
#   택일단어 : 하나 이상 있어야 한다
#   검사범위 : "text" 제목+요약 / "title" 제목만
#
# 전부 "title" 이다. 요약문을 열어주면 지자체·대학 홍보가 샌다.
# 실제로 "평택시, 드론·AI로 반도체 설비 점검", "평택대 2027 수시 특집",
# "최원용 평택시장, 음주운전 근절"이 요약문의 '산업단지' 한 단어로 통과했다.
# 지역명과 부동산 단어가 둘 다 제목에 있어야 그 지역 부동산 기사다.
QUERIES = [
    # --- 내 시장 ---
    # "안성 물류센터"로 검색하면 네이버가 안성과 무관한 물류센터 기사를
    # 물어온다. 지역명 + 흔한 단어 조합이 오히려 잘 걸린다.
    ("안성 공장", "local", ["안성"],
     ["공장", "산업단지", "부지", "용지", "매각", "매물", "분양", "부동산",
      "임대", "준공", "착공", "증설", "물류", "창고", "개발"], "title"),
    ("안성 창고", "local", ["안성"],
     ["창고", "물류", "공장", "부지", "산업단지"], "title"),
    ("이천 공장", "local", ["이천"],
     ["공장", "산업단지", "부지", "용지", "매각", "물류", "창고",
      "준공", "착공", "증설", "개발", "부동산"], "title"),
    ("이천 물류창고", "local", ["이천"],
     ["창고", "물류", "냉동", "저온"], "title"),
    ("여주 산업단지", "local", ["여주"],
     ["산업단지", "물류", "창고", "공장", "부지", "개발"], "title"),
    ("평택 산업단지", "local", ["평택"],
     ["산업단지", "물류", "창고", "공장", "부지", "준공", "개발"], "title"),
    ("용인 물류창고", "local", ["용인"],
     ["창고", "물류", "산업단지", "부지"], "title"),

    # --- 업종 (제목만 본다) ---
    ("저온창고", "comm", ["창고"], ["저온", "냉동", "냉장", "콜드"], "title"),
    ("콜드체인 물류센터", "comm", ["콜드체인"],
     ["물류센터", "물류창고", "창고", "부지", "준공", "착공",
      "임대", "매각", "투자", "구축", "증설"], "title"),
    ("물류센터 공실", "comm", ["공실"], ["물류", "창고", "센터", "산업"], "title"),
    ("물류센터 매각", "comm", ["물류"],
     ["매각", "매입", "인수", "거래", "유동화"], "title"),
    ("지식산업센터 공실", "comm", ["지식산업센터"], [], "title"),
    ("물류센터 준공", "comm", ["물류"],
     ["준공", "착공", "신축", "개장", "완공"], "title"),

    # --- 정책·제도 ---
    ("물류창고 인허가", "policy", ["물류"],
     ["인허가", "허가", "승인", "규제", "심의", "고시",
      "가동", "운영", "지연", "중단", "무산", "반려", "조례"], "title"),
    ("계획관리지역", "policy", ["계획관리"], [], "title"),
    ("용도지역 변경", "policy", ["용도지역"], [], "title"),

    # --- 자본 ---
    ("물류 리츠", "build", ["리츠"],
     ["물류", "창고", "센터", "부동산", "매각", "자산"], "title"),
    ("부동산 PF 물류", "build", ["물류"],
     ["PF", "브릿지", "대출", "펀드"], "title"),
]

DISPLAY = 30          # 검색어당 가져올 건수 (최대 100)
MAX_PER_QUERY = 5     # 한 검색어가 카톡을 독점하지 못하게 하는 상한
HOURS_BACK = 24
REQUEST_DELAY = 0.15  # 초당 10회 제한 여유


# ── 키 읽기 ─────────────────────────────────────────────────
def load_keys(path=None):
    path = path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "keys.txt")
    keys = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
    # 깃허브 액션에서는 환경변수로도 들어온다
    for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
        if not keys.get(k) and os.environ.get(k):
            keys[k] = os.environ[k]
    return keys


def _strip(t):
    t = re.sub(r"(?s)<[^>]+>", " ", t or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


# 카톡에 'N:안성 물류센터' 대신 실제 언론사명을 찍기 위한 표.
# originallink 도메인으로 역추적한다. 없으면 '네이버뉴스'로 표기.
OUTLET_BY_DOMAIN = {
    "hankyung.com": "한국경제", "mk.co.kr": "매일경제", "sedaily.com": "서울경제",
    "fnnews.com": "파이낸셜뉴스", "edaily.co.kr": "이데일리", "asiae.co.kr": "아시아경제",
    "mt.co.kr": "머니투데이", "heraldcorp.com": "헤럴드경제", "biz.chosun.com": "조선비즈",
    "chosun.com": "조선일보", "donga.com": "동아일보", "joongang.co.kr": "중앙일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "seoul.co.kr": "서울신문",
    "yna.co.kr": "연합뉴스", "newsis.com": "뉴시스", "news1.kr": "뉴스1",
    "kmib.co.kr": "국민일보", "segye.com": "세계일보", "munhwa.com": "문화일보",
    "hankookilbo.com": "한국일보", "nocutnews.co.kr": "노컷뉴스",
    "klnews.co.kr": "물류신문", "cnews.co.kr": "건설경제", "conslove.co.kr": "건설신문",
    "renews.co.kr": "부동산신문", "housingherald.co.kr": "하우징헤럴드",
    "thebell.co.kr": "더벨", "kyeonggi.com": "경기일보", "joongboo.com": "중부일보",
    "kyeongin.com": "경인일보", "kgnews.co.kr": "경기신문", "kihoilbo.co.kr": "기호일보",
    "ekgib.com": "경기일보", "dt.co.kr": "디지털타임스", "etnews.com": "전자신문",
}


def outlet_name(originallink):
    """originallink 도메인에서 언론사명을 뽑는다."""
    try:
        host = urllib.parse.urlparse(originallink or "").netloc.lower()
    except Exception:
        return "네이버뉴스"
    host = host.replace("www.", "")
    if host in OUTLET_BY_DOMAIN:
        return OUTLET_BY_DOMAIN[host]
    # sub.domain.co.kr -> domain.co.kr 로 한 번 더 시도
    parts = host.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in OUTLET_BY_DOMAIN:
            return OUTLET_BY_DOMAIN[cand]
    return host or "네이버뉴스"


def _parse_date(s):
    try:
        return datetime.strptime(s.strip(), "%a, %d %b %Y %H:%M:%S %z")
    except Exception:
        return None


def match_terms(title, text, must_all, must_any, scope="text"):
    """
    must_all : 제목에서만 본다.
        요약문까지 보면 곁다리 언급에 걸린다. 쿠팡 인천 화재 기사가
        "덕평물류센터보다 큰"이라 쓰면 요약에 '이천'이 들어가 이천 기사가 된다.
        그래서 지역·핵심어는 반드시 제목에 있어야 한다.
    must_any : 제목+요약에서 본다.
        한국어 기사 제목은 짧아서 부연이 요약문에 있는 경우가 많다.
        둘 다 제목으로 조였더니 34건이 3건으로 떨어졌다.

    핵심은 모든 검색어가 must_all 을 비우지 않는 것이다.
    인천신항 기사가 샜던 것은 must_all 이 비어 제목 잠금장치가 없었기 때문이다.
    """
    if any(w not in title for w in must_all):
        return False
    haystack = title if scope == "title" else text
    if must_any and not any(w in haystack for w in must_any):
        return False
    return True


# ── 같은 사건 기사 묶기 ──────────────────────────────────────
# 아모레 안성 공장 매각이 언론사만 바꿔 5건, 지오영 콜드체인이 4건 들어온다.
# 제목이 조금씩 달라 글자 비교로는 안 잡힌다. 단어 겹침으로 판단한다.
# 지역명이 다르면 절대 같은 기사로 묶지 않는다.
# "이천 물류센터 준공"과 "안성 물류센터 준공"은 문장이 같아도 다른 사건이다.
REGIONS = [
    "안성", "이천", "여주", "평택", "용인", "화성", "오산", "광주", "하남",
    "김포", "인천", "시흥", "안산", "군포", "의왕", "성남", "수원", "양주",
    "파주", "포천", "남양주", "구리", "고양", "부천", "광명", "양평", "가평",
    "천안", "아산", "진천", "음성", "청주", "대전", "세종", "당진", "서산",
    "원주", "충주", "제천", "대구", "부산", "울산", "창원", "광양", "군산",
]


def _regions(title):
    return {r for r in REGIONS if r in title}


def _tokens(title):
    """
    글자 2-gram 을 쓴다.
    단어 단위로 자르면 '아모레'와 '아모레퍼시픽', '오산'과 '오산으로'가
    다른 단어로 잡혀 같은 사건을 못 묶는다. 한국어는 조사가 붙기 때문이다.
    """
    s = re.sub(r"[^가-힣A-Za-z0-9]", "", title)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def is_duplicate(title, kept, threshold=0.35):
    """
    이미 담은 기사와 글자 조각이 많이 겹치면 같은 사건으로 본다.
    단, 지역명이 다르면 아무리 비슷해도 다른 기사다.
    kept 원소는 (2gram집합, 지역집합) 튜플.
    """
    g = _tokens(title)
    rg = _regions(title)
    sig = (g, rg)
    if len(g) < 4:
        return False, sig
    for prev_g, prev_r in kept:
        # 양쪽 다 지역이 있는데 서로 겹치지 않으면 다른 사건
        if rg and prev_r and not (rg & prev_r):
            continue
        inter = len(g & prev_g)
        base = min(len(g), len(prev_g))
        if base and inter / base >= threshold:
            return True, sig
    return False, sig


# 지자체 보도자료·사건사고가 대량으로 섞여 들어온다. 제목에 있으면 버린다.
NOISE_WORDS = [
    # 사건·사고 (하나 터지면 이걸로 도배된다)
    "화재", "불길", "진화", "초진", "잔불", "발화", "소방", "대피",
    "붕괴", "사망", "부상", "실종", "참사", "누출", "폭발", "깔릴",
    "침수", "폭우", "태풍", "지진", "산사태", "호우", "폭염", "장마",
    # 지자체 홍보
    "일자리", "채용", "공모전", "축제", "봉사", "행사", "교육생 모집",
    "보건소", "의료원", "복지관", "장학", "기부", "헌혈", "무료 진료",
    "간담회", "위촉", "표창", "시상", "선포식", "출범식", "기념식",
    "체육대회", "동아리", "캠프", "관광객", "맛집",
    "식중독", "위생", "방역", "예방접종",
    # 소방·안전 캠페인성 (화재 후속기사가 이걸로 계속 나온다)
    "스프링클러", "안전기준", "소방시설", "방화", "불은", "불길",
    # 기업 홍보·사회공헌
    "품질관리", "치료제", "제제", "처방", "임상", "백신",
    "CSR", "사회공헌", "인재 육성", "산학협력", "방문", "견학", "체험단",
    "수상작", "공모", "이벤트", "리뉴얼", "론칭",
    # 화재 후속 (날짜만 바꿔 계속 나온다)
    "꺼지지", "시간째", "이틀째", "사흘째", "나흘째", "재발", "인명피해",
    # 지자체장·대학 홍보
    "음주운전", "근절", "수시", "정시", "신입생", "학과", "총장",
    "시장님", "군수", "구청장", "의정", "공약",
    # 연재·기획물
    "기획-", "100년", "연재", "특별기고",
    # 신문 묶음기사·정치
    "주요 신문", "사설", "조간", "뉴스브리핑", "뉴스프레소", "뉴스UP",
    "패트롤", "민주당", "국민의힘", "대통령실", "여야",
]


def search(query, cid, csec, display=DISPLAY):
    url = f"{API}?query={urllib.parse.quote(query)}&display={display}&sort=date"
    req = urllib.request.Request(url, headers={
        "X-Naver-Client-Id": cid,
        "X-Naver-Client-Secret": csec,
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _check_keys(cid, csec):
    """
    키가 실제 값인지 확인한다.
    한글이 섞여 있으면 HTTP 헤더에 못 담아서
    'latin-1 codec can't encode' 라는 알아보기 힘든 오류가 난다.
    그 전에 미리 잡아준다.
    """
    problems = []
    for label, val in (("NAVER_CLIENT_ID", cid), ("NAVER_CLIENT_SECRET", csec)):
        if not val:
            problems.append(f"  {label} 가 keys.txt 에 없습니다")
            continue
        try:
            val.encode("ascii")
        except UnicodeEncodeError:
            problems.append(
                f"  {label} 에 한글이 들어 있습니다 -> '{val[:14]}'\n"
                f"    예시 문구를 그대로 두신 것 같습니다.\n"
                f"    developers.naver.com 에서 받은 영문·숫자 값으로 바꾸세요.")
            continue
        if len(val) < 8:
            problems.append(f"  {label} 가 너무 짧습니다 -> '{val}'")
    return problems


def collect_naver(hours_back=HOURS_BACK, verbose=True):
    """news_report.py 의 items 와 같은 형식으로 반환."""
    keys = load_keys()
    cid = keys.get("NAVER_CLIENT_ID")
    csec = keys.get("NAVER_CLIENT_SECRET")

    problems = _check_keys(cid, csec)
    if problems:
        if verbose:
            print("\n  [네이버] 키 설정 문제로 건너뜁니다")
            for p in problems:
                print(p)
            print("\n  발급 방법")
            print("    1) developers.naver.com 로그인")
            print("    2) Application > 애플리케이션 등록")
            print("    3) 사용 API 에서 '검색' 체크")
            print("    4) WEB 설정 > 서비스 URL 에 http://localhost")
            print("    5) 나온 Client ID / Client Secret 을 keys.txt 에 입력")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    out, kept = [], []

    if verbose:
        print(f"\n네이버 검색 API / 키워드 {len(QUERIES)}개 / 최근 {hours_back}시간\n")

    for q, cat, must_all, must_any, scope in QUERIES:
        try:
            data = search(q, cid, csec)
        except Exception as e:
            msg = str(e)
            # 키가 로그에 찍히지 않도록
            msg = msg.replace(cid, "***").replace(csec, "***")
            if verbose:
                print(f"  {q:<18} 실패: {msg[:50]}")
            time.sleep(REQUEST_DELAY)
            continue

        keep, dropped, dup = 0, 0, 0
        for it in data.get("items", []):
            if keep >= MAX_PER_QUERY:
                break

            when = _parse_date(it.get("pubDate", ""))
            if when and when < cutoff:
                continue

            title = _strip(it.get("title", ""))
            if not title:
                continue

            desc = _strip(it.get("description", ""))

            # 지자체 보도자료·사건사고 걷어내기
            if any(w in title for w in NOISE_WORDS):
                dropped += 1
                continue

            # 네이버가 느슨하게 물어온 것을 여기서 조인다. 판단은 제목으로.
            if not match_terms(title, title + " " + desc, must_all, must_any, scope):
                dropped += 1
                continue

            # 같은 사건이 언론사만 바꿔 여러 건 들어온다
            is_dup, sig = is_duplicate(title, kept)
            if is_dup:
                dup += 1
                continue
            kept.append(sig)

            # 카카오 웹 도메인이 10개뿐이다.
            # 네이버 링크를 쓰면 n.news.naver.com 하나로 전 언론사가 열린다.
            nlink = it.get("link", "")
            olink = it.get("originallink", "")
            link = nlink if "naver.com" in nlink else (olink or nlink)
            if not link.startswith("http"):
                continue

            out.append({
                "source": f"N:{q}",          # 정원 계산용. 검색어 단위로 상한
                "outlet": outlet_name(olink),  # 카톡·리포트에 찍히는 이름
                "cat": cat,
                "mode": "pure",   # 필수단어를 이미 통과했으므로 앵커 검사 생략
                "title": title,
                "link": link,
                "desc": desc,
                "when": (when or datetime.now(timezone.utc)),
            })
            keep += 1

        if verbose:
            print(f"  {q:<18} {keep:>2}건  (걸러냄 {dropped}, 중복 {dup})")
        time.sleep(REQUEST_DELAY)

    if verbose:
        print(f"\n  네이버 합계 {len(out)}건 (중복 제거 후)")
    return out


if __name__ == "__main__":
    items = collect_naver()
    print("\n--- 상위 20건 ---")
    for it in sorted(items, key=lambda x: x["when"], reverse=True)[:20]:
        print(f"  [{it['cat']:<6}] {it['when'].astimezone(KST):%m/%d %H:%M} "
              f"{it['title'][:46]}")
