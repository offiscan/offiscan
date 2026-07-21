"""
OFFISCAN - 경기도 공장·창고 실거래 리포트
국토교통부_공장 및 창고 등 부동산 매매 실거래가
-> 대지 평단가 / 연면적 평단가 동시 산출 -> CSV + 카카오톡

사용법:
  py indu_report.py          전체 수집 후 리포트
  py indu_report.py diag     [먼저 이거 실행] 응답 필드 이름 확인용

* keys.txt 는 land_report.py 와 같은 것을 쓴다 (같은 인증키)
"""

import os
import csv
import json
import time
import statistics
from datetime import datetime
from collections import defaultdict
import xml.etree.ElementTree as ET

import requests

# ===================== CONFIG =====================

# 규모 구간 (대지 평 기준). 공장·창고는 대지 규모로 말하는 것이 실무 감각.
SIZE_BANDS = [
    ("소형(~500평)", 0, 500),
    ("중형(500~2천평)", 500, 2000),
    ("대형(2천평~)", 2000, 10**9),
]

# 건폐율(연면적÷대지)이 이 값 미만이면 사실상 토지 거래로 본다.
# 창고 한 채 딸린 나대지를 연면적으로 나누면 평당 2,900만원 같은 허수가 나온다.
MIN_FAR_FOR_BLDG_STAT = 0.20

# 특정 시군만 조회하려면 여기에 이름을 적는다. 비우면 경기도 전체.
#   예) ONLY_CITIES = ["안성시", "이천시", "여주시"]
ONLY_CITIES = []

MONTHS_BACK = 12                # 공장창고는 거래가 적어 12개월 권장
MIN_PYEONG = 100                # 대지 100평 미만 제외 (소형 근생 섞임 방지)
MIN_COUNT_FOR_STAT = 3
REQUEST_DELAY = 0.15
MAX_ROWS_PER_CITY = 30          # 리포트에 넣을 개별거래 수

# 리포트 앞쪽에 배치할 주력 시군
REPORT_CITIES = ["이천시", "여주시", "안성시", "평택시", "용인시", "화성시", "광주시", "양평군"]
# 카톡에 시세를 넣을 시군. 여기 적은 순서대로 나온다.
#   한 곳만 보려면  MAIN_CITIES = ["안성시"]
#   비워두면 []      ROTATION 순서대로 매주 한 곳씩 돌아가며 나온다.
#   이름은 "안성" 이 아니라 "안성시" 처럼 정확히 적을 것.
MAIN_CITIES = ["안성시", "이천시", "여주시", "평택시"]

# --- 월별/분기/연간 흐름 표 ---
# 매주 붙이면 똑같은 표가 반복돼서 안 보게 된다. 그래서 기본은 "auto".
#   "auto"   매달 첫째 월요일에만. 분기 다음 달이면 분기표, 1월이면 연간표까지
#   "always" 매번 다 붙인다
#   "never"  안 붙인다
FLOW_MODE = "auto"

# 월별 흐름에서 전체와 나란히 보여줄 용도지역
FLOW_ZONE = "계획관리"

# 흐름 표에 쓸 최소 건수. 이보다 적은 달은 중앙값 대신 '-' 로 둔다.
FLOW_MIN = 1
ROTATION = ["안성시", "이천시", "여주시", "평택시", "용인시", "화성시", "광주시", "양평군"]

# 건축물 주용도 필터. 빈 리스트면 전체.
# API는 공장/창고시설/운수시설/위험물저장처리/자동차관련/자원순환/동식물관련 7종을 준다.
USE_FILTER = ["공장", "창고"]

