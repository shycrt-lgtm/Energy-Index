# -*- coding: utf-8 -*-
"""에너지시장 뉴스 수집: 구글 뉴스 RSS(키워드별) -> 중복 제거 -> 상위 15건 -> news.json"""
import json, re, time, difflib, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
KEYWORDS = ["집단에너지", "전력시장", "용량시장", "SMP 전력시장", "전력도매가격", "PPA 전력", "전력수급기본계획", "전기본 에너지",
            "RPS 신재생", "태양광 발전", "풍력 발전", "LNG 발전", "LNG 가격", "도시가스 요금", "탄소배출권", "전력거래소",
            "전기위원회", "발전사업 허가", "ESS 에너지저장", "열병합발전", "RE100", "전기차 충전", "VPP 가상발전소", "열병합 용량입찰", "LNG 용량시장",
            "SMR", "원전", "탄소중립", "가스터빈", "화력발전", "석탄발전", "JKM LNG", "히트펌프", "재생열", "전극보일러",
            "발전소 사고", "발전소 화재", "발전소 폭발", "발전소 불시정지", "도시가스 공급비용", "구역전기",
            "LNG 시장", "LNG 터미널", "LNG 도입", "LNG 수입", "천연가스", "가스공사 LNG"]

# ▼▼ 그룹 사업장 사명. 제목에 사명이 있으면 ①따로 검색 ②점수 크게 가산·다른 기사에 밀리지 않음 ③화면 맨 위 노출 ④협약·MOU·인사 등 노이즈 필터 면제 ▼▼
# 추가·삭제는 이름만 고치면 됩니다. 띄어쓰기는 무시하고 비교합니다(예: "삼천리 광명" -> "삼천리광명"도 인정).
# "삼천리 광명"은 "삼천리 광명열병합"처럼 뒤에 다른 말이 붙어도 걸립니다. "삼천리ES 광명사업단"은 삼천리와 광명이 붙어 있지 않아 걸리지 않습니다.
COMPANIES = ["안산도시개발", "삼천리 광명", "삼천리 광명열병합", "삼천리 구역전기", "에스파워", "휴세스"]
# ▲▲ ----------------------------------------------------------------------- ▲▲

# 사명 앞에 다른 글자가 붙은 경우는 제외(예: "이오에스 파워볼"의 "에스파워"). 사명 뒤에 붙는 말은 허용(예: "삼천리 광명열병합", "휴세스는").
_COMP_RE = [re.compile(r"(?<![가-힣A-Za-z0-9])" + r"\s*".join(map(re.escape, c.replace(" ", "")))) for c in COMPANIES]

def has_company(t):
    t = t or ""
    if any(w in t for w in GAMBLE_NOISE):
        return False
    return any(r.search(t) for r in _COMP_RE)

# ▼▼ 가져올 언론사 목록 (여기에 있는 언론사 기사만 사용). 추가·삭제는 이름만 고치면 됩니다. ▼▼
# 구글 뉴스에 표시되는 언론사 이름의 일부만 맞아도 인정합니다(예: "한국경제"는 "한국경제TV"도 포함).
# ALLOWED_SOURCES를 빈 목록 [] 으로 두면 언론사 제한 없이 모두 가져옵니다.
ALLOWED_SOURCES = []
# 아래 언론사는 구글 검색에 더해 해당 사이트에서도 같은 키워드로 따로 검색하고, 점수도 조금 더 줍니다.
# 우선 노출 언론사 (위에 있을수록 먼저 노출). 이름은 구글 뉴스에 표시되는 언론사명, 값은 사이트 주소입니다.
FOCUS_SITES = {"조선비즈": "biz.chosun.com", "이투뉴스": "e2news.com", "조선일보": "chosun.com", "에너지경제": "ekn.kr",
               "중앙일보": "joongang.co.kr", "매일경제": "mk.co.kr", "투데이에너지": "todayenergy.kr", "전기신문": "electimes.com"}
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

