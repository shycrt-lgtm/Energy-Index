# -*- coding: utf-8 -*-
"""주요 시장지표 수집 → indicators.json
- JKM LNG : OilPriceAPI (영업일별 값, USD/MMBtu)  ← Secret OILPRICE_API_KEY
- 두바이/브렌트/WTI : 한국석유공사 오피넷 국제원유가격 (일별, $/Bbl)
수집에 실패한 항목은 이전 값을 그대로 둔다."""
import os, re, io, csv, json, time, urllib.request, urllib.parse, urllib.error, http.cookiejar
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUT = "indicators.json"
OIL_KEY = os.environ.get("OILPRICE_API_KEY", "")
UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ko"}


def retry(fn, tries=3):
    last = None
    for i in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f">> [재시도 {i}/{tries}] {type(e).__name__}: {str(e)[:120]}")
            time.sleep(5 * i)
    raise last


def fetch_jkm():
    def call():
        req = urllib.request.Request("https://api.oilpriceapi.com/v1/prices/past_week?by_code=JKM_LNG_USD",
                                     headers={**UA, "Authorization": "Token " + OIL_KEY})
        return json.loads(urllib.request.urlopen(req, timeout=40).read().decode("utf-8"))
    j = retry(call)
    d = j.get("data")
    if isinstance(d, dict) and isinstance(d.get("prices"), list):
        d = d["prices"]
    if isinstance(d, dict):
        d = [d]
    by_day = {}
    for x in d or []:
        ts = str(x.get("as_of") or x.get("created_at") or "")
        try:
            p = float(x["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(ts) >= 10 and (ts[:10] not in by_day or ts > by_day[ts[:10]][0]):
            by_day[ts[:10]] = (ts, p)
    days = sorted(by_day, reverse=True)
    if not days:
        raise ValueError("JKM 자료 없음")
    cur = by_day[days[0]][1]
    prev = by_day[days[1]][1] if len(days) > 1 else None
    return {"jkm": {"name": "JKM LNG", "unit": "USD/MMBtu", "date": days[0], "value": cur,
                    "prev_date": days[1] if len(days) > 1 else None, "prev_value": prev}}


def opinet_csv(text):
    rows = []
    for line in csv.reader(io.StringIO(text)):
        if len(line) >= 4 and re.fullmatch(r"\d{6}", line[0].strip()):
            try:
                rows.append((line[0].strip(), float(line[1]), float(line[2]), float(line[3])))
            except ValueError:
                pass
    return sorted(rows)


def fetch_opinet():
    base = "https://www.opinet.co.kr"
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    H = {**UA, "Referer": base + "/glopcoilSelect.do"}
    op.open(urllib.request.Request(base + "/glopcoilSelect.do", headers=H), timeout=40).read()
    now = datetime.now(KST).date()
    s, e = (now - timedelta(days=14)).strftime("%Y%m%d"), now.strftime("%Y%m%d")

    def post(sel, multi):
        data = [("TERM", "D"), ("OILSRTCD1", "001"), ("OILSRTCD2", "002"), ("OILSRTCD3", "003")]
        data += [("OILSRTCD", c) for c in (("001", "002", "003") if multi else ("001",))]
        data += [("STDDATE", s), ("ENDDATE", e), ("SEL_DIV", sel),
                 ("STA_Y", s[:4]), ("STA_M", s[4:6]), ("STA_D", s[6:]),
                 ("END_Y", e[:4]), ("END_M", e[4:6]), ("END_D", e[6:])]
        raw = op.open(urllib.request.Request(base + "/glopcoil_csv.do", data=urllib.parse.urlencode(data).encode(),
                                             headers=H), timeout=40).read()
        for enc in ("utf-8", "cp949"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                pass
        return raw.decode("utf-8", "ignore")

    rows = []
    for sel, multi in (("div_dar", False), ("div_dar", True), ("D", False)):
        try:
            txt = retry(lambda: post(sel, multi), tries=2)
        except Exception as ex:
            print(f">> [오피넷 시도 SEL_DIV={sel} 다중={multi}] 통신 실패 {type(ex).__name__}")
            continue
        r = opinet_csv(txt)
        print(f">> [오피넷 시도 SEL_DIV={sel} 다중={multi}] 응답 {len(txt)}자, 행 {len(r)}개, 앞부분: {txt[:120]!r}")
        if r and all(20 <= v <= 400 for v in r[-1][1:]):
            rows = r
            break
        if r:
            print(">> [오피넷] $/Bbl 범위를 벗어난 값 → 다른 조건으로 재시도 (마지막 행:", r[-1], ")")
    if not rows:
        raise ValueError("오피넷 자료 없음")
    cur, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)
    ymd = lambda t: "20" + t[:2] + "-" + t[2:4] + "-" + t[4:]
    out = {}
    for i, (key, name) in enumerate((("dubai", "두바이유"), ("brent", "브렌트유"), ("wti", "WTI")), start=1):
        out[key] = {"name": name, "unit": "$/bbl", "date": ymd(cur[0]), "value": cur[i],
                    "prev_date": ymd(prev[0]) if prev else None, "prev_value": prev[i] if prev else None}
    return out


def main():
    store = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    items = store.get("items", {})
    ok = 0
    for label, fn in (("JKM", fetch_jkm), ("오피넷 국제유가", fetch_opinet)):
        try:
            got = fn()
            items.update(got)
            ok += 1
            for k, v in got.items():
                print(f">> [수집 성공] {k}: {v['date']} {v['value']} (전일 {v['prev_date']} {v['prev_value']})")
        except Exception as e:
            print(f">> [수집 실패] {label}: {type(e).__name__}: {str(e)[:150]} → 이전 값 유지")
    if ok == 0:
        raise SystemExit(1)
    json.dump({"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "items": items},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(">> indicators.json 저장 완료")


if __name__ == "__main__":
    main()