REGIONS = {
    "41111": ("수원시", "남부"), "41113": ("수원시", "남부"),
    "41115": ("수원시", "남부"), "41117": ("수원시", "남부"),
    "41131": ("성남시", "남부"), "41133": ("성남시", "남부"), "41135": ("성남시", "남부"),
    "41150": ("의정부시", "북부"),
    "41171": ("안양시", "남부"), "41173": ("안양시", "남부"),
    "41192": ("부천시", "남부"), "41194": ("부천시", "남부"), "41196": ("부천시", "남부"),
    "41210": ("광명시", "남부"),
    "41220": ("평택시", "남부"),
    "41250": ("동두천시", "북부"),
    "41271": ("안산시", "남부"), "41273": ("안산시", "남부"),
    "41281": ("고양시", "북부"), "41285": ("고양시", "북부"), "41287": ("고양시", "북부"),
    "41290": ("과천시", "남부"),
    "41310": ("구리시", "북부"),
    "41360": ("남양주시", "북부"),
    "41370": ("오산시", "남부"),
    "41390": ("시흥시", "남부"),
    "41410": ("군포시", "남부"),
    "41430": ("의왕시", "남부"),
    "41450": ("하남시", "남부"),
    "41461": ("용인시", "남부"), "41463": ("용인시", "남부"), "41465": ("용인시", "남부"),
    "41480": ("파주시", "북부"),
    "41500": ("이천시", "남부"),
    "41550": ("안성시", "남부"),
    "41570": ("김포시", "남부"),
    "41591": ("화성시", "남부"), "41593": ("화성시", "남부"),
    "41595": ("화성시", "남부"), "41597": ("화성시", "남부"),
    "41610": ("광주시", "남부"),
    "41630": ("양주시", "북부"),
    "41650": ("포천시", "북부"),
    "41670": ("여주시", "남부"),
    "41800": ("연천군", "북부"),
    "41820": ("가평군", "북부"),
    "41830": ("양평군", "남부"),
}

INDU_API = "http://apis.data.go.kr/1613000/RTMSDataSvcInduTrade/getRTMSDataSvcInduTrade"
KAKAO_API = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
PYEONG = 3.305785

# 필드 별칭 — API가 어떤 이름을 쓰든 잡아낸다
F_DONG = ("umdNm", "법정동")
F_AMOUNT = ("dealAmount", "거래금액")
F_PLOT = ("plottageAr", "plottageAR", "landAr", "대지면적")          # 대지면적 m2
F_BLDG = ("buildingAr", "buildingAR", "totalFloorAr", "건물면적")     # 건축(연)면적 m2
F_USE = ("buildingUse", "mainPurpsCdNm", "건물주용도")
F_ZONE = ("landUse", "용도지역")
F_TYPE = ("buildingType", "건물유형")                                # 일반/집합
F_YEAR = ("dealYear", "년")
F_MONTH = ("dealMonth", "월")
F_BUILD_YEAR = ("buildYear", "건축년도")
F_DEALING = ("dealingGbn", "거래유형")        # 직거래 / 중개거래
F_CANCEL = ("cdealType", "해제여부")          # 값이 있으면 해제된 거래
F_BUYER = ("buyerGbn", "매수자")              # 법인 / 개인
F_SELLER = ("slerGbn", "매도자")

def size_band(plot_py):
    for name, lo, hi in SIZE_BANDS:
        if lo <= plot_py < hi:
            return name
    return SIZE_BANDS[-1][0]


def far(r):
    """건폐율(연면적/대지). 다층이면 1.0을 넘는다."""
    return r["bldg_py"] / r["plot_py"] if r["plot_py"] else 0


def bldg_stat_ok(r):
    """연면적 평단가를 통계에 넣어도 되는 물건인가."""
    return r["per_bldg"] > 0 and far(r) >= MIN_FAR_FOR_BLDG_STAT


DATA_KEY = ""
KAKAO_ACCESS_TOKEN = ""
FAIL_SHOWN = False


def load_keys():
    global DATA_KEY, KAKAO_ACCESS_TOKEN
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.txt")
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"').strip("'")
    DATA_KEY = os.environ.get("DATA_GO_KR_KEY") or data.get("DATA_GO_KR_KEY", "")
    KAKAO_ACCESS_TOKEN = os.environ.get("KAKAO_ACCESS_TOKEN") or data.get("KAKAO_ACCESS_TOKEN", "")
    if not DATA_KEY:
        print("[중단] keys.txt 에 DATA_GO_KR_KEY 가 없습니다.")
        raise SystemExit


# ===================== 유틸 =====================

