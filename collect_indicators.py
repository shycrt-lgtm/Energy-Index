# -*- coding: utf-8 -*-
"""주요 시장지표 수집 → indicators.json
- JKM LNG : OilPriceAPI (영업일별 값, USD/MMBtu)  ← Secret OILPRICE_API_KEY
- 두바이/브렌트/WTI : OilPriceAPI(JKM과 동일, $/bbl) — 실패 시에만 한국석유공사 오피넷으로 보충
수집에 실패한 항목은 이전 값을 그대로 둔다."""
import os, re, io, csv, json, time, urllib.request, urllib.parse, urllib.error, http.cookiejar
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OUT = "indicators.json"
OIL_KEY = os.environ.get("OILPRICE_API_KEY", "")
EXIM_KEY = os.environ.get("KOREAEXIM_KEY", "")
DATA_KEY = urllib.parse.unquote(os.environ.get("DATA_GO_KR_KEY", ""))
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


def oil_series(code):
    """OilPriceAPI 최근 1주 값 → {날짜: (시각, 가격)}"""
    def call():
        req = urllib.request.Request("https://api.oilpriceapi.com/v1/prices/past_week?by_code=" + code,
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
    return by_day


def oil_item(by_day, name, unit):
    days = sorted(by_day, reverse=True)
    if not days:
        raise ValueError(name + " 자료 없음")
    return {"name": name, "unit": unit, "date": days[0], "value": by_day[days[0]][1],
            "prev_date": days[1] if len(days) > 1 else None, "prev_value": by_day[days[1]][1] if len(days) > 1 else None}


def fetch_jkm():
    return {"jkm": oil_item(oil_series("JKM_LNG_USD"), "JKM LNG", "USD/MMBtu")}


# 두바이·브렌트·WTI: JKM 과 같은 OilPriceAPI 에서 가져옴(빠름). 코드는 후보를 차례로 시도
OIL_CODES = {"dubai": ("두바이유", ["DUBAI_CRUDE_USD", "DUBAI_USD"]),
             "brent": ("브렌트유", ["BRENT_CRUDE_USD"]),
             "wti": ("WTI", ["WTI_USD"])}


def fetch_crude():
    out = {}
    for key, (name, codes) in OIL_CODES.items():
        for code in codes:
            try:
                out[key] = oil_item(oil_series(code), name, "$/bbl")
                print(f">> [OilPriceAPI] {name} 코드 {code} 사용")
                break
            except Exception as e:
                print(f">> [OilPriceAPI] {name} 코드 {code} 실패: {type(e).__name__}: {str(e)[:100]}")
    if not out:
        raise ValueError("OilPriceAPI 원유 자료 없음")
    return out


def opinet_csv(text):
    """행: (yymmdd, 두바이, 브렌트, WTI). 날짜는 '260930' 또는 '26년09월30일' 두 형식을 모두 허용"""
    rows = []
    for line in csv.reader(io.StringIO(text)):
        if len(line) < 4:
            continue
        d = line[0].strip()
        m = re.fullmatch(r"(\d{2})\D+(\d{1,2})\D+(\d{1,2})\D*", d)
        if m:
            d = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        if not re.fullmatch(r"\d{6}", d):
            continue
        try:
            rows.append((d, float(line[1]), float(line[2]), float(line[3])))
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


def fetch_kau():
    """금융위원회 배출권시세(공공데이터포털). 종가 clpr, 전일대비 vs 를 그대로 사용.
    KAU 종목 중 최신일 거래량이 가장 큰 종목(당해 연도물)을 대표값으로 쓴다."""
    ops = ["https://apis.data.go.kr/1160100/GetGeneralProductInfoService_V2/getCertifiedEmissionReductionPriceInfo_V2",
           "https://apis.data.go.kr/1160100/service/GetGeneralProductInfoService_V2/getCertifiedEmissionReductionPriceInfo_V2"]
    now = datetime.now(KST).date()
    q = {"serviceKey": DATA_KEY, "pageNo": 1, "numOfRows": 300, "resultType": "json",
         "beginBasDt": (now - timedelta(days=14)).strftime("%Y%m%d"), "likeItmsNm": "KAU"}
    last = None
    for base in ops:
        try:
            def call():
                raw = urllib.request.urlopen(urllib.request.Request(base + "?" + urllib.parse.urlencode(q), headers=UA), timeout=40).read()
                return json.loads(raw.decode("utf-8"))
            j = retry(call, tries=2)
            root = j.get("response", j)
            head, body = root.get("header", {}), root.get("body", {})
            items = body.get("items", {}) if isinstance(body, dict) else {}
            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]
            print(f">> [배출권 호출] code={head.get('resultCode')} msg={head.get('resultMsg')} 건수={body.get('totalCount') if isinstance(body, dict) else None}")
            rows = []
            for x in items or []:
                try:
                    rows.append((str(x["basDt"]), str(x.get("itmsNm", "")), float(x["clpr"]), float(x.get("vs") or 0), float(x.get("trqu") or 0)))
                except (KeyError, ValueError, TypeError):
                    pass
            rows = [r for r in rows if r[2] > 0]
            if not rows:
                last = ValueError("배출권 자료 없음")
                continue
            latest = max(r[0] for r in rows)
            cand = sorted([r for r in rows if r[0] == latest], key=lambda r: -r[4])
            print(f">> [배출권 종목별 {latest}] (종목, 종가, 대비, 거래량) 상위:", [(r[1], r[2], r[3], r[4]) for r in cand[:6]])
            print(">> [배출권 조회된 종목 이름]", sorted({r[1] for r in rows}))
            _, name, clpr, vs, _t = cand[0]
            dt = f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"
            same = sorted([r for r in rows if r[1] == name and r[0] < latest])
            prev_v, prev_d = (clpr - vs, None)
            if same:
                prev_d = f"{same[-1][0][:4]}-{same[-1][0][4:6]}-{same[-1][0][6:]}"
            return {"kau": {"name": "배출권(KAU)", "item": name, "unit": "원/톤", "date": dt, "value": clpr,
                            "prev_date": prev_d, "prev_value": prev_v}}
        except Exception as e:
            last = e
            print(f">> [배출권 주소 실패] {base.split('/1160100/')[1][:40]} {type(e).__name__}: {str(e)[:100]}")
    raise last or ValueError("배출권 자료 없음")


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _find_rows(node):
    """응답 JSON 안에서 일자별 행(trd_dd, tdd_clsprc 가 있는 dict 목록)을 찾는다"""
    if isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node) and any("trd_dd" in x and "tdd_clsprc" in x for x in node):
            return node
        for x in node:
            r = _find_rows(x)
            if r:
                return r
    elif isinstance(node, dict):
        for v in node.values():
            r = _find_rows(v)
            if r:
                return r
    return []


