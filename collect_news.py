# -*- coding: utf-8 -*-
"""에너지시장 뉴스 수집: 구글 뉴스 RSS(키워드별) -> 중복 제거 -> 상위 10건 -> news.json"""
import json, re, time, difflib, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
KEYWORDS = ["집단에너지", "전력시장", "용량시장", "SMP 전력시장", "전력도매가격", "PPA 전력", "전력수급기본계획", "전기본 에너지",
            "RPS 신재생", "태양광 발전", "풍력 발전", "LNG 발전", "LNG 가격", "도시가스 요금", "탄소배출권", "전력거래소",
            "전기위원회", "발전사업 허가", "ESS 에너지저장", "열병합발전", "RE100", "전기차 충전", "VPP 가상발전소", "열병합 용량입찰", "LNG 용량시장",
            "SMR", "원전", "탄소중립", "가스터빈", "화력발전", "석탄발전", "JKM LNG", "히트펌프", "재생열", "전극보일러"]
# ▼▼ 가져올 언론사 목록 (여기에 있는 언론사 기사만 사용). 추가·삭제는 이름만 고치면 됩니다. ▼▼
# 구글 뉴스에 표시되는 언론사 이름의 일부만 맞아도 인정합니다(예: "한국경제"는 "한국경제TV"도 포함).
# ALLOWED_SOURCES를 빈 목록 [] 으로 두면 언론사 제한 없이 모두 가져옵니다.
ALLOWED_SOURCES = []
# 아래 3개 언론사는 구글 검색에 더해 해당 사이트에서도 같은 키워드로 따로 검색하고, 점수도 조금 더 줍니다.
# 우선 노출 언론사 (위에 있을수록 먼저 노출). 이름은 구글 뉴스에 표시되는 언론사명, 값은 사이트 주소입니다.
FOCUS_SITES = {"조선비즈": "biz.chosun.com", "이투뉴스": "e2news.com", "조선일보": "chosun.com", "에너지경제": "ekn.kr",
               "중앙일보": "joongang.co.kr", "매일경제": "mk.co.kr", "투데이에너지": "todayenergy.kr"}
FOCUS_ORDER = list(FOCUS_SITES)

def src_rank(src):
    """우선 언론사 순번(0이 가장 높음), 해당 없으면 99"""
    for i, n in enumerate(FOCUS_ORDER):
        if n in (src or ""):
            return i
    return 99
# ▲▲ ----------------------------------------------------------------------- ▲▲

def source_ok(src):
    return (not ALLOWED_SOURCES) or any(w in (src or "") for w in ALLOWED_SOURCES)

PER_KEYWORD = 25      # 키워드당 가져올 최대 건수
MAX_AGE_H = 36        # 평소 이 시간보다 오래된 기사는 제외 (주말·휴일이 끼면 아래 함수가 자동으로 늘림)
# 공휴일(주말 제외). 새 해가 되면 날짜를 추가하세요.
HOLIDAYS = {"2026-10-05", "2026-10-09", "2026-12-25", "2027-01-01", "2027-02-06", "2027-02-07", "2027-02-08", "2027-02-09",
            "2027-03-01", "2027-03-02", "2027-05-05", "2027-05-13", "2027-06-07", "2027-08-16", "2027-09-14", "2027-09-15",
            "2027-09-16", "2027-10-04", "2027-10-11", "2027-12-27"}