# ▼▼ 제외할 언론사(구글 뉴스에 표시되는 언론사명의 일부, 대소문자 무시). 제목에 나라 이름이 없어도 이 언론사 기사는 모두 뺍니다. 사명 기사도 예외 없음. 새로 걸러낼 곳은 여기에 이름만 추가하세요. ▼▼
BLOCKED_SOURCES = ["베트남", "vietnam", "viet nam", "vnexpress", "vov", "vietnamplus", "vietnamnet", "tuoi tre", "thanh nien",
                   "dan tri", "nhan dan", "saigon", "hanoi", "vir.com.vn", ".vn",
                   # 시장조사 보고서 판매·홍보 사이트(언론사 아님)
                   "indexbox", "index box", "researchandmarkets", "research and markets", "marketsandmarkets", "markets and markets",
                   "grand view research", "mordor intelligence", "fortune business insights", "allied market research", "imarc",
                   "precedence research", "straits research", "verified market", "reportlinker", "business research insights",
                   "헬로디디", "hellodd", "market research", "marketresearch", "research report", "openpr", "einpresswire", "ein presswire", "globenewswire", "prnewswire", "pr newswire", "businesswire", "business wire"]
# ▲▲ ----------------------------------------------------------------------- ▲▲

def blocked_source(src):
    s = (src or "").lower()
    return any(w in s for w in BLOCKED_SOURCES)

PER_KEYWORD = 25      # 키워드당 가져올 최대 건수
MAX_AGE_H = 30        # 평소 이 시간보다 오래된 기사는 제외 (주말·휴일이 끼면 아래 함수가 자동으로 늘림)
# 공휴일(주말 제외). 새 해가 되면 날짜를 추가하세요.
HOLIDAYS = {"2026-10-05", "2026-10-09", "2026-12-25", "2027-01-01", "2027-02-06", "2027-02-07", "2027-02-08", "2027-02-09",
            "2027-03-01", "2027-03-02", "2027-05-05", "2027-05-13", "2027-06-07", "2027-08-16", "2027-09-14", "2027-09-15",
            "2027-09-16", "2027-10-04", "2027-10-11", "2027-12-27"}