def fetch_kau_krx():
    """한국거래소 배출권시장 정보플랫폼(ets.krx.co.kr) 일자별 시세 — 거래 당일 바로 반영.
    ① GenerateOTP 로 일회용 code 를 받고 ② 그 code 로 일자별 정보를 조회한다. 종목은 올해 연도물(KAU26 등)."""
    base = "https://ets.krx.co.kr"
    page = base + "/contents/ETS/03/03010000/ETS03010000.jsp"
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    H = {**UA, "Referer": page}
    now = datetime.now(KST)
    item = "KAU" + now.strftime("%y")
    op.open(urllib.request.Request(page, headers=H), timeout=40).read()          # 쿠키 받기
    otp_url = (base + "/contents/COM/GenerateOTP.jspx?bld=" + urllib.parse.quote("ETS/03/03010000/ets03010000_05", safe="")
               + "&name=grid&_=" + str(int(time.time() * 1000)))
    code = op.open(urllib.request.Request(otp_url, headers=H), timeout=40).read().decode("utf-8", "ignore").strip()
    if len(code) < 20 or "<" in code:
        raise ValueError("KRX 인증값(code) 형식 이상: " + code[:80])
    form = {"isu_cd": item, "fromdate": (now - timedelta(days=20)).strftime("%Y%m%d"), "todate": now.strftime("%Y%m%d"),
            "pagePath": "/contents/ETS/03/03010000/ETS03010000.jsp", "code": code,
            "gNo": "98f13708210194c475687be6106a3b84"}
    req = urllib.request.Request(base + "/contents/ETS/99/ETS99000001.jspx", data=urllib.parse.urlencode(form).encode(),
                                 headers={**H, "X-Requested-With": "XMLHttpRequest"})
    body = op.open(req, timeout=40).read().decode("utf-8", "ignore")
    j = json.loads(body)
    rows = []
    for x in _find_rows(j):
        dd = re.sub(r"\D", "", str(x.get("trd_dd", "")))
        px, vs, vol = _num(x.get("tdd_clsprc")), _num(x.get("cmpprevdd_prc")), _num(x.get("acc_trdvol")) or 0
        names = {str(x.get(k, "")) for k in ("isu_eng_abbrv", "isu_cd", "isu_avvrv")}
        if len(dd) == 8 and px and px > 0 and (item in names or not any(n.startswith("K") for n in names)):
            rows.append((dd, px, vs or 0, vol))
    print(f">> [배출권 KRX] {item} 일자별 {len(rows)}행, 앞부분: {body[:120]!r}")
    if not rows:
        raise ValueError("배출권(KRX) 행 없음")
    rows.sort(reverse=True)
    traded = [r for r in rows if r[3] > 0]
    use = traded if len(traded) >= 2 else rows
    cur, prev = use[0], (use[1] if len(use) > 1 else None)
    ymd = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"
    print(f">> [배출권 KRX] {ymd(cur[0])} 종가 {cur[1]:,.0f} (전 거래일 {prev[0] if prev else None})")
    return {"kau": {"name": "배출권(KAU)", "item": item, "unit": "원/톤", "date": ymd(cur[0]), "value": cur[1],
                    "prev_date": ymd(prev[0]) if prev else None, "prev_value": prev[1] if prev else cur[1] - cur[2]}}


