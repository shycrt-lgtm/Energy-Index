# -*- coding: utf-8 -*-
"""에너지시장 뉴스 수집: 구글 뉴스 RSS(키워드별) -> 중복 제거 -> 상위 10건 -> news.json"""
import json, re, time, difflib, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
KEYWORDS = ["SMP 전력시장", "전력도매가격", "LNG 가격", "도시가스 요금", "집단에너지", "탄소배출권",
            "한국가스공사", "한국전력", "전기위원회", "RE100", "전력거래소", "발전사업"]
PER_KEYWORD = 15      # 키워드당 가져올 최대 건수
MAX_AGE_H = 36        # 이 시간보다 오래된 기사는 제외
TOP_N = 10
SIM = 0.72            # 제목 유사도 기준(이 이상이면 같은 기사로 봄)

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
            if nx == g["n"] or difflib.SequenceMatcher(None, nx, g["n"]).ratio() >= SIM:
                g["more"] += 1
                if x["source"] and x["source"] != g["item"]["source"]:
                    g["srcs"].add(x["source"])
                break
        else:
            groups.append({"n": nx, "item": x, "more": 0, "srcs": {x["source"]}})
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
    # 여러 매체가 다룬 기사(= 중요도 높음) 우선, 같으면 최신순
    groups.sort(key=lambda g: (-g["more"], -g["item"]["pub"].timestamp()))
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