def q(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def stat_block(prices):
    return {"count": len(prices), "median": statistics.median(prices),
            "q75": q(prices, 0.75), "q90": q(prices, 0.90)}


def _text(node, names):
    for n in names:
        el = node.find(n)
        if el is not None and el.text:
            return el.text.strip()
    return ""


def _num(node, names):
    v = _text(node, names).replace(",", "")
    try:
        return float(v)
    except ValueError:
        return 0.0


def recent_months(n):
    now = datetime.now()
    y, m = now.year, now.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        out.append(f"{y}{m:02d}")
    return sorted(out)


# ===================== 수집 =====================

def mask(text):
    """어떤 문자열에서도 인증키를 지운다. 화면 캡처 사고 방지."""
    t = str(text)
    if DATA_KEY:
        t = t.replace(DATA_KEY, "***KEY***")
        try:
            from urllib.parse import quote
            t = t.replace(quote(DATA_KEY, safe=""), "***KEY***")
        except Exception:
            pass
    return t


def call(lawd_cd, ym):
    """(성공여부, 응답텍스트 또는 에러메시지). 예외를 밖으로 던지지 않는다."""
    params = {"serviceKey": DATA_KEY, "LAWD_CD": lawd_cd, "DEAL_YMD": ym,
              "pageNo": 1, "numOfRows": 1000}
    try:
        r = requests.get(INDU_API, params=params, timeout=20)
    except Exception as e:
        return False, f"네트워크 오류: {mask(e)}"

    if r.status_code == 403:
        return False, ("403 Forbidden - 이 API에 인증키가 아직 등록되지 않았습니다.\n"
                       "      활용신청 직후면 1~2시간 기다리세요. "
                       "마이페이지 > 오픈API > 개발계정 에 목록이 있는지 확인.")
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {mask(r.text)[:200]}"

    body = r.text
    if "SERVICE_KEY_IS_NOT_REGISTERED" in body or "<returnReasonCode>30" in body:
        return False, "인증키 미등록(코드30) - 활용신청 반영 대기 중이거나 키가 틀렸습니다."
    if "LIMITED_NUMBER_OF_SERVICE_REQUESTS" in body:
        return False, "일일 호출한도 초과."
    return True, body


def diagnose():
    """응답 필드 이름을 눈으로 확인한다. 거래가 있을 때까지 몇 개 시군을 시도."""
    print("=== 진단 모드: 실제 응답 필드 확인 ===\n")
    for code, name in [("41550", "안성"), ("41500", "이천"), ("41220", "평택"),
                       ("41591", "화성"), ("41670", "여주")]:
        for ym in recent_months(6)[::-1]:
            ok, text = call(code, ym)
            if not ok:
                print(f"  {name} {ym}: {text}")
                time.sleep(REQUEST_DELAY)
                continue
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                print(f"  {name} {ym}: XML 파싱 실패 - {mask(text)[:150]}")
                continue
            items = list(root.iter("item"))
            if not items:
                continue
            print(f"[{name} {ym}] {len(items)}건 발견. 첫 건의 전체 필드:\n")
            for ch in items[0]:
                print(f"   {ch.tag:<22} = {(ch.text or '').strip()}")
            print("\n위 목록을 그대로 복사해서 알려주시면 코드를 맞추겠습니다.")
            return
        time.sleep(REQUEST_DELAY)
    print("\n필드를 확인하지 못했습니다. 위 메시지를 확인하세요.")


def parse(root, fallback_ym):
    rows = []
    for item in root.iter("item"):
        amount = _num(item, F_AMOUNT)          # 만원
        plot = _num(item, F_PLOT)              # 대지면적 m2
        bldg = _num(item, F_BLDG)              # 연면적 m2
        if amount <= 0:
            continue

        # 해제된 거래는 통계에서 뺀다. 계약이 취소된 건이라 시세가 아니다.
        if _text(item, F_CANCEL):
            continue

        use = _text(item, F_USE)
        if USE_FILTER and use and not any(u in use for u in USE_FILTER):
            continue

        plot_py = plot / PYEONG if plot > 0 else 0
        bldg_py = bldg / PYEONG if bldg > 0 else 0
        if plot_py and plot_py < MIN_PYEONG:
            continue

        y = _text(item, F_YEAR)
        m = _text(item, F_MONTH)
        rows.append({
            "dong": _text(item, F_DONG),
            "use": use,
            "zone": _text(item, F_ZONE),
            "btype": _text(item, F_TYPE),
            "build_year": _text(item, F_BUILD_YEAR),
            "dealing": _text(item, F_DEALING),
            "buyer": _text(item, F_BUYER),
            "seller": _text(item, F_SELLER),
            "plot_py": plot_py,
            "bldg_py": bldg_py,
            "total_manwon": amount,
            "per_plot": amount / plot_py if plot_py else 0,
            "per_bldg": amount / bldg_py if bldg_py else 0,
            "ym": f"{y}-{int(m):02d}" if y and m else fallback_ym,
        })
    return rows


def collect():
    months = recent_months(MONTHS_BACK)
    print(f"조회 기간: {months[0]} ~ {months[-1]}")
    codes = {c: v for c, v in REGIONS.items() if not ONLY_CITIES or v[0] in ONLY_CITIES}
    print(f"대상 {len(set(v[0] for v in codes.values()))}개 시군 / 호출 {len(codes) * len(months)}회\n")

    global FAIL_SHOWN
    FAIL_SHOWN = False
    by_city = defaultdict(list)
    city_region = {}
    done, total = 0, len(REGIONS) * len(months)
    for code, (city, area) in REGIONS.items():
        if ONLY_CITIES and city not in ONLY_CITIES:
            done += len(months)
            continue
        city_region[city] = area
        for ym in months:
            ok, text = call(code, ym)
            rows = []
            if not ok:
                if not FAIL_SHOWN:
                    print(f"    [실패] {text}")
                    FAIL_SHOWN = True
            else:
                try:
                    rows = parse(ET.fromstring(text), ym)
                except ET.ParseError:
                    print(f"    [파싱실패] {code}/{ym}: {mask(text)[:120]}")
            for r in rows:
                r["city"], r["area_group"] = city, area
            by_city[city].extend(rows)
            done += 1
            time.sleep(REQUEST_DELAY)
        print(f"  {city:<8} 누적 {len(by_city[city]):>4}건   ({done}/{total})")
    return by_city, city_region, months


# ===================== 신규 거래 판별 =====================

MASTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "indu_master.csv")