def fetch_kau_any():
    try:
        return fetch_kau_krx()
    except Exception as e:
        print(f">> [배출권] 한국거래소 실패 → 공공데이터포털로 대체: {type(e).__name__}: {str(e)[:120]}")
        return fetch_kau()


def _fx_items(rows, label):
    """rows: [(날짜, 원/달러, 원/100엔)] 최신순 → 환율 항목 2개"""
    out = {}
    for key, name, idx in (("fx", "원/달러 환율", 1), ("fx_jpy", "원/100엔 환율", 2)):
        vals = [(r[0], r[idx]) for r in rows if r[idx] is not None]
        if not vals:
            continue
        out[key] = {"name": name, "item": label, "unit": "원", "date": vals[0][0], "value": vals[0][1],
                    "prev_date": vals[1][0] if len(vals) > 1 else None,
                    "prev_value": vals[1][1] if len(vals) > 1 else None}
    if not out:
        raise ValueError("환율 자료 없음")
    return out


def fetch_fx_exim():
    """한국수출입은행 환율(매매기준율 deal_bas_r): USD, JPY(100). 당일 값은 영업일 11시경 갱신되므로
    아침에는 직전 영업일 값이 최신이 된다. 최근 8일을 거슬러 올라가 유효한 날 2개를 찾는다."""
    if not EXIM_KEY:
        raise ValueError("KOREAEXIM_KEY 미등록")
    today = datetime.now(KST).date()
    found = []
    num = lambda v: float(str(v).replace(",", ""))
    for back in range(0, 8):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        url = ("https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON?"
               + urllib.parse.urlencode({"authkey": EXIM_KEY, "searchdate": d.strftime("%Y%m%d"), "data": "AP01"}))
        def call():
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8"))
        j = retry(call, tries=2)
        j = j if isinstance(j, list) else []
        usd = [x for x in j if str(x.get("cur_unit", "")).startswith("USD")]
        jpy = [x for x in j if str(x.get("cur_unit", "")).startswith("JPY")]
        if not usd:
            print(f">> [환율] {d} 자료 없음")
            continue
        found.append((d.isoformat(), num(usd[0]["deal_bas_r"]), num(jpy[0]["deal_bas_r"]) if jpy else None))
        if len(found) == 2:
            break
    if not found:
        raise ValueError("환율 자료 없음")
    return _fx_items(found, "매매기준율")


def fetch_fx_ecb():
    """Frankfurter(ECB 기준환율). 인증키 불필요, 일별 이력 제공. USD/KRW, JPY는 100엔당 원으로 환산."""
    today = datetime.now(KST).date()
    q = urllib.parse.urlencode({"base": "USD", "symbols": "KRW,JPY"})
    last = None
    for base in ("https://api.frankfurter.dev/v1/", "https://api.frankfurter.app/"):
        try:
            url = f"{base}{(today - timedelta(days=12)).isoformat()}..{today.isoformat()}?{q}"
            def call():
                return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8"))
            j = retry(call, tries=2)
            rows = []
            for d in sorted((j.get("rates") or {}), reverse=True):
                v = j["rates"][d]
                if "KRW" in v:
                    rows.append((d, float(v["KRW"]), (float(v["KRW"]) / float(v["JPY"]) * 100) if v.get("JPY") else None))
            if not rows:
                raise ValueError("환율 자료 없음")
            print(f">> [환율 ECB] {rows[0][0]} USD {rows[0][1]:.2f}, 100엔 {rows[0][2]:.2f}" if rows[0][2] else f">> [환율 ECB] {rows[0]}")
            return _fx_items(rows, "ECB 기준")
        except Exception as e:
            last = e
            print(f">> [환율 ECB 주소 실패] {base} {type(e).__name__}: {str(e)[:100]}")
    raise last or ValueError("환율 자료 없음")


def fetch_fx():
    """수출입은행 키가 있으면 공식 매매기준율을 먼저, 없거나 실패하면 ECB 기준환율을 쓴다."""
    if EXIM_KEY:
        try:
            return fetch_fx_exim()
        except Exception as e:
            print(f">> [환율 수출입은행 실패] {type(e).__name__}: {str(e)[:100]} → ECB 기준환율로 대체")
    return fetch_fx_ecb()


