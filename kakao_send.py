"""
카카오톡 '나에게 보내기' 전송 모듈 (액세스 토큰 자동 갱신)

다른 스크립트에서:
    from kakao_send import send, send_long
    send("짧은 내용")            990자까지. 넘으면 잘린다.
    send_long("긴 리포트")       990자마다 (1/3) 처럼 나눠서 순서대로 보낸다

단독 테스트:
    py kakao_send.py
"""

import os
import json
import requests

KEYS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.txt")
SEND_API = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
TOKEN_API = "https://kauth.kakao.com/oauth/token"
LINK = "https://blog.naver.com/dadaissue"


def read_keys():
    d = {}
    if os.path.exists(KEYS):
        with open(KEYS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def write_keys(d):
    with open(KEYS, "w", encoding="utf-8") as f:
        for k, v in d.items():
            f.write(f"{k}={v}\n")


def refresh_access_token():
    """리프레시 토큰으로 액세스 토큰을 새로 받는다. 실패하면 None."""
    keys = read_keys()
    rest = keys.get("KAKAO_REST_KEY", "")
    refresh = keys.get("KAKAO_REFRESH_TOKEN", "")
    if not rest or not refresh:
        print("[카카오] KAKAO_REST_KEY / KAKAO_REFRESH_TOKEN 이 없습니다.")
        print("         py kakao_setup.py 를 먼저 한 번 실행하세요.")
        return None

    payload = {"grant_type": "refresh_token",
               "client_id": rest, "refresh_token": refresh}
    secret = keys.get("KAKAO_CLIENT_SECRET", "")
    if secret:
        payload["client_secret"] = secret
    r = requests.post(TOKEN_API, data=payload, timeout=15)
    js = r.json()
    if "access_token" not in js:
        print(f"[카카오] 토큰 갱신 실패: {js}")
        print("         리프레시 토큰이 만료됐을 수 있습니다. py kakao_setup.py 재실행.")
        return None

    keys["KAKAO_ACCESS_TOKEN"] = js["access_token"]
    # 리프레시 토큰도 갱신되어 내려오면 함께 저장 (만료 1개월 미만일 때만 내려옴)
    if "refresh_token" in js:
        keys["KAKAO_REFRESH_TOKEN"] = js["refresh_token"]
        print("[카카오] 리프레시 토큰도 갱신되었습니다.")
    write_keys(keys)
    return js["access_token"]


def _post(token, text, link=None):
    url = link or LINK
    template = {"object_type": "text", "text": text[:990],
                "link": {"web_url": url, "mobile_web_url": url}}
    return requests.post(SEND_API,
                         headers={"Authorization": f"Bearer {token}"},
                         data={"template_object": json.dumps(template, ensure_ascii=False)},
                         timeout=15)


CHUNK = 900          # 머리표 '(1/3)' 자리를 남긴 실제 본문 상한


def split_text(text, chunk=CHUNK):
    """줄 단위로 자른다. 한 줄이 통째로 상한을 넘으면 그 줄만 강제로 쪼갠다."""
    parts, buf = [], ""
    for line in text.split("\n"):
        while len(line) > chunk:
            if buf:
                parts.append(buf.rstrip("\n"))
                buf = ""
            parts.append(line[:chunk])
            line = line[chunk:]
        if len(buf) + len(line) + 1 > chunk:
            parts.append(buf.rstrip("\n"))
            buf = ""
        buf += line + "\n"
    if buf.strip():
        parts.append(buf.rstrip("\n"))
    return parts or [""]


def send_long(text, link=None, delay=1.0):
    """긴 글을 여러 통으로 나눠 보낸다. 한 통이라도 실패하면 False."""
    import time
    parts = split_text(text)
    if len(parts) == 1:
        return send(parts[0], link)

    print(f"[카카오] {len(text)}자 -> {len(parts)}통으로 분할")
    ok = True
    for i, part in enumerate(parts, 1):
        head = f"({i}/{len(parts)})\n"
        # 링크 미리보기는 첫 통에만. 매 통에 붙으면 카드가 줄줄이 생긴다.
        if not send(head + part, link if i == 1 else None):
            ok = False
        if i < len(parts):
            time.sleep(delay)
    return ok


def send(text, link=None):
    """저장된 액세스 토큰으로 보내고, 만료면 자동 갱신 후 1회 재시도.
    link 를 주면 '자세히 보기'가 그 주소로 열린다."""
    token = read_keys().get("KAKAO_ACCESS_TOKEN", "")

    if token:
        r = _post(token, text, link)
        if r.status_code == 200:
            print("[카카오] 전송 완료")
            return True
        print(f"[카카오] 만료 추정({r.status_code}) - 토큰 갱신 시도")

    token = refresh_access_token()
    if not token:
        return False

    r = _post(token, text, link)
    if r.status_code == 200:
        print("[카카오] 갱신 후 전송 완료")
        return True
    print(f"[카카오] 전송 실패 {r.status_code}: {r.text[:200]}")
    return False


if __name__ == "__main__":
    from datetime import datetime
    ok = send(f"[OFFISCAN] 토큰 자동갱신 테스트\n{datetime.now():%Y-%m-%d %H:%M}\n"
              f"이 메시지가 오면 세팅 완료입니다.")
    print("결과:", "성공" if ok else "실패")