def lookback_hours(now):
    """직전 영업일 낮 12시 이후 기사부터 모두 포함. 평일 연속이면 기존 36시간."""
    d = now.date()
    while True:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS:
            break
    cut = datetime(d.year, d.month, d.day, 12, 0, tzinfo=KST)
    return max(MAX_AGE_H, int((now - cut).total_seconds() // 3600) + 1)
TOP_N = 15
SIM = 0.50            # 제목 글자 유사도 기준(이 이상이면 같은 기사로 봄)
JAC = 0.25            # 두 글자 조각 겹침 비율 기준(이 이상이면 같은 기사로 봄)
# 시장과 관련 깊은 낱말(제목에 있으면 가산) / 시장과 무관한 행사·인사·홍보 낱말(제목에 있으면 감점)
CORE = ["SMP", "전력시장", "전력도매", "도매가격", "계통한계", "LNG", "천연가스", "도시가스", "가스요금", "전기요금", "요금",
        "가격", "배출권", "탄소", "REC", "열병합", "집단에너지", "한전", "한국전력", "전력거래소", "가스공사", "수급",
        "정산", "상한제", "재생에너지", "RE100", "발전", "전력", "유가", "환율", "에너지", "송전", "전기위원회", "산업부", "기후부", "전력수급기본계획", "ESS", "RPS", "PPA", "태양광", "충전", "VPP", "가상발전소", "에너지저장", "입찰", "용량시장", "SMR", "원전", "탄소중립", "가스터빈", "화력", "석탄", "JKM", "히트펌프", "재생열", "전극보일러"]
NOISE_HARD = ["연봉", "채용", "인사", "부고", "결혼", "장학", "봉사", "기부", "특징주", "목표주가", "주가", "수상", "표창", "시상", "동정", "인사말", "학생", "대학교", "국립대",
              # 스포츠(가스공사 농구단 등)
              "농구", "프로농구", "페가수스", "KBL", "구단", "선수", "배구", "야구", "축구", "시즌",
              # 지역상생·협약성 홍보 기사
              "맞손", "상생", "손잡", "업무협약", "MOU", "협약", "지역사회", "나눔", "후원",
              # 해외 사업·해외 뉴스
              "베트남", "인도네시아", "필리핀", "말레이시아", "태국", "싱가포르", "몽골", "카자흐", "우즈벡", "중동", "사우디", "UAE", "호주", "캐나다", "멕시코", "브라질", "인도 ", "아프리카", "해외"]
NOISE_SOFT = ["설명회", "포럼", "세미나", "워크숍", "개최", "성료", "간담회", "발대식", "캠페인"]
# 사용자가 중점으로 보는 주제(제목에 있으면 크게 가산)
PRIORITY = ["집단에너지", "전력시장", "용량시장", "SMP", "PPA", "전력수급기본계획", "전기본", "용량입찰", "LNG 용량", "RPS", "태양광", "풍력", "열병합", "전력거래소", "도매가격", "LNG발전", "발전사업", "계통한계"]

# 정책·제도 기사(화면 맨 위에 노출) / 기술 기사(맨 아래로 후순위)
POLICY = ["정책", "제도", "개편", "개선안", "기본계획", "전기본", "전력수급기본계획", "용량시장", "용량입찰", "전력시장", "도매시장", "전기위원회",
          "산업부", "기후부", "기후에너지환경부", "국회", "정부", "법안", "개정", "시행령", "고시", "규제", "허가", "요금", "정산", "상한", "보조금",
          "지침", "공고", "탈탄소", "RPS", "PPA", "배출권", "계통", "집단에너지법", "전기사업법", "열병합"]
TECH = ["기술", "기술개발", "개발", "실증", "연구", "소재", "효율", "배터리", "전고체", "모듈", "인버터", "수소", "연료전지", "특허",
        "상용화", "시스템", "AI", "알고리즘", "플랫폼", "센서", "탠덤", "페로브스카이트", "준공", "착공"]

def tier(t):
    """0=정책(맨 위), 1=일반, 2=기술(후순위)"""
    if any(w in t for w in POLICY):
        return 0
    if any(w in t for w in TECH):
        return 2
    return 1

def hangul_ratio(t):
    letters = re.findall(r"[A-Za-z가-힣]", t)
    return (sum(1 for c in letters if "가" <= c <= "힣") / len(letters)) if letters else 0

def retry(fn, n=3, wait=3):
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            print(f"   재시도 {i+1}/{n}: {type(e).__name__} {e}")
            time.sleep(wait)
    return None

def fetch_rss(q, when="when:2d"):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " " + when) + "&hl=ko&gl=KR&ceid=KR:ko"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()

def parse(xml_bytes):
    out = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)].strip()
        elif " - " in title and not source:
            title, source = title.rsplit(" - ", 1)
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:
            continue
        if title and link:
            out.append({"title": title, "source": source, "link": link, "pub": pub})
    return out