def deal_key(r):
    """거래 1건을 식별하는 열쇠.
    거래월은 넣지 않는다. 같은 물건이 정정신고로 여러 달에 걸쳐
    중복 등재되는 경우가 많아, 월을 넣으면 신규로 오인한다."""
    return "|".join([
        r["city"], r["dong"],
        str(int(r["total_manwon"])),
        str(int(r["plot_py"])), str(int(r["bldg_py"])),
    ])


def dedupe(by_city):
    """수집 결과 내부의 중복 신고를 제거한다. 가장 최근 거래월만 남긴다."""
    removed = 0
    for city, rows in by_city.items():
        best = {}
        for r in rows:
            k = deal_key(r)
            if k not in best or r["ym"] > best[k]["ym"]:
                if k in best:
                    removed += 1
                best[k] = r
            else:
                removed += 1
        by_city[city] = list(best.values())
    if removed:
        print(f"\n[중복 제거] {removed}건 (같은 물건의 재신고·정정신고)")
    return by_city


def load_seen():
    """이전에 본 거래 키 집합."""
    seen = set()
    if os.path.exists(MASTER):
        with open(MASTER, encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if row:
                    seen.add(row[0])
    return seen


def mark_new(by_city):
    """각 행에 is_new 를 달고, 신규 목록을 돌려준다. 마스터도 갱신."""
    seen = load_seen()
    first_run = not seen
    news = []
    today = datetime.now().strftime("%Y-%m-%d")

    with open(MASTER, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for rows in by_city.values():
            for r in rows:
                k = deal_key(r)
                if k in seen:
                    r["is_new"] = False
                else:
                    r["is_new"] = not first_run   # 첫 실행은 전부 '신규'로 보지 않는다
                    if r["is_new"]:
                        news.append(r)
                    seen.add(k)
                    w.writerow([k, today])

    if first_run:
        print(f"\n[기준선 생성] {sum(len(v) for v in by_city.values())}건을 기록했습니다.")
        print("             다음 실행부터 '신규 거래'가 표시됩니다.")
    else:
        print(f"\n[신규 거래] {len(news)}건")
    news.sort(key=lambda r: r["total_manwon"], reverse=True)
    return news, first_run


def build_new_section(news):
    if not news:
        return "이번 주 신규 거래 없음"
    out = [f"◆ 신규 거래 {len(news)}건 (금액順)"]
    for r in news[:20]:
        out.append(
            f"   경기도 {r['city']} {r['dong']} {r['ym']} {r['use'][:6]} "
            f"대지 {r['plot_py']:,.0f}평 · 연 {r['bldg_py']:,.0f}평(건폐 {far(r)*100:.0f}%) · "
            f"총 {r['total_manwon']/10000:,.1f}억 · "
            f"대지 {r['per_plot']:,.0f} ({r['build_year']}년, {r['zone']})")
    return "\n".join(out)


# ===================== 출력 =====================

def save_csv(by_city):
    stamp = datetime.now().strftime("%Y%m%d")
    path = f"indu_detail_{stamp}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["권역", "시군", "읍면동", "주용도", "용도지역", "건물유형", "건축년도", "규모", "건폐율(%)", "거래유형", "매도자", "매수자",
                    "대지(평)", "연면적(평)", "거래금액(만원)",
                    "대지평단가(만원)", "연면적평단가(만원)", "거래월", "신규"])
        for rows in by_city.values():
            for r in rows:
                w.writerow([r["area_group"], r["city"], r["dong"], r["use"], r["zone"],
                            r["btype"], r["build_year"], size_band(r["plot_py"]), round(far(r) * 100, 1),
                            r.get("dealing", ""), r.get("seller", ""), r.get("buyer", ""),
                            round(r["plot_py"], 1), round(r["bldg_py"], 1),
                            int(r["total_manwon"]),
                            round(r["per_plot"], 1), round(r["per_bldg"], 1), r["ym"],
                            "NEW" if r.get("is_new") else ""])
    print(f"\n저장: {path}")
    return path


