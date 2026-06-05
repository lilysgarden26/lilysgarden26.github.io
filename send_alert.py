#!/usr/bin/env python3
"""
급식봇 - GitHub Actions 알림 발송 스크립트
매일 지정 시간에 NEIS API로 급식 조회 후 카카오 나에게 보내기로 발송
"""

import os
import json
import requests
from datetime import datetime, timedelta, timezone

# ─── 환경변수 (GitHub Secrets) ──────────────────────────
NEIS_API_KEY       = os.environ.get("NEIS_API_KEY", "")
KAKAO_ACCESS_TOKEN = os.environ.get("KAKAO_ACCESS_TOKEN", "")
KAKAO_REFRESH_TOKEN= os.environ.get("KAKAO_REFRESH_TOKEN", "")
KAKAO_REST_KEY     = os.environ.get("KAKAO_REST_KEY", "")
TEST_MODE          = os.environ.get("TEST_MODE", "false").lower() == "true"

# ─── KST 오늘 날짜 ──────────────────────────────────────
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST)
TODAY_STR = TODAY.strftime("%Y%m%d")
TODAY_DISPLAY = f"{TODAY.month}/{TODAY.day}({['월','화','수','목','금','토','일'][TODAY.weekday()]})"

SETTINGS_PATH = "data/settings.json"

# ─── 설정 로드 ───────────────────────────────────────────
def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ settings.json 없음 → 웹사이트에서 먼저 설정해주세요")
        return None

# ─── NEIS 급식 조회 ─────────────────────────────────────
def get_meal(org_code, school_code):
    if not NEIS_API_KEY:
        print("⚠️ NEIS_API_KEY 없음 → 샘플 데이터 사용")
        return ["쌀밥", "된장찌개", "돼지불고기", "깍두기", "우유"]

    url = "https://open.neis.go.kr/hub/mealServiceDietInfo"
    params = {
        "KEY": NEIS_API_KEY,
        "Type": "json",
        "ATPT_OFCDC_SC_CODE": org_code,
        "SD_SCHUL_CODE": school_code,
        "MLSV_YMD": TODAY_STR,
    }
    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        rows = data.get("mealServiceDietInfo", [{}]*2)[1].get("row", [])
        # 중식 우선, 없으면 첫 번째
        meal = next((r for r in rows if r.get("MMEAL_SC_NM") == "중식"), rows[0] if rows else None)

        if not meal:
            return None

        # 알레르기 번호 제거
        items = []
        for item in meal["DDISH_NM"].split("<br/>"):
            clean = ""
            skip = False
            for ch in item:
                if ch == "(":
                    skip = True
                elif ch == ")":
                    skip = False
                elif not skip:
                    clean += ch
            clean = clean.strip()
            if clean:
                items.append(clean)
        return items

    except Exception as e:
        print(f"❌ NEIS API 오류: {e}")
        return None

# ─── D-day 계산 ──────────────────────────────────────────
def calc_dday(date_str):
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=KST)
        today  = TODAY.replace(hour=0, minute=0, second=0, microsecond=0)
        target = target.replace(hour=0, minute=0, second=0, microsecond=0)
        return (target - today).days
    except:
        return None

# ─── 카카오 토큰 갱신 ────────────────────────────────────
def refresh_kakao_token():
    if not KAKAO_REFRESH_TOKEN or not KAKAO_REST_KEY:
        return None
    try:
        res = requests.post(
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": KAKAO_REST_KEY,
                "refresh_token": KAKAO_REFRESH_TOKEN,
            },
            timeout=10,
        )
        data = res.json()
        new_token = data.get("access_token")
        if new_token:
            # 로그 저장 (GitHub Secrets는 Actions에서 직접 수정 불가 → 수동 갱신 안내)
            with open("scripts/token_log.txt", "w") as f:
                f.write(f"갱신된 access_token: {new_token}\n")
                f.write(f"갱신 시각: {TODAY.isoformat()}\n")
                if data.get("refresh_token"):
                    f.write(f"새 refresh_token: {data['refresh_token']}\n")
            print("✅ 카카오 토큰 갱신 성공")
        return new_token
    except Exception as e:
        print(f"❌ 토큰 갱신 실패: {e}")
        return None

# ─── 카카오 나에게 보내기 ────────────────────────────────
def send_kakao(text, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    template = json.dumps({
        "object_type": "text",
        "text": text,
        "link": {"web_url": "", "mobile_web_url": ""},
    })
    try:
        res = requests.post(
            "https://kapi.kakao.com/v2/api/talk/memo/default/send",
            headers=headers,
            data={"template_object": template},
            timeout=10,
        )
        result = res.json()
        if result.get("result_code") == 0:
            print("✅ 카카오 발송 성공!")
            return True
        else:
            print(f"❌ 카카오 발송 실패: {result}")
            # 토큰 만료 시 갱신 시도
            if result.get("code") == -401:
                print("🔄 토큰 만료 → 갱신 시도...")
                new_token = refresh_kakao_token()
                if new_token:
                    return send_kakao(text, new_token)
            return False
    except Exception as e:
        print(f"❌ 요청 오류: {e}")
        return False

# ─── 메시지 생성 ─────────────────────────────────────────
def build_message(school_name, meals, ddays):
    lines = []
    lines.append(f"🍱 {school_name} {TODAY_DISPLAY} 중식")
    lines.append("─────────────────")

    if meals:
        lines.extend(meals)
    else:
        lines.append("오늘은 급식 정보가 없습니다")

    # 디데이
    upcoming = []
    for d in (ddays or []):
        diff = calc_dday(d.get("date", ""))
        if diff is not None and diff >= 0:
            upcoming.append((diff, d["name"]))
    upcoming.sort()

    if upcoming:
        lines.append("─────────────────")
        for diff, name in upcoming[:5]:
            label = "D-Day! 🎉" if diff == 0 else f"D-{diff}"
            lines.append(f"📌 {name}  {label}")

    return "\n".join(lines)

# ─── 메인 ────────────────────────────────────────────────
def main():
    print(f"{'='*40}")
    print(f"🍱 급식봇 실행 | {TODAY.strftime('%Y-%m-%d %H:%M')} KST")
    print(f"{'='*40}")

    # 설정 로드
    settings = load_settings()
    if not settings:
        return

    school = settings.get("school")
    ddays  = settings.get("ddays", [])

    if not school:
        print("❌ 학교 정보 없음 → 웹사이트에서 설정해주세요")
        return

    print(f"🏫 학교: {school['name']}")
    print(f"📅 디데이: {len(ddays)}개")
    print(f"🔑 토큰: {'있음' if KAKAO_ACCESS_TOKEN else '없음'}")

    # 급식 조회
    meals = get_meal(school.get("orgCode"), school.get("schoolCode"))
    print(f"🥗 급식: {meals}")

    # 메시지 생성
    message = build_message(school["name"], meals, ddays)
    print(f"\n{'─'*30}\n{message}\n{'─'*30}")

    if TEST_MODE:
        print("ℹ️ 테스트 모드: 실제 발송 건너뜀")
        return

    # 카카오 발송
    if not KAKAO_ACCESS_TOKEN:
        print("❌ KAKAO_ACCESS_TOKEN 없음 → GitHub Secrets 설정 필요")
        return

    send_kakao(message, KAKAO_ACCESS_TOKEN)

if __name__ == "__main__":
    main()