def norm(t):
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】", " ", t)       # [단독] (종합) 등 말머리 제거
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t)                       # 특수문자·공백 제거
    return t.lower()

def grams(n):
    return {n[i:i+2] for i in range(len(n) - 1)} or {n}

def same(a, b, ga, gb):
    if a == b:
        return True
    if difflib.SequenceMatcher(None, a, b).ratio() >= SIM:
        return True
    return len(ga & gb) / max(1, len(ga | gb)) >= JAC

def score(g):
    t = g["item"]["title"]
    core = sum(1 for w in CORE if w.lower() in t.lower())
    tr = tier(t)
    pri = sum(1 for w in PRIORITY if w.lower() in t.lower())
    r = src_rank(g["item"]["source"])
    bonus = (len(FOCUS_ORDER) - r) if r < 99 else 0
    return min(core, 4) * 3 + min(pri, 3) * 5 + bonus + (6 if tr == 0 else -6 if tr == 2 else 0) + min(g["more"], 3) - 20 * sum(1 for w in NOISE_HARD if w in t) - 2 * sum(1 for w in NOISE_SOFT if w in t), core

def dedup(items):
    # 1) 링크 중복 제거
    seen, uniq = set(), []
    for x in items:
        if x["link"] in seen:
            continue
        seen.add(x["link"]); uniq.append(x)
    # 2) 제목 유사도로 묶기 (먼저 나온 기사를 대표로)
    uniq.sort(key=lambda x: x["pub"])
    groups = []
    for x in uniq:
        nx = norm(x["title"])
        if not nx:
            continue
        for g in groups:
            if same(nx, g["n"], grams(nx), g["g"]):
                g["more"] += 1
                if x["source"] and x["source"] != g["item"]["source"]:
                    g["srcs"].add(x["source"])
                break
        else:
            groups.append({"n": nx, "g": grams(nx), "item": x, "more": 0, "srcs": {x["source"]}})
    return groups