def build_report(by_city, months, news=None):
    period = f"{months[0][:4]}.{months[0][4:]}~{months[-1][:4]}.{months[-1][4:]}"
    out = [f"OFFISCAN 공장·창고 실거래 리포트  ({period})",
           "단가 만원/평 · 중앙값/상위25% · 대지단가=금액÷대지면적, 연면적단가=금액÷연면적",
           f"※ 규모는 대지 기준. 건폐율 {MIN_FAR_FOR_BLDG_STAT*100:.0f}% 미만은 사실상 토지거래로 보아 연면적단가에서 제외.", ""]
    if news is not None:
        out += ["=" * 60, build_new_section(news), "=" * 60, ""]

    order = [c for c in REPORT_CITIES if by_city.get(c)]
    order += sorted([c for c in by_city if c not in REPORT_CITIES and by_city[c]],
                    key=lambda c: len(by_city[c]), reverse=True)

    for city in order:
        rows = by_city[city]
        if not rows:
            continue
        out.append(f"■ 경기도 {city}   {len(rows)}건")

        # 규모별 통계 - 29평과 35,000평을 한 중앙값에 넣으면 의미가 없다
        for band, lo, hi in SIZE_BANDS:
            sub = [r for r in rows if lo <= r["plot_py"] < hi]
            if len(sub) < MIN_COUNT_FOR_STAT:
                continue
            plots = [r["per_plot"] for r in sub if r["per_plot"] > 0]
            blds = [r["per_bldg"] for r in sub if bldg_stat_ok(r)]
            line = f"   {band:<16} {len(sub):>3}건"
            if len(plots) >= MIN_COUNT_FOR_STAT:
                st = stat_block(plots)
                line += f" | 대지 {st['median']:>6,.0f}/{st['q75']:>6,.0f}"
            if len(blds) >= MIN_COUNT_FOR_STAT:
                st = stat_block(blds)
                line += f" | 연면적 {st['median']:>6,.0f}/{st['q75']:>6,.0f}"
            out.append(line)
        out.append("")

        # 거래유형별 - 직거래는 특수관계가 섞여 시세보다 낮게 잡히는 경우가 많다
        deal_g = defaultdict(list)
        for r in rows:
            if r["per_plot"] > 0 and r.get("dealing"):
                deal_g[r["dealing"]].append(r["per_plot"])
        dlist = [(g, stat_block(v)) for g, v in deal_g.items() if len(v) >= MIN_COUNT_FOR_STAT]
        if len(dlist) >= 2:
            dlist.sort(key=lambda x: x[1]["median"], reverse=True)
            out.append("   [거래유형별 대지단가]")
            for g, st in dlist:
                out.append(f"     {g:<10} {st['count']:>3}건 · 중앙 {st['median']:>6,.0f} · 상위25% {st['q75']:>6,.0f}")
            out.append("")

        # 용도지역별 대지단가 - 실무에서 가장 먼저 보는 축
        zones = defaultdict(list)
        for r in rows:
            if r["per_plot"] > 0 and r["zone"]:
                zones[r["zone"]].append(r["per_plot"])
        zlist = [(z, stat_block(v)) for z, v in zones.items() if len(v) >= MIN_COUNT_FOR_STAT]
        if zlist:
            zlist.sort(key=lambda x: x[1]["median"], reverse=True)
            out.append("   [용도지역별 대지단가]")
            for z, st in zlist:
                out.append(f"     {z:<10} {st['count']:>3}건 · 중앙 {st['median']:>6,.0f} · 상위25% {st['q75']:>6,.0f}")
            out.append("")

        rows = sorted(rows, key=lambda r: r["total_manwon"], reverse=True)
        for r in rows[:MAX_ROWS_PER_CITY]:
            out.append(
                f"   {city} {r['dong']:<10} {r['ym']} {r['use'][:6]:<5} "
                f"대지 {r['plot_py']:>7,.0f}평 · 연 {r['bldg_py']:>7,.0f}평(건폐 {far(r)*100:>3.0f}%) · "
                f"총 {r['total_manwon']/10000:>6,.1f}억 · "
                f"대지 {r['per_plot']:>5,.0f}" +
                (f" / 연 {r['per_bldg']:>5,.0f}" if bldg_stat_ok(r) else " / 연   -  ") +
                f"  ({r['build_year']}년, {r['zone']}, {r.get('dealing','')})")
        out.append("")
    return "\n".join(out)