def lookback_hours(now):
    """직전 영업일 낮 12시 이후 기사부터 모두 포함. 평일 연속이면 기본 30시간."""
    d = now.date()
    while True:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS:
            break
    cut = datetime(d.year, d.month, d.day, 12, 0, tzinfo=KST)
    return max(MAX_AGE_H, int((now - cut).total_seconds() // 3600) + 1)
TOP_N = 15
# ▼▼ 고정 기사: 수집 범위(최근 30시간)나 점수와 관계없이 지정한 순위에 넣는 기사. 전일 노출 제외 규칙도 적용하지 않음. ▼▼
# until(KST) 이후 실행부터는 자동으로 빠집니다. rank 는 화면 순위(1부터). 지난 항목은 지워도 됩니다.
PINNED = [
    {"title": "[단독] PPA 확대·전기요금 감면…산자위 의원도 초광역특구법 발의", "source": "서울경제",
     "link": "https://m.sedaily.com/article/20103709", "pub": "2026-10-20 12:10", "until": "2026-10-22 12:00", "rank": 2},
]
# ▲▲ ----------------------------------------------------------------------- ▲▲
# LNG 기사 최소 확보: 제목에 LNG 관련 낱말이 있는 기사를 매일 이 건수 이상 포함(후보가 있을 때). 점수가 낮아도 우선 확보한다.
LNG_MIN = 2
LNG_RE = re.compile(r"LNG|액화천연가스|천연가스|가스공사|\bJKM\b|\bTTF\b|헨리\s*허브|LNG선|인수기지")
SIM = 0.50            # 제목 글자 유사도 기준(이 이상이면 같은 기사로 봄)
JAC = 0.25            # 두 글자 조각 겹침 비율 기준(이 이상이면 같은 기사로 봄)
# 시장과 관련 깊은 낱말(제목에 있으면 가산) / 시장과 무관한 행사·인사·홍보 낱말(제목에 있으면 감점)
CORE = ["SMP", "전력시장", "전력도매", "도매가격", "계통한계", "LNG", "천연가스", "도시가스", "가스요금", "전기요금", "요금",
        "가격", "배출권", "탄소", "REC", "열병합", "집단에너지", "한전", "한국전력", "전력거래소", "가스공사", "수급",
        "정산", "상한제", "재생에너지", "RE100", "발전", "전력", "유가", "환율", "에너지", "송전", "전기위원회", "산업부", "기후부", "전력수급기본계획", "ESS", "RPS", "PPA", "태양광", "충전", "VPP", "가상발전소", "에너지저장", "입찰", "용량시장", "SMR", "원전", "탄소중립", "가스터빈", "화력", "석탄", "JKM", "히트펌프", "재생열", "전극보일러", "구역전기"]
# 사진만 있는 기사(사진 설명문·포토뉴스·화보 등) - 사명이 있어도 제외
PHOTO_NOISE = ["재판매", "DB 금지", "무단 전재", "[포토", "포토뉴스", "[사진", "[화보", "[영상", "[동영상", "[카드뉴스", "사진 제공", "사진=", "기념 촬영", "기념사진"]
NOISE_HARD = ["연봉", "채용", "인사", "부고", "결혼", "장학", "봉사", "기부", "특징주", "목표주가", "주가", "수상", "표창", "시상", "동정", "인사말", "학생", "대학교", "국립대",
              # 스포츠(가스공사 농구단 등)
              "농구", "프로농구", "페가수스", "KBL", "구단", "선수", "배구", "야구", "축구", "시즌",
              # 지역상생·협약성 홍보 기사
              "맞손", "상생", "손잡", "업무협약", "MOU", "협약", "지역사회", "나눔", "후원",
              # 해외 사업·해외 뉴스
              "베트남", "인도네시아", "필리핀", "말레이시아", "태국", "싱가포르", "몽골", "카자흐", "우즈벡", "중동", "사우디", "UAE", "호주", "캐나다", "멕시코", "브라질", "인도 ", "아프리카", "해외", "우크라이나", "러시아", "이스라엘", "이란", "가자"] + PHOTO_NOISE
# 사행성·도박 광고(사명 면제 없이 항상 제외)
GAMBLE_NOISE = ["파워볼", "토토", "카지노", "바카라", "베팅", "배팅", "먹튀", "홀덤", "도박", "사다리게임", "메이저사이트", "안전놀이터", "놀이터 추천", "룰렛"]
NOISE_HARD = NOISE_HARD + GAMBLE_NOISE
# 시장조사 보고서 판매성 제목(언론사명이 달라도 걸러냄)
REPORT_NOISE = ["CAGR", "연평균 성장률", "시장 보고서", "시장조사 보고서", "시장 조사 보고서", "리서치 보고서", "market report", "market size", "market analysis", "market forecast"]
NOISE_HARD = NOISE_HARD + REPORT_NOISE
NOISE_SOFT = ["설명회", "포럼", "세미나", "워크숍", "개최", "성료", "간담회", "발대식", "캠페인"]

# 세미나·교육 홍보성 제목: "[10.30] ..." 처럼 날짜 말머리가 붙거나, 참가 모집·사전등록·수강 안내 같은 낱말이 있는 광고성 기사
SEMINAR_AD_RE = re.compile(r"^\s*\[\s*\d{1,2}\s*[./월]\s*\d{1,2}\s*(일)?\s*\]|참가\s*(자\s*)?(모집|신청)|사전\s*등록|수강\s*(생\s*)?(모집|신청)|교육\s*(과정\s*)?(안내|모집)|접수\s*(중|마감|시작)|얼리버드")

def noise_hits(t):
    """제목에 걸린 강한 노이즈 낱말. 사명이 있는 기사는 사진 기사만 노이즈로 인정(협약·MOU·인사 등은 면제)."""
    hits = [w for w in NOISE_HARD if w in t]
    if SEMINAR_AD_RE.search(t):
        hits.append("세미나광고")
    if has_company(t):
        hits = [w for w in hits if w in PHOTO_NOISE or w in GAMBLE_NOISE]
    return hits

# 사용자가 중점으로 보는 주제(제목에 있으면 크게 가산)
PRIORITY = ["도시가스 공급비용", "공급비용", "발전소 사고", "발전소 화재", "발전소 폭발", "불시정지", "발전정지", "발전기 고장", "집단에너지", "전력시장", "용량시장", "SMP", "PPA", "전력수급기본계획", "전기본", "용량입찰", "LNG 용량", "RPS", "태양광", "풍력", "열병합", "전력거래소", "도매가격", "LNG발전", "발전사업", "계통한계", "구역전기"]

# 정책·제도 기사(화면 맨 위에 노출) / 기술 기사(맨 아래로 후순위)
POLICY = ["정책", "제도", "개편", "개선안", "기본계획", "전기본", "전력수급기본계획", "용량시장", "용량입찰", "전력시장", "도매시장", "전기위원회",
          "산업부", "기후부", "기후에너지환경부", "국회", "정부", "법안", "개정", "시행령", "고시", "규제", "허가", "요금", "정산", "상한", "보조금",
          "지침", "공고", "탈탄소", "RPS", "PPA", "배출권", "계통", "집단에너지법", "전기사업법", "열병합"]
TECH = ["기술", "기술개발", "개발", "실증", "연구", "소재", "효율", "배터리", "전고체", "모듈", "인버터", "수소", "연료전지", "특허",
        "상용화", "시스템", "AI", "알고리즘", "플랫폼", "센서", "탠덤", "페로브스카이트", "준공", "착공"]

# 발전소 사고·정지(국내 설비 안전·공급 이슈) - 정책 기사와 같은 맨 위 구분으로 노출
ACCIDENT_RE = re.compile(r"(발전소|발전기|발전사|화력|열병합|복합|원전|가스터빈|변전소|송전).{0,12}(사고|화재|폭발|고장|불시정지|정지|가동\s*중단|누출|붕괴|정전)"
                         r"|(사고|화재|폭발|불시정지|정전).{0,8}(발전소|발전기|화력|열병합|원전|변전소)|블랙아웃")
# 해외 배출권(가격·시장) 기사: 맨 아래 구분(후순위)으로 보내고 점수도 깎는다. 국내(K-ETS) 기사는 해당 없음.
CARBON_RE = re.compile(r"배출권|탄소\s*크레딧|탄소\s*배출|탄소\s*시장|\bETS\b|\bEUA\b|CTWO|탄소\s*신탁|탄소\s*ETF")
OVERSEAS_RE = re.compile(r"유럽|\bEU\b|미국|중국|일본|영국|인도|글로벌|국제|달러|유로|위안|엔화|\$|€|\bUSD\b|\bEUR\b|신탁|\bETF\b|\bCTWO\b")

def overseas_carbon(t):
    return bool(CARBON_RE.search(t)) and bool(OVERSEAS_RE.search(t))

def tier(t):
    """-1=그룹 사업장 사명(최상단), 0=정책·사고, 1=일반, 2=기술(후순위), 3=해외 배출권(맨 아래)"""
    if has_company(t):
        return -1
    if overseas_carbon(t):
        return 3
    if ACCIDENT_RE.search(t) or any(w in t for w in POLICY):
        return 0
    if any(w in t for w in TECH):
        return 2
    return 1

# ---------------------------------------------------------------------------
# 기사 실제 발행일 검증: 구글 뉴스 날짜는 오래된 기사를 다시 수집하면 오늘 날짜로 바뀔 수 있어, 최종 후보는 원문 페이지의 발행일을 확인한다.
# 확인에 실패(접속 불가·날짜 없음)하면 구글 날짜를 믿고 그대로 둔다(기사가 통째로 사라지지 않게).
STALE_DAYS = 7        # 실제 발행일이 이보다 오래된 기사는 제외
VERIFY_BUDGET = 120   # 초. 검증에 쓰는 총 시간 상한(넘으면 나머지는 검증 생략)
_vstate = {"t0": None, "checked": 0}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

def decode_gnews(url):
    """구글 뉴스 암호화 링크 -> 원문 주소. 실패 시 None."""
    m = re.search(r"/articles/([^/?]+)", url)
    if not m:
        return None
    gid = m.group(1)
    page = None
    for base in ("https://news.google.com/articles/", "https://news.google.com/rss/articles/"):
        try:
            req = urllib.request.Request(base + gid, headers=UA)
            page = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
            sg = re.search(r'data-n-a-sg="([^"]+)"', page)
            ts = re.search(r'data-n-a-ts="([^"]+)"', page)
            if sg and ts:
                break
        except Exception:
            page = None
    else:
        return None
    if not page or not sg or not ts:
        return None
    inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
             '"X","X",1,[1,1,1],1,1,null,0,0,null,0],"%s",%s,"%s"]' % (gid, ts.group(1), sg.group(1)))
    body = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", inner]]])}).encode()
    req = urllib.request.Request("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body,
                                 headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
    arr = json.loads(raw.split("\n\n")[1])
    return json.loads(arr[0][2])[1]

DATE_PATS = [
    r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|og:article:published_time|article:published|pubdate|publishdate|date|dc\.date(?:\.issued)?|og:regDate|regDate|datePublished)["\'][^>]*content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name|itemprop)=["\'](?:article:published_time|og:article:published_time|article:published|pubdate|publishdate|date|dc\.date(?:\.issued)?|og:regDate|regDate|datePublished)["\']',
    r'"datePublished"\s*:\s*"([^"]+)"',
    r'"dateCreated"\s*:\s*"([^"]+)"',
]

def parse_pub_date(text):
    """'2006-03-02T10:00:00+09:00', '20060302100000', '2006.03.02 10:00' 등 -> aware datetime(KST) 또는 None"""
    t = (text or "").strip()
    m = re.match(r"(\d{4})[-./]?(\d{2})[-./]?(\d{2})(?:[T\s]?(\d{2}):?(\d{2}))?", t)
    if not m:
        return None
    y, mo, d, hh, mi = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), tzinfo=KST)
    except ValueError:
        return None