def main():
    now = datetime.now(KST)
    global MAX_AGE_H, WHEN
    MAX_AGE_H = lookback_hours(now)
    WHEN = "when:%dd" % (MAX_AGE_H // 24 + 2)
    print(f">> [뉴스] 수집 범위: 최근 {MAX_AGE_H}시간 ({WHEN})")
    allit, ok = [], 0
    import concurrent.futures as cf
    t0 = time.time()
    DEADLINE = 170   # 초. 이 시간이 지나면 남은 검색은 건너뛰고 지금까지 모은 기사로 마무리

    def q(k):
        return '"' + k + '"' if " " in k else k
    groups_kw = [KEYWORDS[i:i+8] for i in range(0, len(KEYWORDS), 8)]
    tasks = [("kw", k, k, None) for k in KEYWORDS]
    for name, dom in FOCUS_SITES.items():
        for gk in groups_kw:
            tasks.append(("site", name, "(" + " OR ".join(q(k) for k in gk) + ") site:" + dom, name))

    def work(t):
        if time.time() - t0 > DEADLINE:
            return t, None, "skip"
        xml = retry(lambda: fetch_rss(t[2], WHEN), n=2, wait=2)
        return t, xml, None

    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(work, tasks))      # 입력 순서대로 결과를 돌려줌

    site_n, skipped_n = {}, 0
    for t, xml, why in results:
        kind, label, _, name = t
        if why == "skip":
            skipped_n += 1; continue
        if not xml:
            print(f">> [뉴스] '{label}' 실패"); continue
        try:
            rows = parse(xml)
        except Exception as e:
            print(f">> [뉴스] '{label}' 해석 실패 {e}"); continue
        rows = [r for r in rows if now - r["pub"] <= timedelta(hours=MAX_AGE_H)
                and hangul_ratio(r["title"]) >= 0.5 and not any(w in r["title"] for w in NOISE_HARD)]
        if kind == "kw":
            skipped = {r["source"] for r in rows if not source_ok(r["source"])}
            if skipped:
                print(f"   (지정 외 언론사 제외: {', '.join(sorted(skipped))})")
            rows = [r for r in rows if source_ok(r["source"])][:PER_KEYWORD]
            print(f">> [뉴스] '{label}' {len(rows)}건"); ok += 1
        else:
            for r in rows:
                if not r["source"]:
                    r["source"] = name
            site_n[name] = site_n.get(name, 0) + len(rows)
        allit += rows
    for name, dom in FOCUS_SITES.items():
        print(f">> [뉴스] {name}({dom}) 지정 검색 {site_n.get(name, 0)}건")
    if skipped_n:
        print(f">> [뉴스] 시간 초과로 {skipped_n}건 검색을 건너뜀")
    print(f">> [뉴스] 수집에 {int(time.time() - t0)}초 걸림")
    if not allit:
        print(">> [뉴스] 수집된 기사가 없어 기존 파일을 유지합니다."); raise SystemExit(1 if ok == 0 else 0)
    groups = dedup(allit)
    # 시장 관련도(낱말 점수) + 여러 매체가 다룬 정도로 점수를 매겨 상위만 선택, 같으면 최신순
    groups = [g for g in groups if score(g)[1] >= 1 and score(g)[0] > 0]
    # 같은 기관(제목 맨 앞 낱말)이 주어인 기사가 한쪽으로 몰리지 않도록, 이미 뽑힌 수만큼 점수를 깎아 가며 한 건씩 선택
    def lead(g):
        m = re.search(r"[A-Za-z가-힣]{2,}", re.sub(r"\[[^\]]*\]", " ", g["item"]["title"]))
        return m.group(0)[:3] if m else ""
    top, cnt = [], {}
    pool = list(groups)
    while pool and len(top) < TOP_N:
        best = max(pool, key=lambda g: (score(g)[0] - 4 * cnt.get(lead(g), 0), g["item"]["pub"].timestamp()))
        pool.remove(best); top.append(best)
        cnt[lead(best)] = cnt.get(lead(best), 0) + 1
    # 화면 순서: 정책 → 일반 → 기술, 같은 구분 안에서는 우선 언론사 순서 → 최신순
    top.sort(key=lambda g: (tier(g["item"]["title"]), src_rank(g["item"]["source"]), -g["item"]["pub"].timestamp()))
    items = [{"title": g["item"]["title"], "source": g["item"]["source"], "link": g["item"]["link"],
              "pub": g["item"]["pub"].strftime("%Y-%m-%d %H:%M"), "more": g["more"]} for g in top]
    json.dump({"updated": now.strftime("%Y-%m-%d %H:%M"), "items": items},
              open("news.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    import os
    os.makedirs("history", exist_ok=True)                      # 날짜별 보관본 (같은 날 마지막 실행이 남음)
    day = now.strftime("%Y%m%d")
    json.dump({"updated": now.strftime("%Y-%m-%d %H:%M"), "items": items},
              open(f"history/news_{day}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f">> [뉴스] history/news_{day}.json 보관")
    print(f">> [뉴스] 수집 {len(allit)}건 -> 중복 제거 후 {len(groups)}건 -> 상위 {len(items)}건 저장")
    for i, x in enumerate(items, 1):
        print(f"   {i}. [{x['source']}] {x['title']} (같은 내용 +{x['more']})")

if __name__ == "__main__":
    main()