def save_report(text):
    stamp = datetime.now().strftime("%Y%m%d")
    path = f"indu_report_{stamp}.txt"
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text)
    print(f"저장: {path}")
    return path


def _ym(v):
    """'2026-07' 과 '202607' 을 같은 키로 맞춘다."""
    return (v or "").replace("-", "")


def _med(rows):
    """대지평단가 중앙값. 값이 없으면 None."""
    v = [r["per_plot"] for r in rows if r["per_plot"] > 0]
    if len(v) < FLOW_MIN:
        return None
    return statistics.median(v)


def _cell(rows):
    m = _med(rows)
    return f"{len(rows):>2}건 {m:>4,.0f}" if m else f"{len(rows):>2}건    -"


def flow_monthly(rows, months):
    """월별 흐름. 전체와 계획관리를 나란히."""
    L = [f"── 월별 흐름 (전체 / {FLOW_ZONE}) ──"]
    for ym in months:
        sub = [r for r in rows if _ym(r["ym"]) == _ym(ym)]
        zsub = [r for r in sub if FLOW_ZONE in (r["zone"] or "")]
        k = _ym(ym)
        L.append(f"{k[2:4]}.{k[4:]}  {_cell(sub)}  |  {_cell(zsub)}")
    L.append("")
    return L


def flow_quarterly(rows, months):
    """분기 흐름. 최근 12개월을 3개월씩 묶는다."""
    L = ["── 분기 흐름 ──"]
    for i in range(0, len(months) - 2, 3):
        grp = months[i:i + 3]
        keys = {_ym(g) for g in grp}
        sub = [r for r in rows if _ym(r["ym"]) in keys]
        zsub = [r for r in sub if FLOW_ZONE in (r["zone"] or "")]
        k = _ym(grp[0])
        y, m = k[2:4], int(k[4:])
        L.append(f"{y}.{(m - 1) // 3 + 1}Q  {_cell(sub)}  |  {_cell(zsub)}")
    L.append("")
    return L


def flow_annual(rows, months):
    """12개월 총괄."""
    zsub = [r for r in rows if FLOW_ZONE in (r["zone"] or "")]
    L = ["── 연간 총괄 ──",
         f"기간  {_ym(months[0])} ~ {_ym(months[-1])}",
         f"전체     {_cell(rows)}",
         f"{FLOW_ZONE}  {_cell(zsub)}"]
    plots = [r["per_plot"] for r in rows if r["per_plot"] > 0]
    if len(plots) >= MIN_COUNT_FOR_STAT:
        st = stat_block(plots)
        L.append(f"상위25% {st['q75']:,.0f} / 상위10% {st['q90']:,.0f}")
    L.append("")
    return L


def which_flows(today=None):
    """오늘 날짜로 어떤 표를 붙일지 정한다."""
    if FLOW_MODE == "never":
        return set()
    if FLOW_MODE == "always":
        return {"month", "quarter", "year"}

    d = today or datetime.now()
    # 첫째 월요일: 그 달의 1~7일 사이 월요일
    if not (d.weekday() == 0 and d.day <= 7):
        return set()

    f = {"month"}
    # 분기가 끝난 다음 달(4·7·10·1월)은 신고가 덜 들어와 한 달 늦춘다
    if d.month in (2, 5, 8, 11):
        f.add("quarter")
    if d.month == 1:
        f.update({"quarter", "year"})
    return f