def article_date(html_text):
    for pat in DATE_PATS:
        for m in re.finditer(pat, html_text, re.I):
            dt = parse_pub_date(m.group(1))
            if dt and 2000 <= dt.year <= datetime.now(KST).year + 1:
                return dt
    return None

def is_stale(item, now):
    """실제 발행일이 STALE_DAYS 보다 오래되었으면 True. 확인 불가면 False."""
    if _vstate["t0"] is None:
        _vstate["t0"] = time.time()
    if time.time() - _vstate["t0"] > VERIFY_BUDGET:
        return False
    try:
        real = decode_gnews(item["link"])
        if not real or "news.google.com" in real:
            return False
        req = urllib.request.Request(real, headers=UA)
        raw = urllib.request.urlopen(req, timeout=10).read(400000)
        txt = raw.decode("utf-8", "ignore")
        dt = article_date(txt)
        if dt is None:
            txt = raw.decode("euc-kr", "ignore")
            dt = article_date(txt)
        _vstate["checked"] += 1
        if dt is None:
            return False
        if now - dt > timedelta(days=STALE_DAYS):
            print(f"   [날짜검증] 제외: 실제 발행 {dt:%Y-%m-%d} (구글 표시 {item['pub']:%Y-%m-%d}) {item['title'][:40]}")
            return True
    except Exception as e:
        print(f"   [날짜검증] 확인 실패({type(e).__name__}) - 구글 날짜 유지: {item['title'][:30]}")
    return False

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
    comp = has_company(t)
    core = sum(1 for w in CORE if w.lower() in t.lower()) + (3 if comp else 0)   # 사명이 있으면 관련도 필터를 통과
    tr = tier(t)
    pri = sum(1 for w in PRIORITY if w.lower() in t.lower())
    r = src_rank(g["item"]["source"])
    bonus = (len(FOCUS_ORDER) - r) if r < 99 else 0
    return (min(core, 4) * 3 + min(pri, 3) * 5 + bonus + (6 if tr <= 0 else -6 if tr in (2, 3) else 0) + (40 if comp else 0)
            + (4 if LNG_RE.search(t) else 0) + min(g["more"], 3) - 20 * len(noise_hits(t)) - 2 * sum(1 for w in NOISE_SOFT if w in t)), core

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

