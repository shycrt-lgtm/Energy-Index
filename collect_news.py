# -*- coding: utf-8 -*-
"""에너지시장 뉴스 수집: 구글 뉴스 RSS(키워드별) -> 중복 제거 -> 상위 10건 -> news.json"""
import json, re, time, difflib, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
KEYWORDS = ["SMP 전력시장", "전력도매가격", "LNG 가격", "도시가스 요금", "집단에너지", "탄소배출권",
            "한국가스공사", "한국전력", "전기위원회", "RE100", "전력거래소", "발전사업",
            "전력수급기본계획", "ESS 에너지저장 입찰", "RPS 신재생", "태양광 PPA", "전기차 충전", "VPP 가상발전소", "용량시장"]
PER_KEYWORD = 15      # 키워드당 가져올 최대 건수
MAX_AGE_H = 36        # 이 시간보다 오래된 기사는 제외
TOP_N = 10
SIM = 0.60            # 제목 글자 유사도 기준(이 이상이면 같은 기사로 봄)
JAC = 0.35            # 두 글자 조각 겹침 비율 기준(이 이상이면 같은 기사로 봄)
# 시장과 관련 깊은 낱말(제목에 있으면 가산) / 시장과 무관한 행사·인사·홍보 낱말(제목에 있으면 감점)
CORE = ["SMP", "전력시장", "전력도매", "도매가격", "계통한계", "LNG", "천연가스", "도시가스", "가스요금", "전기요금", "요금",
        "가격", "배출권", "탄소", "REC", "열병합", "집단에너지", "한전", "한국전력", "전력거래소", "가스공사", "수급",
        "정산", "상한제", "재생에너지", "RE100", "발전", "전력", "유가", "환율", "에너지", "송전", "전기위원회", "산업부", "기후부", "전력수급기본계획", "ESS", "RPS", "PPA", "태양광", "충전", "VPP", "가상발전소", "에너지저장", "입찰", "용량시장"]
NOISE_HARD = ["연봉", "채용", "인사", "부고", "결혼", "장학", "봉사", "기부", "특징주", "목표주가", "주가", "수상", "표창", "시상", "동정", "인사말", "학생", "대학교", "국립대"]
NOISE_SOFT = ["설명회", "업무협약", "MOU", "협약", "포럼", "세미나", "워크숍", "개최", "성료", "간담회", "발대식", "캠페인"]

def retry(fn, n=3, wait=3):
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            print(f"   재시도 {i+1}/{n}: {type(e).__name__} {e}")
            time.sleep(wait)
    return None

def fetch_rss(q):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:1d") + "&hl=ko&gl=KR&ceid=KR:ko"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
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
    return min(core, 4) * 3 + min(g["more"], 3) - 6 * sum(1 for w in NOISE_HARD if w in t) - 2 * sum(1 for w in NOISE_SOFT if w in t), core

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
    allit, ok = [], 0
    for q in KEYWORDS:
        xml = retry(lambda: fetch_rss(q))
        if not xml:
            print(f">> [뉴스] '{q}' 실패"); continue
        try:
            rows = parse(xml)
        except Exception as e:
            print(f">> [뉴스] '{q}' 해석 실패 {e}"); continue
        rows = [r for r in rows if now - r["pub"] <= timedelta(hours=MAX_AGE_H)][:PER_KEYWORD]
        print(f">> [뉴스] '{q}' {len(rows)}건"); ok += 1
        allit += rows
        time.sleep(1)
    if not allit:
        print(">> [뉴스] 수집된 기사가 없어 기존 파일을 유지합니다."); raise SystemExit(1 if ok == 0 else 0)
    groups = dedup(allit)
    # 시장 관련도(낱말 점수) + 여러 매체가 다룬 정도로 점수를 매겨 상위만 선택, 같으면 최신순
    groups = [g for g in groups if score(g)[1] >= 1 and score(g)[0] > 0]
    groups.sort(key=lambda g: (-score(g)[0], -g["item"]["pub"].timestamp()))
    top = groups[:TOP_N]
    top.sort(key=lambda g: -g["item"]["pub"].timestamp())      # 화면에는 최신순
    items = [{"title": g["item"]["title"], "source": g["item"]["source"], "link": g["item"]["link"],
              "pub": g["item"]["pub"].strftime("%Y-%m-%d %H:%M"), "more": g["more"]} for g in top]
    json.dump({"updated": now.strftime("%Y-%m-%d %H:%M"), "items": items},
              open("news.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f">> [뉴스] 수집 {len(allit)}건 -> 중복 제거 후 {len(groups)}건 -> 상위 {len(items)}건 저장")
    for i, x in enumerate(items, 1):
        print(f"   {i}. [{x['source']}] {x['title']} (같은 내용 +{x['more']})")

if __name__ == "__main__":
    main()