def fetch_rec_kpx():
    """전력거래소 홈페이지 첫 화면 '오늘의 REC' (가장 최근 거래일의 평균가). 전일 값은 저장된 이전 값에서 이어받는다."""
    def call():
        req = urllib.request.Request("https://kpx.or.kr/", headers=UA)
        return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "ignore")
    html = retry(call, tries=2)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html)))
    i = text.find("오늘의 REC")
    seg = text[i:i + 600] if i >= 0 else ""
    m = re.search(r"(\d{4})\.\s?(\d{2})\.\s?(\d{2})", seg)
    p = re.search(r"평균가\D{0,20}?([\d,]{4,})", seg)
    if not (m and p):
        print(f">> [REC KPX] 형식을 읽지 못함. 부근: {seg[:200]!r}")
        raise ValueError("REC(KPX) 형식 불일치")
    day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    val = float(p.group(1).replace(",", ""))
    old = (json.load(open(OUT, encoding="utf-8")).get("items", {}) if os.path.exists(OUT) else {}).get("rec") or {}
    if old.get("date") == day:
        pd, pv = old.get("prev_date"), old.get("prev_value")
    elif old.get("date") and old.get("value") is not None and old["date"] < day:
        pd, pv = old["date"], old["value"]
    else:
        pd, pv = old.get("prev_date"), old.get("prev_value")
    print(f">> [REC KPX] {day} 평균가 {val:,.0f} (전 거래일 {pd} {pv})")
    return {"rec": {"name": "REC", "item": "평균가", "unit": "원/REC", "date": day, "value": val,
                    "prev_date": pd, "prev_value": pv}}


def fetch_rec_any():
    try:
        return fetch_rec_kpx()
    except Exception as e:
        print(f">> [REC] 전력거래소 홈페이지 실패 → 공공데이터포털로 대체: {type(e).__name__}")
        return fetch_rec()


def fetch_rec():
    """한국전력거래소 REC 현물시장 정보(공공데이터포털). 육지 평균가(landAvgPrc).
    거래가 있는 날(장운영일)에만 값이 있으므로, 날짜를 지정해 최근일부터 거슬러 올라가 2개 거래일을 찾는다."""
    base = "https://apis.data.go.kr/B552115/RecMarketInfo2/getRecMarketInfo2"
    today = datetime.now(KST).date()
    found = []
    for back in range(0, 25):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        q = {"serviceKey": DATA_KEY, "pageNo": 1, "numOfRows": 5, "dataType": "json", "bzDd": d.strftime("%Y%m%d")}
        def call():
            return json.loads(urllib.request.urlopen(urllib.request.Request(base + "?" + urllib.parse.urlencode(q), headers=UA), timeout=40).read().decode("utf-8"))
        j = retry(call, tries=2)
        root = j.get("response", j)
        body = root.get("body", {})
        items = body.get("items", {}) if isinstance(body, dict) else {}
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        rows = [x for x in (items or []) if x.get("landAvgPrc") not in (None, "")]
        if not rows:
            continue
        try:
            found.append((d.isoformat(), float(rows[0]["landAvgPrc"])))
        except (TypeError, ValueError):
            continue
        if len(found) == 2:
            break
    if not found:
        raise ValueError("REC 자료 없음")
    return {"rec": {"name": "REC(육지)", "item": "평균가", "unit": "원/REC", "date": found[0][0], "value": found[0][1],
                    "prev_date": found[1][0] if len(found) > 1 else None,
                    "prev_value": found[1][1] if len(found) > 1 else None}}


def main():
    store = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    items = store.get("items", {})
    ok = 0
    def fetch_oil_all():
        got = {}
        try:
            got.update(fetch_crude())
        except Exception as e:
            print(f">> [국제유가] OilPriceAPI 실패 → 오피넷으로 대체: {type(e).__name__}")
        if len(got) < 3:  # 하나라도 못 받았으면 오피넷(느림)으로 보충
            try:
                for k, v in fetch_opinet().items():
                    got.setdefault(k, v)
            except Exception as e:
                print(f">> [오피넷] 보충 실패: {type(e).__name__}")
        if not got:
            raise ValueError("국제유가 자료 없음")
        return got
    for label, fn in (("JKM", fetch_jkm), ("국제유가", fetch_oil_all), ("배출권", fetch_kau_any), ("REC", fetch_rec_any), ("환율", fetch_fx)):
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
    # 날짜별 보관본(history/): 화면에서 과거 일자를 고르면 이 파일을 읽는다. 같은 날 여러 번 실행되면 마지막 값으로 덮어쓴다.
    os.makedirs("history", exist_ok=True)
    day = datetime.now(KST).strftime("%Y%m%d")
    with open(f"history/ind_{day}.json", "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "items": items}, f, ensure_ascii=False, indent=1)
    print(f">> history/ind_{day}.json 보관 완료")


if __name__ == "__main__":
    main()