def news_block(news):
    """이번 주 신규. 한 줄로는 비싼지 싼지 판단이 안 돼서 4줄로 편다."""
    L = [f"━━ 이번 주 신규 {len(news)}건 ━━", ""]
    for r in news:
        L.append(f"{r['city']} {r['dong']}")
        L.append(f" 대지 {r['plot_py']:,.0f}평 / 연면적 {r['bldg_py']:,.0f}평")
        line = f" {r['total_manwon']/10000:,.1f}억"
        if r["per_plot"] > 0:
            line += f" · 대지평당 {r['per_plot']:,.0f}만"
        L.append(line)
        tail = [x for x in (r["zone"], f"{r['build_year']}년" if r["build_year"] else "",
                            f"건폐율 {far(r)*100:,.0f}%" if r["plot_py"] else "") if x]
        if tail:
            L.append(" " + " · ".join(tail))
        L.append("")
    return L


def city_block(city, rows, flows, months):
    L = [f"━━ {city} ━━"]
    if not rows:
        L += ["(해당 기간 거래 없음)", ""]
        return L

    plots = [r["per_plot"] for r in rows if r["per_plot"] > 0]
    blds = [r["per_bldg"] for r in rows if bldg_stat_ok(r)]
    L.append(f"◆ {len(rows)}건")
    if len(plots) >= MIN_COUNT_FOR_STAT:
        st = stat_block(plots)
        L.append(f" 대지단가 중앙 {st['median']:,.0f} / 상위25% {st['q75']:,.0f}")
    if len(blds) >= MIN_COUNT_FOR_STAT:
        st = stat_block(blds)
        L.append(f" 연면적단가 중앙 {st['median']:,.0f} / 상위25% {st['q75']:,.0f}")
    L.append("")

    L.append("◆ 금액 상위")
    for r in sorted(rows, key=lambda x: x["total_manwon"], reverse=True)[:6]:
        L.append(f" {r['dong']} 연{r['bldg_py']:,.0f}평 {r['total_manwon']/10000:,.1f}억 "
                 f"(연{r['per_bldg']:,.0f})")
    L.append("")

    if "month" in flows:
        L += flow_monthly(rows, months)
    if "quarter" in flows:
        L += flow_quarterly(rows, months)
    if "year" in flows:
        L += flow_annual(rows, months)
    return L


def build_message(by_city, months, news=None, today=None):
    a, b = _ym(months[0]), _ym(months[-1])
    period = f"{a[2:4]}.{a[4:]}~{b[2:4]}.{b[4:]}"
    flows = which_flows(today)

    cities = list(MAIN_CITIES)
    if not cities:
        week = (today or datetime.now()).isocalendar()[1]
        cities = [ROTATION[week % len(ROTATION)]]

    lines = ["[OFFISCAN] 공장·창고 브리핑", f"{period} 기준 · 만원/평", ""]

    if news:
        lines += news_block(news[:5])

    for c in cities:
        lines += city_block(c, by_city.get(c, []), flows, months)

    lines.append("상세 거래내역은 깃허브 reports 폴더")
    return "\n".join(lines)


def send_kakao(text):
    try:
        from kakao_send import send_long as send
    except ImportError:
        print("[카카오] kakao_send.py 가 같은 폴더에 없습니다.")
        return False
    return send(text)


# ===================== 실행 =====================

if __name__ == "__main__":
    import sys
    load_keys()

    if len(sys.argv) > 1 and sys.argv[1] == "diag":
        diagnose()
        raise SystemExit

    by_city, city_region, months = collect()
    if not any(by_city.values()):
        print("\n수집된 거래가 없습니다. 먼저 'py indu_report.py diag' 로 필드를 확인하세요.")
        raise SystemExit

    by_city = dedupe(by_city)
    news, first_run = mark_new(by_city)
    save_csv(by_city)
    rpt = build_report(by_city, months, None if first_run else news)
    save_report(rpt)
    print("\n" + rpt)

    msg = build_message(by_city, months, None if first_run else news)
    print("\n" + "-" * 44)
    print(msg)
    print(f"-- {len(msg)}자 --\n")
    send_kakao(msg)
