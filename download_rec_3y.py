# -*- coding: utf-8 -*-
"""공공데이터포털에서 과거 3년치 REC 육지평균가를 일괄 수집하여 data/rec_history.csv로 저장합니다."""
import os, time, csv, json, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DATA_KEY = urllib.parse.unquote(os.environ.get("DATA_GO_KR_KEY", ""))
URL = "https://apis.data.go.kr/B552115/RecMarketInfo2/getRecMarketInfo2"
UA = {"User-Agent": "Mozilla/5.0"}

def fetch_rec_3y():
    if not DATA_KEY:
        print("ERROR: DATA_GO_KR_KEY 환경변수가 없습니다.")
        return

    today = datetime.now(KST).date()
    start_date = today - timedelta(days=365 * 3 + 7)  # 3년 전

    days = []
    cur = start_date
    while cur <= today:
        if cur.weekday() in (1, 3):
            days.append(cur)
        cur += timedelta(days=1)

    print(f">> 총 {len(days)}개 거래일(화/목) 조회를 시작합니다...\n")

    results = []
    for idx, d in enumerate(days, 1):
        d_str = d.strftime("%Y%m%d")
        iso_str = d.isoformat()
        q = {
            "serviceKey": DATA_KEY,
            "pageNo": 1,
            "numOfRows": 5,
            "dataType": "json",
            "bzDd": d_str
        }

        val = None
        for attempt in range(5):
            try:
                req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), headers=UA)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                body = data.get("response", data).get("body", {})
                items = body.get("items", {}).get("item", [])
                if isinstance(items, dict):
                    items = [items]

                it = next((x for x in items if x.get("landAvgPrc") not in (None, "")), None)
                if it:
                    val = float(str(it["landAvgPrc"]).replace(",", ""))
                break
            except urllib.error.HTTPError as ex:
                if ex.code == 429:
                    print(f"  [429 대기] 요청 과다 감지, 10초 대기 후 재시도...")
                    time.sleep(10)
                    continue
                break
            except Exception:
                time.sleep(2)
                continue

        if val is not None:
            results.append((iso_str, val))
            print(f"[{idx}/{len(days)}] {iso_str}: {val:,.1f}원")
        else:
            print(f"[{idx}/{len(days)}] {iso_str}: 휴장일 또는 데이터 없음")

        # 429 차단 방지 간격
        time.sleep(1.2)

    os.makedirs("data", exist_ok=True)
    out_path = os.path.join("data", "rec_history.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["날짜", "육지평균가"])
        for row in sorted(results):
            writer.writerow(row)

    print(f"\n>> 완료! 총 {len(results)}건이 {out_path}에 저장되었습니다.")

if __name__ == "__main__":
    fetch_rec_3y()
