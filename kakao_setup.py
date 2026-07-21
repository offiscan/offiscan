"""
카카오 리프레시 토큰 1회 발급 도우미  (딱 한 번만 실행하면 됨)

py kakao_setup.py

끝나면 keys.txt 에 KAKAO_REST_KEY / KAKAO_REFRESH_TOKEN 이 자동으로 저장된다.
이후 land_report.py, indu_report.py 는 알아서 토큰을 갱신한다.
"""

import os
import requests

KEYS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.txt")


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


def main():
    keys = read_keys()

    print("=" * 60)
    print(" 카카오 리프레시 토큰 발급")
    print("=" * 60)

    rest = keys.get("KAKAO_REST_KEY", "")
    if rest:
        print(f"\n저장된 REST API 키를 사용합니다. ({rest[:6]}...)")
    else:
        print("\n[1단계] 카카오 디벨로퍼스 > 내 애플리케이션 > 앱 설정 > 앱 키")
        print("        'REST API 키' 를 복사해서 붙여넣으세요.")
        rest = input("REST API 키: ").strip()
        if not rest:
            print("입력이 없습니다. 종료.")
            return

    secret = keys.get("KAKAO_CLIENT_SECRET", "")
    if secret:
        print(f"저장된 클라이언트 시크릿을 사용합니다. ({secret[:4]}...)")
    else:
        print("\n[1-2단계] 클라이언트 시크릿 (있으면 입력, 없으면 그냥 엔터)")
        print("        카카오 디벨로퍼스 > 앱 설정 > 보안 > 클라이언트 시크릿 '코드'")
        print("        활성화가 ON 이면 반드시 필요합니다.")
        secret = input("클라이언트 시크릿 [없으면 엔터]: ").strip()

    print("\n[2단계] 카카오 로그인 > 일반 > Redirect URI 를 확인하세요.")
    print("        (등록 안 돼 있으면 https://example.com/oauth 로 등록)")
    redirect = input("Redirect URI [https://example.com/oauth]: ").strip()
    if not redirect:
        redirect = "https://example.com/oauth"

    auth_url = (
        "https://kauth.kakao.com/oauth/authorize"
        f"?client_id={rest}&redirect_uri={redirect}"
        "&response_type=code&scope=talk_message"
    )

    print("\n[3단계] 아래 주소를 통째로 복사해서 브라우저 주소창에 붙여넣고 엔터.")
    print("        동의 화면이 뜨면 동의하세요.\n")
    print(auth_url)
    print("\n        동의하면 빈 페이지로 넘어갑니다. 페이지 내용은 무시하고,")
    print("        브라우저 '주소창'을 보세요. 이렇게 생겼습니다:")
    print(f"        {redirect}?code=ABCD1234EFGH....")
    print("        여기서 code= 뒤의 값만 복사하세요.\n")

    code = input("code 값: ").strip()
    if "code=" in code:                       # 주소 통째로 붙여넣어도 처리
        code = code.split("code=")[1].split("&")[0]
    if not code:
        print("입력이 없습니다. 종료.")
        return

    print("\n토큰 요청 중...")
    payload = {"grant_type": "authorization_code", "client_id": rest,
               "redirect_uri": redirect, "code": code}
    if secret:
        payload["client_secret"] = secret
    r = requests.post("https://kauth.kakao.com/oauth/token", data=payload, timeout=15)
    js = r.json()

    if "refresh_token" not in js:
        print(f"\n[실패] {js}")
        print("\n흔한 원인:")
        print(" - code 는 1회용입니다. 이미 썼거나 몇 분 지났으면 3단계부터 다시.")
        print(" - Redirect URI 가 카카오에 등록된 것과 한 글자라도 다르면 실패합니다.")
        print(" - KOE010(Bad client credentials) 이면:")
        print("     · REST API 키가 다른 앱 것이거나")
        print("     · 클라이언트 시크릿이 ON 인데 값을 안 넣었거나 틀렸습니다.")
        return

    keys["KAKAO_REST_KEY"] = rest
    if secret:
        keys["KAKAO_CLIENT_SECRET"] = secret
    keys["KAKAO_REDIRECT_URI"] = redirect
    keys["KAKAO_REFRESH_TOKEN"] = js["refresh_token"]
    keys["KAKAO_ACCESS_TOKEN"] = js.get("access_token", "")
    write_keys(keys)

    print("\n성공. keys.txt 에 저장했습니다.")
    print(f"  리프레시 토큰 유효기간: 약 {js.get('refresh_token_expires_in', 0) // 86400}일")
    print("  (스크립트가 돌 때마다 자동 연장됩니다)")
    print("\n확인: py kakao_send.py")


if __name__ == "__main__":
    main()
