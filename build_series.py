# -*- coding: utf-8 -*-
"""주요 시장지표 장기 시계열(series/지표.json) 구축·갱신 — 화면에서 지표를 누르면 뜨는 3년 추이 그래프용.

  python build_series.py --backfill      # 최근 3년치를 출처별로 새로 받아 만든다(최초 1회·복구용, GitHub Actions에서 실행)
  python build_series.py                 # 매일: indicators.json·KPX SMP 최근값을 이어 붙인다(collect_indicators.py 끝에서 호출)

series/지표.json 형식: {"key","name","unit","source","updated","data":[["YYYY-MM-DD", 값(, 종목)], ...]} (날짜 오름차순)
series/_status.json : 마지막 구축 결과(출처별 건수·기간·오류). 실행 로그를 볼 수 없을 때 점검용.
"""
import os, re, io, csv, sys, json, time, urllib.request, urllib.parse, urllib.error, http.cookiejar
from datetime import datetime, timedelta, timezone, date

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
SDIR = os.path.join(HERE, "series")
YEARS = 3
DATA_KEY = urllib.parse.unquote(os.environ.get("DATA_GO_KR_KEY", ""))
UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ko"}
KPX_FULL = "https://raw.githubusercontent.com/shycrt-lgtm/KPX-Daily-Report/main/data.json"
KPX_RECENT = "https://raw.githubusercontent.com/shycrt-lgtm/KPX-Daily-Report/main/data_recent.json"

META = {
    "fx":      ("원/달러 환율", "원", "ECB 기준환율(과거) · 한국수출입은행 매매기준율(2026.10~ 수집분)"),
    "fx_jpy":  ("원/100엔 환율", "원", "ECB 기준환율(과거) · 한국수출입은행 매매기준율(2026.10~ 수집분)"),
    "dubai":   ("두바이유", "$/bbl", "한국석유공사 오피넷"),
    "brent":   ("브렌트유", "$/bbl", "한국석유공사 오피넷"),
    "wti":     ("WTI", "$/bbl", "한국석유공사 오피넷"),
    "jkm":     ("JKM LNG", "USD/MMBtu", "OilPriceAPI (2026.10~ 사이트 수집분만 보유)"),
    "kau":     ("배출권(KAU)", "원/톤", "금융위원회 배출권시세(공공데이터포털) · 한국거래소. 날짜별 거래량 최대 종목"),
    "rec":     ("REC", "원/REC", "전력거래소 REC 현물시장(공공데이터포털 육지 평균가) · 전력거래소 홈페이지(수집분)"),
    "smp_avg": ("SMP 가중평균", "원/kWh", "전력거래소(육지)"),
    "smp_max": ("SMP 최고", "원/kWh", "전력거래소(육지)"),
    "smp_min": ("SMP 최저", "원/kWh", "전력거래소(육지)"),
}
STATUS = {}
BUDGET = 540          # 초. 출처 하나에 쓰는 최대 시간(넘으면 받은 데까지 저장)
DEADLINE = [None]


def over():
    return DEADLINE[0] is not None and time.time() > DEADLINE[0]


def now():
    return datetime.now(KST)


def start_day():
    return (now() - timedelta(days=365 * YEARS + 7)).date()


def retry(fn, tries=2, wait=3):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(wait * (i + 1))
    raise last


def get(url, headers=None, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers or UA), timeout=timeout).read()


# ---------------------------------------------------------------- 파일 입출력
def path(key):
    return os.path.join(SDIR, key + ".json")


