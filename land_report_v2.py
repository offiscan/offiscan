"""
OFFISCAN - 경기도 토지 실거래 자동 리포트 v2
국토교통부 토지 매매 실거래가 -> 시군 x 용도지역 x 원지/기개발 평당가 집계
-> 카카오톡 요약 전송 + CSV 상세 저장

v2 변경점
  - 쓰레기 지목(구거/묘지/하천/도로 등) 제외
  - 원지(전답임야) / 기개발(공장창고대) 분리 집계
  - 중앙값 + 상위25%(3사분위) 병기
  - 원지는 최소 평수 필터 별도 적용 (소규모 필지가 중앙값 끌어내리는 문제)

사용법:
  1) 같은 폴더에 keys.txt (키 2줄)
  2) py -m pip install requests
  3) py land_report.py
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

MONTHS_BACK = 3                 # 최근 몇 개월
FILTER_ZONES = ["계획관리", "생산관리", "보전관리", "자연녹지", "일반공업", "준공업"]
MIN_AREA_M2 = 300               # 전체 최소 필지 면적
MIN_PYEONG_WONJI = 300          # 원지 통계에 넣을 최소 평수 (소규모 텃밭 제외)
MIN_COUNT_FOR_STAT = 3          # 통계 산출 최소 건수
REQUEST_DELAY = 0.15

# 카톡에 상세 출력할 주력 시군
FOCUS_CITIES = ["이천시", "여주시", "안성시", "평택시", "용인시", "화성시", "광주시", "양평군"]
FOCUS_ZONE = "계획관리"

# --- 지목 분류 ---
JIMOK_DROP = {
    "구거", "묘지", "하천", "염전", "체육용지", "도로", "유지", "제방",
    "수도용지", "철도용지", "공원", "종교용지", "사적지", "광천지", "양어장",
}
JIMOK_WONJI = {"전", "답", "임야", "과수원", "목장용지"}
JIMOK_GAEBAL = {"공장용지", "창고용지", "대", "주차장", "주유소용지", "학교용지", "잡종지"}


def classify(jimok):
    """지목 -> 원지 / 기개발 / 기타"""
    j = (jimok or "").strip()
    if j in JIMOK_WONJI:
        return "원지"
    if j in JIMOK_GAEBAL:
        return "기개발"
    return "기타"


# 경기도 31개 시군 / 47개 조회코드 (국토교통부 법정동코드 전체자료 기준)
REGIONS = {
    "41111": ("수원시", "남부"),  # 장안구
    "41113": ("수원시", "남부"),  # 권선구
    "41115": ("수원시", "남부"),  # 팔달구
    "41117": ("수원시", "남부"),  # 영통구
    "41131": ("성남시", "남부"),  # 수정구
    "41133": ("성남시", "남부"),  # 중원구
    "41135": ("성남시", "남부"),  # 분당구
    "41150": ("의정부시", "북부"),
    "41171": ("안양시", "남부"),  # 만안구
    "41173": ("안양시", "남부"),  # 동안구
    "41192": ("부천시", "남부"),  # 원미구
    "41194": ("부천시", "남부"),  # 소사구
    "41196": ("부천시", "남부"),  # 오정구
    "41210": ("광명시", "남부"),
    "41220": ("평택시", "남부"),
    "41250": ("동두천시", "북부"),
    "41271": ("안산시", "남부"),  # 상록구
    "41273": ("안산시", "남부"),  # 단원구
    "41281": ("고양시", "북부"),  # 덕양구
    "41285": ("고양시", "북부"),  # 일산동구
    "41287": ("고양시", "북부"),  # 일산서구
    "41290": ("과천시", "남부"),
    "41310": ("구리시", "북부"),
    "41360": ("남양주시", "북부"),
    "41370": ("오산시", "남부"),
    "41390": ("시흥시", "남부"),
    "41410": ("군포시", "남부"),
    "41430": ("의왕시", "남부"),
    "41450": ("하남시", "남부"),
    "41461": ("용인시", "남부"),  # 처인구
    "41463": ("용인시", "남부"),  # 기흥구
    "41465": ("용인시", "남부"),  # 수지구
    "41480": ("파주시", "북부"),
    "41500": ("이천시", "남부"),
    "41550": ("안성시", "남부"),
    "41570": ("김포시", "남부"),
    "41591": ("화성시", "남부"),  # 만세구
    "41593": ("화성시", "남부"),  # 효행구
    "41595": ("화성시", "남부"),  # 병점구
    "41597": ("화성시", "남부"),  # 동탄구
    "41610": ("광주시", "남부"),
    "41630": ("양주시", "북부"),
    "41650": ("포천시", "북부"),
    "41670": ("여주시", "남부"),
    "41800": ("연천군", "북부"),
    "41820": ("가평군", "북부"),
    "41830": ("양평군", "남부"),
}

LAND_API = "http://apis.data.go.kr/1613000/RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade"
KAKAO_API = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
PYEONG = 3.305785

DATA_KEY = ""
KAKAO_ACCESS_TOKEN = ""


def load_keys():
    """keys.txt 를 읽어 전역 키를 채운다."""
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
    if not DATA_KEY or not KAKAO_ACCESS_TOKEN:
        print("[중단] keys.txt 에 DATA_GO_KR_KEY / KAKAO_ACCESS_TOKEN 이 모두 있어야 합니다.")
        raise SystemExit


# ===================== 통계 유틸 =====================

def q(values, p):
    """분위수 (선형보간). values 는 정렬 안 돼 있어도 됨."""
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
    return {
        "count": len(prices),
        "median": statistics.median(prices),
        "q75": q(prices, 0.75),
        "q90": q(prices, 0.90),
    }


# ===================== 수집 =====================

def _text(node, *names):
    for n in names:
        el = node.find(n)
        if el is not None and el.text:
            return el.text.strip()
    return ""


def recent_months(n):
    now = datetime.now()
    y, m = now.year, now.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        out.append(f"{y}{m:02d}")
    return sorted(out)


def parse_items(root, fallback_ym):
    """XML -> row 리스트. 필터/분류까지 여기서 끝낸다."""
    rows = []
    for item in root.iter("item"):
        dong = _text(item, "umdNm", "법정동")
        zone = _text(item, "landUse", "용도지역")
        jimok = _text(item, "jimok", "지목")
        area_s = _text(item, "dealArea", "거래면적").replace(",", "")
        price_s = _text(item, "dealAmount", "거래금액").replace(",", "")
        y = _text(item, "dealYear", "년")
        m = _text(item, "dealMonth", "월")

        try:
            area = float(area_s)
            price = float(price_s)          # 만원 단위
        except ValueError:
            continue

        if area < MIN_AREA_M2:
            continue
        if jimok.strip() in JIMOK_DROP:
            continue

        zone_key = next((z for z in FILTER_ZONES if z in zone), "")
        if FILTER_ZONES and not zone_key:
            continue

        py = area / PYEONG
        if py <= 0:
            continue

        rows.append({
            "dong": dong, "zone": zone, "zone_key": zone_key, "jimok": jimok,
            "cat": classify(jimok),
            "area_m2": round(area, 1), "pyeong": round(py, 1),
            "total_manwon": price, "per_pyeong": price / py,
            "ym": f"{y}-{int(m):02d}" if y and m else fallback_ym,
        })
    return rows


def fetch(lawd_cd, ym):
    params = {
        "serviceKey": DATA_KEY,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": ym,
        "pageNo": 1,
        "numOfRows": 1000,
    }
    text = ""
    try:
        r = requests.get(LAND_API, params=params, timeout=20)
        r.raise_for_status()
        text = r.text
        root = ET.fromstring(text)
    except ET.ParseError:
        print(f"    [파싱실패] {lawd_cd}/{ym}: {text[:150]}")
        return []
    except Exception as e:
        print(f"    [실패] {lawd_cd}/{ym}: {e}")
        return []

    code = _text(root, ".//resultCode", ".//returnReasonCode")
    if code and code not in ("00", "000"):
        msg = _text(root, ".//resultMsg", ".//returnAuthMsg", ".//errMsg")
        print(f"    [API오류] {code} {msg}")
        return []

    return parse_items(root, ym)


def collect():
    months = recent_months(MONTHS_BACK)
    cities = sorted(set(v[0] for v in REGIONS.values()))
    print(f"조회 기간: {months[0]} ~ {months[-1]}")
    print(f"대상: {len(cities)}개 시군 / 호출 {len(REGIONS) * len(months)}회\n")

    by_city = defaultdict(list)
    city_region = {}
    done, total = 0, len(REGIONS) * len(months)

    for code, (city, area) in REGIONS.items():
        city_region[city] = area
        for ym in months:
            rows = fetch(code, ym)
            for r in rows:
                r["city"] = city
                r["area_group"] = area
            by_city[city].extend(rows)
            done += 1
            time.sleep(REQUEST_DELAY)
        print(f"  {city:<8} 누적 {len(by_city[city]):>4}건   ({done}/{total})")

    return by_city, city_region, months


# ===================== 집계 =====================

def eligible(r):
    """통계에 넣을 행인가. 원지는 최소 평수 조건 추가."""
    if r["cat"] == "기타":
        return False
    if r["cat"] == "원지" and r["pyeong"] < MIN_PYEONG_WONJI:
        return False
    return True


def summarize(by_city, city_region):
    """[{city, area_group, count, zones:{zone:{cat:{stat}}}, dongs:{...}}]"""
    result = []
    for city, rows in by_city.items():
        rows = [r for r in rows if eligible(r)]
        if not rows:
            continue

        zones = defaultdict(lambda: defaultdict(list))
        dongs = defaultdict(lambda: defaultdict(list))
        for r in rows:
            zones[r["zone_key"]][r["cat"]].append(r["per_pyeong"])
            if r["zone_key"] == FOCUS_ZONE:
                dongs[r["dong"]][r["cat"]].append(r["per_pyeong"])

        zone_stat = {
            z: {c: stat_block(ps) for c, ps in cats.items() if len(ps) >= MIN_COUNT_FOR_STAT}
            for z, cats in zones.items()
        }
        dong_stat = {
            d: {c: stat_block(ps) for c, ps in cats.items() if len(ps) >= MIN_COUNT_FOR_STAT}
            for d, cats in dongs.items()
        }
        dong_stat = {d: v for d, v in dong_stat.items() if v}

        result.append({
            "city": city,
            "area_group": city_region.get(city, ""),
            "count": len(rows),
            "zones": zone_stat,
            "dongs": dong_stat,
        })
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


# ===================== 출력 =====================

def save_csv(by_city, summary):
    stamp = datetime.now().strftime("%Y%m%d")

    detail = f"land_detail_{stamp}.csv"
    with open(detail, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["권역", "시군", "읍면동", "용도지역", "지목", "구분",
                    "면적(m2)", "면적(평)", "거래금액(만원)", "평당가(만원)", "거래월", "통계포함"])
        for rows in by_city.values():
            for r in rows:
                w.writerow([r["area_group"], r["city"], r["dong"], r["zone"], r["jimok"], r["cat"],
                            r["area_m2"], r["pyeong"], int(r["total_manwon"]),
                            round(r["per_pyeong"], 1), r["ym"], "Y" if eligible(r) else "N"])

    summ = f"land_summary_{stamp}.csv"
    with open(summ, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["권역", "시군", "용도지역", "구분", "읍면동",
                    "건수", "중앙값(만원)", "상위25%(만원)", "상위10%(만원)"])
        for s in summary:
            for z in FILTER_ZONES:
                for c in ("원지", "기개발"):
                    st = s["zones"].get(z, {}).get(c)
                    if st:
                        w.writerow([s["area_group"], s["city"], z, c, "(시군전체)",
                                    st["count"], round(st["median"], 1),
                                    round(st["q75"], 1), round(st["q90"], 1)])
            for d, cats in sorted(s["dongs"].items()):
                for c in ("원지", "기개발"):
                    st = cats.get(c)
                    if st:
                        w.writerow([s["area_group"], s["city"], FOCUS_ZONE, c, d,
                                    st["count"], round(st["median"], 1),
                                    round(st["q75"], 1), round(st["q90"], 1)])

    print(f"\n저장: {detail} / {summ}")
    return detail, summ


def print_console(summary):
    print("\n" + "=" * 56)
    for s in summary:
        z = s["zones"].get(FOCUS_ZONE)
        if not z:
            continue
        print(f"\n{s['city']} {FOCUS_ZONE}")
        for c in ("원지", "기개발"):
            st = z.get(c)
            if not st:
                continue
            tag = f"원지(전답임야) {MIN_PYEONG_WONJI}평↑" if c == "원지" else "기개발(공장창고대)"
            print(f"  {tag:<22} {st['count']:>4}건  "
                  f"중앙 {st['median']:>6,.0f}만 · 상위25% {st['q75']:>6,.0f}만 · 상위10% {st['q90']:>6,.0f}만")
        if z.get("원지") and z.get("기개발"):
            r = z["기개발"]["median"] / z["원지"]["median"]
            print(f"  → 전용 전 {z['원지']['median']:,.0f}만 → 전용 후 {z['기개발']['median']:,.0f}만 ({r:.1f}배)")
    print("=" * 56)


def build_message(summary, months):
    period = f"{months[0][2:4]}.{months[0][4:]}~{months[-1][2:4]}.{months[-1][4:]}"
    total = sum(s["count"] for s in summary)

    lines = [
        "[OFFISCAN] 경기 토지 실거래 브리핑",
        f"{period} · {FOCUS_ZONE} · 유효 {total}건",
        "(중앙 / 상위25%, 만원/평)",
        "",
    ]

    focus = [s for s in summary if s["city"] in FOCUS_CITIES]
    focus.sort(key=lambda s: FOCUS_CITIES.index(s["city"]))
    for s in focus:
        z = s["zones"].get(FOCUS_ZONE)
        if not z:
            continue
        lines.append(f"◆ {s['city']}")
        for c in ("원지", "기개발"):
            st = z.get(c)
            if st:
                lines.append(f" {c} {st['count']}건 {st['median']:,.0f}/{st['q75']:,.0f}")

    hot = []
    for s in summary:
        st = s["zones"].get(FOCUS_ZONE, {}).get("기개발")
        if st:
            hot.append((s["city"], st))
    hot.sort(key=lambda x: x[1]["median"], reverse=True)
    if hot:
        lines += ["", "◆ 기개발(공장·창고) 상위"]
        for city, st in hot[:5]:
            lines.append(f" {city} {st['median']:,.0f}/{st['q75']:,.0f} ({st['count']})")

    lines += ["", "상세는 CSV 확인"]
    return "\n".join(lines)


def send_kakao(text):
    template = {
        "object_type": "text",
        "text": text[:990],
        "link": {
            "web_url": "https://blog.naver.com/dadaissue",
            "mobile_web_url": "https://blog.naver.com/dadaissue",
        },
    }
    r = requests.post(
        KAKAO_API,
        headers={"Authorization": f"Bearer {KAKAO_ACCESS_TOKEN}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    print("카카오:", r.status_code, r.text[:200])
    return r.status_code == 200


# ===================== 실행 =====================

if __name__ == "__main__":
    load_keys()
    by_city, city_region, months = collect()
    summary = summarize(by_city, city_region)

    if not summary:
        print("\n조건에 맞는 거래가 없습니다. 필터를 완화해 보세요.")
        raise SystemExit

    save_csv(by_city, summary)
    print_console(summary)

    msg = build_message(summary, months)
    print("\n" + "-" * 44)
    print(msg)
    print(f"-- {len(msg)}자 --\n")

    send_kakao(msg)