SEEN_DAYS = 3         # 최근 며칠간 이미 노출한 기사는 다시 올리지 않음(history 폴더 기준)

def load_seen(now):
    """오늘 이전 날짜의 history/news_YYYYMMDD.json 에 실린 기사 링크·제목을 모은다. 오늘 파일은 제외(같은 날 재실행 허용)."""
    import os, glob
    links, titles = set(), []
    today = now.strftime("%Y%m%d")
    for f in sorted(glob.glob("history/news_*.json"))[-(SEEN_DAYS + 1):]:
        day = os.path.basename(f)[5:13]
        if day >= today:
            continue
        try:
            for x in json.load(open(f, encoding="utf-8")).get("items", []):
                links.add(x.get("link"))
                n = norm(x.get("title", ""))
                if n:
                    titles.append((n, grams(n)))
        except Exception as e:
            print(f"   (history 읽기 실패 {f}: {type(e).__name__})")
    return links, titles

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
    # 사명은 맨 앞에 두어 시간 초과 시에도 건너뛰지 않게 하고, 정확히 일치하는 구문("...")으로 검색
    tasks = [("kw", c, '"' + c + '"', None) for c in COMPANIES]
    tasks += [("kw", k, k, None) for k in KEYWORDS]
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
                and hangul_ratio(r["title"]) >= 0.5 and not noise_hits(r["title"]) and not blocked_source(r["source"])]
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
    seen_links, seen_titles = load_seen(now)
    if seen_links or seen_titles:
        before = len(groups)
        groups = [g for g in groups if g["item"]["link"] not in seen_links
                  and not any(same(g["n"], pn, g["g"], pg) for pn, pg in seen_titles)]
        print(f">> [뉴스] 전일까지 노출된 기사 {before - len(groups)}건 제외")
    allg = list(groups)      # LNG 최소 확보용 전체 후보(전일 노출분 제외 후)
    # 시장 관련도(낱말 점수) + 여러 매체가 다룬 정도로 점수를 매겨 상위만 선택, 같으면 최신순
    main = [g for g in groups if score(g)[1] >= 1 and score(g)[0] > 0]
    # 주말·휴일처럼 기사가 적어 TOP_N 에 못 미치면, 에너지 관련 낱말은 있으나 점수가 낮아 탈락한 기사(후순위)로 채운다.
    # 채운 기사는 filler 표시를 달아 화면 맨 아래에 놓는다.
    if len(main) < TOP_N:
        rest = sorted([g for g in groups if g not in main and score(g)[1] >= 1],
                      key=lambda g: (score(g)[0], g["item"]["pub"].timestamp()), reverse=True)
        for g in rest[: TOP_N - len(main)]:
            g["filler"] = True
        main += rest[: TOP_N - len(main)]
        print(f">> [뉴스] 기사가 적어 후순위 기사로 {sum(1 for g in main if g.get('filler'))}건 보충")
    groups = main
    # 같은 기관(제목 맨 앞 낱말)이 주어인 기사가 한쪽으로 몰리지 않도록, 이미 뽑힌 수만큼 점수를 깎아 가며 한 건씩 선택
    def lead(g):
        m = re.search(r"[A-Za-z가-힣]{2,}", re.sub(r"\[[^\]]*\]", " ", g["item"]["title"]))
        return m.group(0)[:3] if m else ""
    top, cnt = [], {}
    # LNG 기사를 먼저 LNG_MIN 건까지 확보(점수 높은 순, 발행일 검증 통과분)
    lng_pool = sorted([g for g in allg if LNG_RE.search(g["item"]["title"]) and score(g)[1] >= 1],
                      key=lambda g: (score(g)[0], g["item"]["pub"].timestamp()), reverse=True)
    for g in lng_pool:
        if len(top) >= LNG_MIN:
            break
        if is_stale(g["item"], now):
            continue
        top.append(g)
        cnt[lead(g)] = cnt.get(lead(g), 0) + 1
    print(f">> [뉴스] LNG 기사 {len(top)}건 우선 확보(후보 {len(lng_pool)}건)")
    pool = [g for g in groups if g not in top]
    while pool and len(top) < TOP_N:
        best = max(pool, key=lambda g: (score(g)[0] - (0 if has_company(g["item"]["title"]) else 4 * cnt.get(lead(g), 0)), g["item"]["pub"].timestamp()))
        pool.remove(best)
        if is_stale(best["item"], now):
            continue
        top.append(best)
        cnt[lead(best)] = cnt.get(lead(best), 0) + 1
    # 화면 순서: 그룹 사업장 → 정책·사고 → 일반 → 기술, 같은 구분 안에서는 우선 언론사 순서 → 최신순
    top.sort(key=lambda g: (g.get("filler", False), tier(g["item"]["title"]), src_rank(g["item"]["source"]), -g["item"]["pub"].timestamp()))
    items = [{"title": g["item"]["title"], "source": g["item"]["source"], "link": g["item"]["link"],
              "pub": g["item"]["pub"].strftime("%Y-%m-%d %H:%M"), "more": g["more"]} for g in top]
    pins = [p for p in PINNED if now.strftime("%Y-%m-%d %H:%M") < p["until"]]
    for p in pins:   # 같은 기사가 자연 선별로 이미 들어 있으면 먼저 빼고, 지정 순위에 한 번만 넣는다
        pn = norm(p["title"]); pg = grams(pn)
        items = [x for x in items if x["link"] != p["link"] and not same(norm(x["title"]), pn, grams(norm(x["title"])), pg)]
    for p in sorted(pins, key=lambda p: p["rank"]):
        items.insert(min(max(p["rank"] - 1, 0), len(items)),
                     {"title": p["title"], "source": p["source"], "link": p["link"], "pub": p["pub"], "more": 0})
    items = items[:TOP_N]
    if pins:
        print(f">> [뉴스] 고정 기사 {len(pins)}건 반영")
    json.dump({"updated": now.strftime("%Y-%m-%d %H:%M"), "items": items},
              open("news.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    import os
    os.makedirs("history", exist_ok=True)                      # 날짜별 보관본 (같은 날 마지막 실행이 남음)
    day = now.strftime("%Y%m%d")
    json.dump({"updated": now.strftime("%Y-%m-%d %H:%M"), "items": items},
              open(f"history/news_{day}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f">> [뉴스] history/news_{day}.json 보관")
    print(f">> [뉴스] 발행일 검증 {_vstate['checked']}건 확인")
    print(f">> [뉴스] 수집 {len(allit)}건 -> 중복 제거 후 {len(groups)}건 -> 상위 {len(items)}건 저장")
    for i, x in enumerate(items, 1):
        print(f"   {i}. [{x['source']}] {x['title']} (같은 내용 +{x['more']})")

if __name__ == "__main__":
    main()