def load(key):
    try:
        with open(path(key), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save(key, rows, merge=True):
    """rows: [(YYYY-MM-DD, 값[, 종목])]. merge=True 면 기존 값과 날짜 기준으로 합친다(새 값 우선). 3년보다 오래된 값은 버린다."""
    os.makedirs(SDIR, exist_ok=True)
    name, unit, src = META[key]
    by = {}
    old = load(key) if merge else None
    for r in (old or {}).get("data", []):
        by[r[0]] = list(r)
    for r in rows:
        if r[1] is None:
            continue
        v = round(float(r[1]), 4)
        by[r[0]] = [r[0], v] + ([r[2]] if len(r) > 2 and r[2] else [])
    cut = start_day().isoformat()
    data = [by[d] for d in sorted(by) if d >= cut]
    out = {"key": key, "name": name, "unit": unit, "source": src, "updated": now().strftime("%Y-%m-%d %H:%M"), "data": data}
    with open(path(key), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    return len(data)


def note(key, rows=None, err=None):
    s = STATUS.setdefault(key, {})
    if rows is not None:
        ds = sorted(r[0] for r in rows)
        s.update({"rows": len(rows), "from": ds[0] if ds else None, "to": ds[-1] if ds else None})
    if err:
        s.setdefault("errors", []).append(str(err)[:300])
    print(f">> [{key}] {s}")


# ---------------------------------------------------------------- 출처별 과거자료
def smp_rows(days):
    out = {"smp_avg": [], "smp_max": [], "smp_min": []}
    for k, d in days.items():
        if not re.fullmatch(r"\d{8}", k):
            continue
        day = f"{k[:4]}-{k[4:6]}-{k[6:]}"
        for key, f in (("smp_avg", "smp_avg"), ("smp_max", "smp_max"), ("smp_min", "smp_min")):
            if isinstance(d, dict) and d.get(f) is not None:
                out[key].append((day, d[f]))
    return out


def back_smp():
    days = json.loads(retry(lambda: get(KPX_FULL, timeout=120)).decode("utf-8")).get("days", {})
    try:
        days.update(json.loads(get(KPX_RECENT).decode("utf-8")).get("days", {}))
    except Exception as e:
        note("smp_avg", err=f"data_recent 실패 {e}")
    for key, rows in smp_rows(days).items():
        save(key, rows, merge=False)
        note(key, rows)


def back_fx():
    """Frankfurter(ECB). 1년 단위로 나눠 받는다. KRW per USD, KRW per 100 JPY."""
    s, e = start_day(), now().date()
    usd, jpy = [], []
    cur = s
    while cur <= e and not over():
        nxt = min(cur + timedelta(days=364), e)
        q = urllib.parse.urlencode({"base": "USD", "symbols": "KRW,JPY"})
        j, last = None, None
        for base in ("https://api.frankfurter.dev/v1/", "https://api.frankfurter.app/"):
            try:
                j = json.loads(retry(lambda: get(f"{base}{cur.isoformat()}..{nxt.isoformat()}?{q}")).decode("utf-8"))
                break
            except Exception as ex:
                last = ex
        if j is None:
            note("fx", err=f"{cur}~{nxt} 실패 {last}")
        else:
            for d, v in (j.get("rates") or {}).items():
                if v.get("KRW"):
                    usd.append((d, float(v["KRW"])))
                    if v.get("JPY"):
                        jpy.append((d, float(v["KRW"]) / float(v["JPY"]) * 100))
        cur = nxt + timedelta(days=1)
    # 사이트가 매일 모은 수출입은행 매매기준율(history/ind_*.json)이 있으면 그 날짜는 그 값으로
    usd += collected("fx"); jpy += collected("fx_jpy")
    save("fx", usd, merge=False); note("fx", usd)
    save("fx_jpy", jpy, merge=False); note("fx_jpy", jpy)


def opinet_rows(text):
    rows = []
    for line in csv.reader(io.StringIO(text)):
        if len(line) < 4:
            continue
        d = line[0].strip()
        m = re.fullmatch(r"(\d{2,4})\D+(\d{1,2})\D+(\d{1,2})\D*", d)
        if m:
            y = m.group(1)
            y = y if len(y) == 4 else "20" + y
            d = f"{y}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        elif re.fullmatch(r"\d{6}", d):
            d = "20" + d
        if not re.fullmatch(r"\d{8}", d):
            continue
        try:
            vals = [float(x.replace(",", "")) for x in line[1:4]]
        except ValueError:
            continue
        rows.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", *vals))
    return rows


def back_crude():
    base = "https://www.opinet.co.kr"
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    H = {**UA, "Referer": base + "/glopcoilSelect.do"}
    op.open(urllib.request.Request(base + "/glopcoilSelect.do", headers=H), timeout=60).read()

    def post(s, e):
        data = [("TERM", "D"), ("OILSRTCD1", "001"), ("OILSRTCD2", "002"), ("OILSRTCD3", "003"), ("OILSRTCD", "001"),
                ("STDDATE", s), ("ENDDATE", e), ("SEL_DIV", "div_dar"),
                ("STA_Y", s[:4]), ("STA_M", s[4:6]), ("STA_D", s[6:]), ("END_Y", e[:4]), ("END_M", e[4:6]), ("END_D", e[6:])]
        raw = op.open(urllib.request.Request(base + "/glopcoil_csv.do", data=urllib.parse.urlencode(data).encode(), headers=H), timeout=90).read()
        for enc in ("utf-8", "cp949"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                pass
        return raw.decode("utf-8", "ignore")

    rows, cur, e = [], start_day(), now().date()
    while cur <= e and not over():
        nxt = min(cur + timedelta(days=180), e)
        try:
            txt = retry(lambda: post(cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")))
            r = opinet_rows(txt)
            if not r:
                note("dubai", err=f"{cur}~{nxt} 행 없음, 앞부분 {txt[:150]!r}")
            rows += r
        except Exception as ex:
            note("dubai", err=f"{cur}~{nxt} 실패 {type(ex).__name__} {ex}")
        cur = nxt + timedelta(days=1)
        time.sleep(1)
    rows = [r for r in rows if all(5 <= v <= 400 for v in r[1:])]
    for i, key in enumerate(("dubai", "brent", "wti"), start=1):
        rr = [(r[0], r[i]) for r in rows]
        save(key, rr, merge=False); note(key, rr)


def datago_items(url, q):
    j = json.loads(retry(lambda: get(url + "?" + urllib.parse.urlencode(q), timeout=40)).decode("utf-8"))
    root = j.get("response", j)
    body = root.get("body", {}) if isinstance(root, dict) else {}
    items = body.get("items", {}) if isinstance(body, dict) else {}
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    total = int(body.get("totalCount") or 0) if isinstance(body, dict) else 0
    return items or [], total, root.get("header", {}) if isinstance(root, dict) else {}


def back_kau():
    """배출권: 날짜별로 거래량이 가장 큰 KAU 종목의 종가(거래 없으면 직전 대표 종목의 종가)."""
    if not DATA_KEY:
        note("kau", err="DATA_GO_KR_KEY 없음"); return
    url = "https://apis.data.go.kr/1160100/service/GetGeneralProductInfoService_V2/getCertifiedEmissionReductionPriceInfo_V2"
    alt = "https://apis.data.go.kr/1160100/GetGeneralProductInfoService_V2/getCertifiedEmissionReductionPriceInfo_V2"
    allrows, page = [], 1
    while page < 60 and not over():
        q = {"serviceKey": DATA_KEY, "pageNo": page, "numOfRows": 1000, "resultType": "json",
             "beginBasDt": start_day().strftime("%Y%m%d"), "likeItmsNm": "KAU"}
        try:
            items, total, head = datago_items(url, q)
        except Exception as e:
            items, total, head = datago_items(alt, q)
        for x in items:
            try:
                allrows.append((str(x["basDt"]), str(x.get("itmsNm", "")), float(x["clpr"]), float(x.get("trqu") or 0)))
            except (KeyError, ValueError, TypeError):
                pass
        if page == 1:
            STATUS.setdefault("kau", {})["total"] = total
        if not items or page * 1000 >= total:
            break
        page += 1
    by = {}
    for d, nm, px, vol in allrows:
        if px > 0 and re.fullmatch(r"KAU\d{2}", nm.strip()):
            by.setdefault(d, []).append((vol, nm.strip(), px))
    rows, rep = [], None
    for d in sorted(by):
        c = sorted(by[d], reverse=True)
        pick = c[0] if c[0][0] > 0 or rep is None else next((x for x in c if x[1] == rep), c[0])
        if pick[0] > 0:
            rep = pick[1]
        rows.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", pick[2], pick[1]))
    rows += collected("kau")
    save("kau", rows, merge=False); note("kau", rows)


def back_rec(limit=None, stop_on_429=False):
    """REC 현물시장: 화·목(거래일)마다 날짜를 지정해 조회. 육지 평균가(landAvgPrc).
    공공데이터포털이 동시·연속 호출을 '요청 과다(429)'로 막으므로 1건씩 간격을 두고, 이미 받은 날짜는 건너뛴다(여러 번 나눠 실행 가능)."""
    if not DATA_KEY:
        note("rec", err="DATA_GO_KR_KEY 없음"); return
    url = "https://apis.data.go.kr/B552115/RecMarketInfo2/getRecMarketInfo2"
    have = {r[0] for r in (load("rec") or {}).get("data", [])}
    days, d, e = [], start_day(), now().date()
    while d <= e:
        if d.weekday() in (1, 3) and d.isoformat() not in have:
            days.append(d)
        d += timedelta(days=1)
    rows, sample, fails, empty, done = [], None, 0, 0, 0
    stopped = False
    for d in sorted(days, reverse=True)[:limit]:  # 최근부터
        if stopped:
            break
        if over():
            note("rec", err="시간 초과 - 받은 데까지 저장(다시 실행하면 이어서 받음)"); break
        q = {"serviceKey": DATA_KEY, "pageNo": 1, "numOfRows": 5, "dataType": "json", "bzDd": d.strftime("%Y%m%d")}
        for attempt in range(4):
            try:
                items, _, _ = datago_items(url, q)
                it = next((x for x in items if x.get("landAvgPrc") not in (None, "")), None)
                if it:
                    sample = sample or it
                    rows.append((d.isoformat(), float(str(it["landAvgPrc"]).replace(",", ""))))
                else:
                    empty += 1
                break
            except urllib.error.HTTPError as ex:
                if ex.code == 429:
                    if stop_on_429:
                        note("rec", err=f"{d} 요청 과다(429) - 오늘은 중단, 다음 실행에서 이어 받음"); stopped = True; break
                    time.sleep(5 * (attempt + 1)); continue
                fails += 1; break
            except Exception as ex:
                fails += 1
                if fails <= 3:
                    note("rec", err=f"{d} {type(ex).__name__} {ex}")
                break
        done += 1
        time.sleep(0.7)
    st = STATUS.setdefault("rec", {})
    st.update({"sample_fields": sorted(sample.keys()) if sample else st.get("sample_fields"), "fails": fails, "empty": empty,
               "asked": done, "left": len(days) - done})
    rows += collected("rec")
    save("rec", rows, merge=True); note("rec", rows)


def collected(key):
    """사이트가 매일 모아 둔 history/ind_*.json 에서 해당 지표의 (날짜, 값) — 같은 날짜는 마지막 파일 값."""
    by = {}
    for fn in sorted(os.listdir(os.path.join(HERE, "history"))) if os.path.isdir(os.path.join(HERE, "history")) else []:
        if not re.fullmatch(r"ind_\d{8}\.json", fn):
            continue
        try:
            it = json.load(open(os.path.join(HERE, "history", fn), encoding="utf-8")).get("items", {}).get(key)
        except Exception:
            continue
        if it and it.get("date") and it.get("value") is not None:
            by[it["date"]] = (it["date"], it["value"]) + ((it["item"],) if key == "kau" and it.get("item") else ())
            if it.get("prev_date") and it.get("prev_value") is not None and it["prev_date"] not in by:
                by[it["prev_date"]] = (it["prev_date"], it["prev_value"])
    return list(by.values())


# ---------------------------------------------------------------- 매일 이어 붙이기
def daily():
    """indicators.json 의 현재값(전일값 포함)과 KPX 최근 SMP 를 series 에 더한다. 파일이 없으면 건너뜀(구축 전)."""
    try:
        items = json.load(open(os.path.join(HERE, "indicators.json"), encoding="utf-8")).get("items", {})
    except Exception:
        items = {}
    for key in ("fx", "fx_jpy", "dubai", "brent", "wti", "jkm", "kau", "rec"):
        it = items.get(key)
        if not it or it.get("value") is None or not it.get("date"):
            continue
        if not os.path.exists(path(key)) and key != "jkm":
            continue
        rows = [(it["date"], it["value"]) + ((it["item"],) if key == "kau" and it.get("item") else ())]
        if it.get("prev_date") and it.get("prev_value") is not None:
            old = {r[0] for r in (load(key) or {}).get("data", [])}
            if it["prev_date"] not in old:
                rows.append((it["prev_date"], it["prev_value"]))
        save(key, rows)
    try:
        days = json.loads(get(KPX_RECENT).decode("utf-8")).get("days", {})
        for key, rows in smp_rows(days).items():
            if os.path.exists(path(key)):
                save(key, rows)
    except Exception as e:
        print(f">> [series SMP] 최근값 실패 {type(e).__name__}: {e}")
    # REC 과거자료는 공공데이터포털 호출 한도 때문에 매일 조금씩(최대 30거래일) 채운다
    if os.path.exists(path("rec")) and DATA_KEY:
        try:
            DEADLINE[0] = time.time() + 150
            back_rec(limit=30, stop_on_429=True)
            write_status()
        except Exception as e:
            print(f">> [series REC 채우기] 실패 {type(e).__name__}: {e}")
    print(">> series 갱신 완료")


def write_status():
    os.makedirs(SDIR, exist_ok=True)
    p = os.path.join(SDIR, "_status.json")
    try:
        old = json.load(open(p, encoding="utf-8")).get("status", {})
    except Exception:
        old = {}
    old.update(STATUS)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"built": now().strftime("%Y-%m-%d %H:%M"), "status": old}, f, ensure_ascii=False, indent=1)


def back_jkm():
    rows = collected("jkm")          # 무료 과거자료가 없어 사이트 수집분만
    save("jkm", rows, merge=True); note("jkm", rows)


SOURCES = {"smp": back_smp, "fx": back_fx, "crude": back_crude, "kau": back_kau, "rec": back_rec, "jkm": back_jkm}


def backfill(only=None):
    for name in (only or list(SOURCES)):
        t0 = time.time()
        DEADLINE[0] = t0 + (1320 if name == "rec" else BUDGET)
        STATUS.setdefault(name, {})
        try:
            SOURCES[name]()
        except Exception as e:
            note(name, err=f"{type(e).__name__}: {e}")
        STATUS[name]["sec"] = int(time.time() - t0)
        write_status()        # 출처마다 바로 기록(중간에 끊겨도 앞의 결과는 남게)


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        only = [a for a in sys.argv[1:] if not a.startswith("--")]
        backfill(only or None)
    else:
        daily()
