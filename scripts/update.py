#!/usr/bin/env python3
"""Wales Tender Pipeline: daily update.

Pulls notices published or changed since the last run from the Find a Tender
API (and, best effort, the Sell2Wales API), keeps the ones for Wales or Welsh
buyers, and merges them into data/data.js. It writes data/data.js (what
the page loads), data/new.json (just the notices added today, a small
feed other tools can read) and data/state.json (run log).

Standard library only, so it runs on a plain GitHub Actions runner.

Usage:
  python scripts/update.py             # normal daily run
  python scripts/update.py --force     # run even if it already ran today
  python scripts/update.py --days 14   # look back 14 days instead of since last run
  python scripts/update.py --fixture f.json   # test with a saved OCDS release package
"""
import argparse, datetime as dt, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "data", "data.js")
STATE_JSON = os.path.join(ROOT, "data", "state.json")
NEW_JSON = os.path.join(ROOT, "data", "new.json")
LONDON = ZoneInfo("Europe/London")
FTS = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
S2W = "https://api.sell2wales.gov.wales/v1/Notices"
UA = "WalesTenderPipeline/1.0 (+https://github.com)"

# ----------------------------------------------------------------- places
LAS = ["Isle of Anglesey", "Gwynedd", "Conwy", "Denbighshire", "Flintshire", "Wrexham", "Ceredigion", "Pembrokeshire", "Carmarthenshire", "Swansea",
       "Neath Port Talbot", "Bridgend", "Vale of Glamorgan", "Cardiff", "Rhondda Cynon Taf", "Caerphilly", "Blaenau Gwent", "Torfaen", "Monmouthshire", "Newport", "Powys", "Merthyr Tydfil"]
NUTS = {"UKL11": ["Isle of Anglesey"], "UKL12": ["Gwynedd"], "UKL13": ["Conwy", "Denbighshire"], "UKL14": ["Carmarthenshire", "Pembrokeshire", "Ceredigion"],
        "UKL15": ["Merthyr Tydfil", "Rhondda Cynon Taf"], "UKL16": ["Torfaen", "Blaenau Gwent", "Caerphilly"], "UKL17": ["Bridgend", "Neath Port Talbot"],
        "UKL18": ["Swansea"], "UKL21": ["Monmouthshire", "Newport"], "UKL22": ["Cardiff", "Vale of Glamorgan"], "UKL23": ["Flintshire", "Wrexham"], "UKL24": ["Powys"]}
NUTS["UKL1"] = sum((NUTS[k] for k in ["UKL11", "UKL12", "UKL13", "UKL14", "UKL15", "UKL16", "UKL17", "UKL18"]), [])
NUTS["UKL2"] = sum((NUTS[k] for k in ["UKL21", "UKL22", "UKL23", "UKL24"]), [])
# ITL 2025 codes use TL instead of UK
NUTS.update({"TL" + k[2:]: v for k, v in list(NUTS.items())})
BUYER = [
    (r"Betsi Cadwaladr", ["Isle of Anglesey", "Gwynedd", "Conwy", "Denbighshire", "Flintshire", "Wrexham"]),
    (r"Hywel Dda", ["Carmarthenshire", "Pembrokeshire", "Ceredigion"]), (r"Swansea Bay", ["Swansea", "Neath Port Talbot"]),
    (r"Cwm Taf", ["Bridgend", "Rhondda Cynon Taf", "Merthyr Tydfil"]), (r"Aneurin Bevan", ["Blaenau Gwent", "Caerphilly", "Monmouthshire", "Newport", "Torfaen"]),
    (r"Cardiff and Vale", ["Cardiff", "Vale of Glamorgan"]), (r"Powys Teaching", ["Powys"]),
    (r"Isle of Anglesey|Ynys M[oô]n|Anglesey", ["Isle of Anglesey"]), (r"Gwynedd", ["Gwynedd"]), (r"Conwy", ["Conwy"]),
    (r"Denbighshire|Sir Ddinbych", ["Denbighshire"]), (r"Flintshire|Sir y Fflint", ["Flintshire"]), (r"Wrexham|Wrecsam", ["Wrexham"]),
    (r"Ceredigion", ["Ceredigion"]), (r"Pembrokeshire|Sir Benfro", ["Pembrokeshire"]), (r"Carmarthenshire|Sir Gaerfyrddin", ["Carmarthenshire"]),
    (r"Neath Port Talbot|Castell-nedd", ["Neath Port Talbot"]), (r"Swansea|Abertawe", ["Swansea"]), (r"Bridgend|Pen-y-bont", ["Bridgend"]),
    (r"Vale of Glamorgan|Bro Morgannwg", ["Vale of Glamorgan"]), (r"Cardiff|Caerdydd", ["Cardiff"]), (r"Rhondda", ["Rhondda Cynon Taf"]),
    (r"Caerphilly|Caerffili", ["Caerphilly"]), (r"Blaenau Gwent", ["Blaenau Gwent"]), (r"Torfaen", ["Torfaen"]), (r"Monmouthshire|Sir Fynwy", ["Monmouthshire"]),
    (r"Newport City|Casnewydd", ["Newport"]), (r"Powys", ["Powys"]), (r"Merthyr", ["Merthyr Tydfil"]),
    (r"Aberystwyth", ["Ceredigion"]), (r"Bangor University|Prifysgol Bangor", ["Gwynedd"]),
]
WELSH_BUYER = re.compile(r"Wales|Welsh|Cymru|Cyngor|Llywodraeth|Prifysgol|Coleg|Betsi|Hywel Dda|Aneurin|Cwm Taf|Velindre|Dwr Cymru|Dŵr|Trafnidiaeth|M-SParc|Adra|Cartrefi|Tai |"
                         + "|".join(p for p, _ in BUYER[7:]), re.I)
NOT_WELSH = re.compile(r"England and Wales|England & Wales|Newport Pagnell|Isle of Wight", re.I)
WELSH_POSTCODE = re.compile(r"^(CF|LL|NP|SA|LD)\d", re.I)

SECTOR_RULES = [("42511", "energy"), ("0933", "energy"), ("45321", "energy"), ("71314", "energy"), ("09", "energy"), ("65", "energy"),
                ("85312110", "education"), ("8011", "education"), ("80", "education"), ("85", "health"), ("33", "health"), ("98", "health"),
                ("34", "transport"), ("60", "transport"), ("63", "transport"), ("38", "research"), ("73", "research"), ("71315", "construction"), ("71355", "construction"),
                ("71", "professional"), ("72", "professional"), ("79", "professional"), ("66", "professional"), ("48", "professional"), ("64", "professional"),
                ("70", "professional"), ("75", "professional"), ("92", "professional"), ("45", "construction"), ("44", "construction"), ("50", "construction"),
                ("51", "construction"), ("90", "environment"), ("77", "environment"), ("03", "environment"), ("41", "environment")]


def sector(cpv):
    for c in cpv:
        for p, s in SECTOR_RULES:
            if c.startswith(p):
                return s
    return "supplies" if cpv else "professional"


def place(codes, buyer, buyer_region="", buyer_postcode=""):
    codes = [c.strip().upper() for c in codes if c]
    in_wales = any(c.startswith(("UKL", "TLL")) for c in codes)
    buyer_las = next((las for pat, las in BUYER if re.search(pat, buyer, re.I)), [])
    nuts_las = sorted({la for c in codes for la in NUTS.get(c, NUTS.get(c[:5], NUTS.get(c[:4], [])))}, key=LAS.index)
    las = ([la for la in nuts_las if la in buyer_las] or nuts_las) if nuts_las else buyer_las
    if len(set(las)) >= 22:
        las = []
    xb = in_wales and any(c and not c.startswith(("UKL", "TLL")) for c in codes)
    welsh_buyer = (bool(WELSH_BUYER.search(buyer)) and not NOT_WELSH.search(buyer)) or str(buyer_region).upper().startswith(("UKL", "TLL")) \
        or bool(WELSH_POSTCODE.match(str(buyer_postcode).strip()))
    # a Welsh buyer with no delivery place given is treated as Wales-wide
    return las, xb, (in_wales or (welsh_buyer and not codes) or (welsh_buyer and in_wales))


# ----------------------------------------------------------------- names
def nb(s):
    s = str(s).lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9ŵŷâêîôû]+", " ", s)
    s = re.sub(r"\b(the|limited|ltd|plc|cyf|cyfyngedig)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


ACR = {"NHS", "UK", "PLC", "CIC", "CBC", "BBC", "HMRC", "DVLA", "MOD", "NEUPC", "LUPC", "ESPO", "YPO", "JISC", "TFW", "LLP", "IT", "ICT", "UHB", "WAST", "PHW", "DWP",
       "HM", "CCS", "NWSSP", "RCT", "WRAP", "EE", "BT", "SSE", "SP", "DCWW", "EV", "CMS", "HR", "UKRI", "PFH", "APUC", "NI"}
SMALL = {"and", "of", "for", "the", "in", "on", "at", "to", "by", "a", "an"}


def smart_title(s):
    out = []
    for i, w in enumerate(s.split(" ")):
        core = re.sub(r"[^A-Za-z]", "", w)
        if core.upper() in ACR or (core and not re.search(r"[AEIOUY]", core.upper()) and len(core) <= 4):
            out.append(w.upper())
        elif i and w.lower() in SMALL:
            out.append(w.lower())
        else:
            out.append(re.sub(r"[A-Za-z]", lambda m: m.group().upper(), w.lower(), count=1))
    return " ".join(out)


BILING = [(r"^Cyngor Bwrdeistref Sirol Merthyr Tudful\W+", ""), (r"^Cyngor Sir Ddinbych\s+", ""), (r"^Llywodraeth Cymru\s+", ""),
          (r"^Cyngor Sir Ceredigion County Council$", "Ceredigion County Council"), (r"^National Highways National Highways$", "National Highways"),
          (r"^Cyngor Sir Ynys M[oô]n$", "Isle of Anglesey County Council")]


def tidy(name):
    s = re.sub(r"\s+", " ", str(name)).strip()
    s = re.sub(r"\s*\((?:lead authority|utility buyer|tfw|tfwrl|heiw|sbuhb|ccha|the|trading as [^)]*|hosted by [^)]*)\)", "", s, flags=re.I)
    s = re.sub(r"\(THE\)$", "", s).strip(" .,-")
    for p, r in BILING:
        s = re.sub(p, r, s, flags=re.I).strip()
    if " / " in s:
        left, right = s.split(" / ", 1)
        if re.search(r"\b(cyngor|llywodraeth|prifysgol|bwrdd|awdurdod|cyfoeth|comisiynydd|heddlu|amgueddfa|llyfrgell|trafnidiaeth|iechyd)\b", left, re.I):
            s = right.strip()
    s = re.sub(r"^[/\-–\s]+", "", s).strip()
    if s.isupper() and len(s) > 5:
        s = smart_title(s)
    return s


def split_joint(name):
    seen = []
    for p in re.split(r"\s*;\s*", str(name or "")):
        p = tidy(p)
        if p and nb(p) and nb(p) not in [nb(x) for x in seen]:
            seen.append(p)
    return seen or [tidy(name or "Unknown buyer")]


def tidy_supplier(s):
    s = re.sub(r"\s+", " ", str(s)).strip(" ;,")
    return smart_title(s) if s.isupper() and len(s) > 4 else s


# ----------------------------------------------------------------- flags
P_CRP = r"carbon reduction plan|\bcrp\b|ppn ?0?06|ppn ?0?6/21|wppn ?0?6|net[- ]?zero|decarboni[sz]|carbon footprint|carbon emission|scope 3|science[- ]based target|carbon literacy|cynllun lleihau carbon|sero net"
P_LC = r"low[- ]carbon|solar|photovoltaic|\bpv\b|heat pump|retrofit|insulation|ev charg|electric vehicle|charge ?points?|charging infrastructure|renewable|energy efficien|\bled lighting|led upgrade|district heat|hydrogen|wind turbine|battery storage|decarboni|net[- ]?zero|whole house|optimised retrofit|\bepc\b|zero emission|ynni adnewyddadwy|carbon isel"
P_CY = r"welsh language|cymraeg|bilingual|dwyieithog|safonau.r gymraeg|welsh speak|through the medium of welsh|in welsh and english|in english and welsh|\bin welsh\b"
LC_CPV = ("0933", "4526121", "4532", "42511110", "34144900", "71314300", "45261215", "09331", "09332")


def add_flags(x, text):
    t = (text or "").lower()
    if re.search(P_CRP, t): x["crp"] = 1
    if re.search(P_LC, t) or any(c.startswith(LC_CPV) for c in x.get("cpv", [])): x["lc"] = 1
    if re.search(P_CY, t): x["cyl"] = 1


# ----------------------------------------------------------------- helpers
def day(s):
    """'2026-10-14T12:00:00+01:00' -> '2026-10-14' (in UK time)."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo:
            d = d.astimezone(LONDON)
        return d.date().isoformat() if d.year < 2090 else None
    except Exception:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(s))
        return m.group(1) if m else None


def money(v):
    """returns (amount, includes_vat)"""
    if not isinstance(v, dict):
        return None, False
    for k, inc in (("amount", False), ("amountGross", True)):
        try:
            a = float(v.get(k))
            if a > 1:
                return round(a), inc
        except (TypeError, ValueError):
            pass
    return None, False


def short(s, k=300):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:k - 1] + "…" if len(s) > k else s


def get(url, tries=5):
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and i < tries - 1:
                wait = int(e.headers.get("Retry-After") or 10) + 1
                log(f"  {e.code} from server, waiting {wait}s")
                time.sleep(min(wait, 120))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            raise


LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


# ----------------------------------------------------------------- fetching
def fetch_fts(since, until):
    """Yield every release updated in the window, following the API's paging."""
    params = {"updatedFrom": since.strftime("%Y-%m-%dT%H:%M:%S"), "updatedTo": until.strftime("%Y-%m-%dT%H:%M:%S"), "limit": 100}
    url = FTS + "?" + urllib.parse.urlencode(params)
    pages = 0
    while url and pages < 400:
        pkg = get(url)
        pages += 1
        rels = pkg.get("releases") or []
        for r in rels:
            yield r
        nxt = (pkg.get("links") or {}).get("next")
        if nxt:
            url = nxt
        elif pkg.get("cursor") or pkg.get("nextCursor"):
            params["cursor"] = pkg.get("cursor") or pkg.get("nextCursor")
            url = FTS + "?" + urllib.parse.urlencode(params)
        else:
            url = None
        time.sleep(0.4)
    log(f"Find a Tender: {pages} page(s) read")


def fetch_s2w(months):
    """Best effort: Sell2Wales OCDS notices for the given 'mm-yyyy' months (contract notices, PINs and site tenders)."""
    for m in months:
        for nt in (1, 2, 51, 52):
            url = S2W + "?" + urllib.parse.urlencode({"dateFrom": m, "noticeType": nt, "outputType": 0, "locale": 2057})
            try:
                pkg = get(url, tries=2)
            except Exception as e:
                log(f"  Sell2Wales {m} type {nt}: skipped ({e})")
                continue
            rels = pkg.get("releases") or [r for rec in (pkg.get("records") or []) for r in (rec.get("releases") or [rec.get("compiledRelease") or {}])]
            for r in rels:
                if r:
                    r["_src"] = "Sell2Wales"
                    yield r


# ----------------------------------------------------------------- mapping
def notice_types(r):
    out = set()
    for d in ((r.get("planning") or {}).get("documents") or []) + ((r.get("tender") or {}).get("documents") or []):
        if d.get("noticeType"): out.add(str(d["noticeType"]).upper())
    for a in (r.get("awards") or []) + (r.get("contracts") or []):
        for d in a.get("documents") or []:
            if d.get("noticeType"): out.add(str(d["noticeType"]).upper())
    return out


def cpvs(tender):
    out = []
    def add(c):
        if isinstance(c, dict) and str(c.get("scheme", "CPV")).upper().startswith("CPV") and re.match(r"^\d{8}", str(c.get("id", ""))):
            cid = str(c["id"])[:8]
            if cid not in out: out.append(cid)
            if c.get("description"): CPV_DESC.setdefault(cid, c["description"])
    add(tender.get("classification"))
    for it in tender.get("items") or []:
        add(it.get("classification"))
        for c in it.get("additionalClassifications") or []: add(c)
    for lot in tender.get("lots") or []:
        add(lot.get("classification"))
    return out


def regions(r, tender):
    codes = []
    for it in tender.get("items") or []:
        for a in it.get("deliveryAddresses") or []:
            if a.get("region"): codes.append(a["region"])
    for a in tender.get("deliveryAddresses") or []:
        if isinstance(a, dict) and a.get("region"): codes.append(a["region"])
    for lot in tender.get("lots") or []:
        for a in lot.get("deliveryAddresses") or []:
            if a.get("region"): codes.append(a["region"])
    return codes


def party(r, role):
    return [p for p in r.get("parties") or [] if role in (p.get("roles") or [])]


CPV_DESC = {}
CLOSED_TENDER = {"cancelled", "withdrawn", "unsuccessful", "complete"}


def map_release(r, today):
    """Returns (notice or None, [awards], drop_notice: bool, is_wales: bool)."""
    tender = r.get("tender") or {}
    buyer_party = (party(r, "buyer") or [{}])[0]
    buyer_raw = (r.get("buyer") or {}).get("name") or buyer_party.get("name") or ""
    names = split_joint(buyer_raw)
    addr = buyer_party.get("address") or {}
    cpv = cpvs(tender)
    las, xb, welsh = place(regions(r, tender), buyer_raw, addr.get("region", ""), addr.get("postalCode", ""))
    if not welsh:
        return None, [], False, False
    ocid = r.get("ocid") or r.get("id")
    src = r.get("_src") or "Find a Tender"
    url = f"https://www.find-tender.service.gov.uk/procurement/{ocid}" if src == "Find a Tender" else ((tender.get("documents") or [{}])[0].get("url") or "https://www.sell2wales.gov.wales/search/search_mainpage.aspx")
    tags = set(r.get("tag") or [])
    nts = notice_types(r)
    status = str(tender.get("status") or "").lower()
    title = short(tender.get("title") or r.get("description") or "Untitled notice", 220)
    text = " ".join([title, str(tender.get("description") or ""), " ".join(str(l.get("title", "")) + " " + str(l.get("description", "")) for l in tender.get("lots") or [])])
    lots = tender.get("lots") or []
    base = dict(t=title, b=names[0], las=las, s=sector(cpv), cpv=cpv[:6], src=src, u=url)
    if len(names) > 1: base["jb"] = names[1:]
    if xb: base["xb"] = True
    cat = str(tender.get("mainProcurementCategory") or "").lower()
    if cat in ("goods", "services", "works"): base["cat"] = cat.capitalize()

    # ---- awards
    awards = []
    sup_parties = {p.get("id"): p for p in party(r, "supplier")}
    contracts = {str(c.get("awardID")): c for c in r.get("contracts") or []}
    for a in r.get("awards") or []:
        if str(a.get("status") or "active").lower() not in ("active", "pending"):
            continue
        c = contracts.get(str(a.get("id"))) or {}
        sups = [tidy_supplier(s.get("name")) for s in a.get("suppliers") or [] if s.get("name")]
        v, vinc = money(a.get("value"))
        if v is None: v, vinc = money(c.get("value"))
        per = c.get("period") or a.get("contractPeriod") or {}
        cs, ce = day(per.get("startDate")), day(per.get("endDate"))
        aw = dict(base, id=f"{ocid}-{a.get('id', '1')}", t=short(a.get("title") or title, 220), sup=sups[:12], v=v, ad=day(c.get("dateSigned") or a.get("date")),
                  cs=cs, ce=ce, cs_status="active" if (not ce or ce >= today) else "closed")
        aw.pop("src", None); aw["src"] = src
        scales = [((sup_parties.get(s.get("id")) or {}).get("details") or {}) for s in a.get("suppliers") or []]
        if any(str(x.get("scale", "")).lower() in ("sme", "micro", "small", "medium") for x in scales): aw["sme"] = True
        if any(x.get("vcse") for x in scales): aw["vcse"] = True
        if re.search(r"above threshold", str(tender.get("procurementMethodDetails") or ""), re.I): aw["above"] = True
        add_flags(aw, text)
        awards.append(aw)

    # ---- notice status
    if status in CLOSED_TENDER or "UK12" in nts or (tags & {"award", "contract", "awardUpdate", "contractUpdate"} and not status == "active"):
        return None, awards, True, True
    tp = tender.get("tenderPeriod") or {}
    milestones = (r.get("planning") or {}).get("milestones") or []
    comm = tender.get("communication") or {}
    future = day(comm.get("futureNoticeDate")) or day(tp.get("startDate"))
    eng = next((day(m.get("dueDate")) for m in milestones if str(m.get("type", "")).lower() == "engagement"), None)
    tech = tender.get("techniques") or {}
    if "UK13" in nts or tech.get("hasDynamicPurchasingSystem") or tech.get("hasDynamicMarket"):
        st, nd, ndt = "dps", day(tp.get("endDate")) or day(((lots[0] if lots else {}).get("contractPeriod") or {}).get("endDate")), "dpsend"
    elif status == "active" or "UK4" in nts or "tender" in tags:
        st, nd, ndt = "active", day(tp.get("endDate")), "submit"
    elif "UK3" in nts:
        st, nd, ndt = "planned", future, "publish"
    elif "UK2" in nts or eng:
        st, nd, ndt = "pme", eng or future, "engage"
    else:
        st, nd, ndt = "pipeline", future, "publish"
    v, vinc = money(tender.get("value"))
    n = dict(base, id=ocid, st=st, nd=nd, ndt=ndt, v=v, d=short(tender.get("description"), 300))
    if vinc: n["vinc"] = True
    lot0 = (lots[0] if lots else {}).get("contractPeriod") or tender.get("contractPeriod") or {}
    if lot0.get("startDate"): n["cs"] = day(lot0.get("startDate"))
    if lot0.get("endDate"): n["ce"] = day(lot0.get("endDate"))
    suit = [l.get("suitability") or {} for l in lots] + [tender.get("suitability") or {}]
    if any(s.get("sme") for s in suit): n["sme"] = True
    if any(s.get("vcse") for s in suit): n["vcse"] = True
    if tech.get("hasFrameworkAgreement"): n["tool"] = True
    if tender.get("procurementMethodDetails"): n["proc"] = short(tender["procurementMethodDetails"], 80)
    if len(lots) > 1: n["lots"] = len(lots)
    if re.search(r"consorti", text, re.I): n["consortia"] = True
    add_flags(n, text)
    return n, awards, False, True


# ----------------------------------------------------------------- merging
def toks(s):
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if len(w) > 2 and w not in {"and", "the", "for", "contract", "services", "works", "provision", "supply"}}


def same_title(a, b):
    A, B = toks(a), toks(b)
    if not A or not B: return False
    na, nb_ = set(re.findall(r"\d+", a)), set(re.findall(r"\d+", b))
    if na and nb_ and na != nb_ and not (na <= nb_ or nb_ <= na): return False
    if min(len(A), len(B)) < 3 and A != B: return False
    return len(A & B) / len(A | B) >= 0.6 or len(A & B) / min(len(A), len(B)) >= 0.85


def canon(name, index):
    """Use the spelling already in the data for the same buyer or supplier."""
    return index.get(nb(name), name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--fixture")
    ap.add_argument("--no-s2w", action="store_true")
    a = ap.parse_args()

    now_uk = dt.datetime.now(LONDON)
    today = now_uk.date().isoformat()
    state = json.load(open(STATE_JSON)) if os.path.exists(STATE_JSON) else {}
    if state.get("lastRunDate") == today and not a.force and not a.fixture:
        print(f"Already updated today ({today}); nothing to do. Use --force to run again.")
        return 0
    raw = open(DATA_JS, encoding="utf-8").read().strip()
    data = json.loads(raw[raw.index("=") + 1:].rstrip(";"))
    CPV_DESC.update(data.get("cpvDesc") or {})

    # window: from the last successful run (minus a day of overlap) to now
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if a.days:
        since = now_utc - dt.timedelta(days=a.days)
    elif state.get("lastRunUtc"):
        since = dt.datetime.fromisoformat(state["lastRunUtc"]) - dt.timedelta(days=1)
    else:
        since = now_utc - dt.timedelta(days=14)
    log(f"Run {now_uk:%Y-%m-%d %H:%M} UK · looking for notices updated since {since:%Y-%m-%d %H:%M} UTC")

    releases, ok_fts = [], True
    try:
        if a.fixture:
            pkg = json.load(open(a.fixture))
            releases = pkg.get("releases") or [r for rec in pkg.get("records", []) for r in rec.get("releases", [])]
        else:
            releases = list(fetch_fts(since, now_utc))
    except Exception as e:
        ok_fts = False
        log(f"Find a Tender API failed: {e}")
    log(f"Find a Tender: {len(releases)} release(s)")
    if not a.no_s2w and not a.fixture:
        months = sorted({since.strftime("%m-%Y"), now_utc.strftime("%m-%Y")})
        try:
            s2w = list(fetch_s2w(months))
            log(f"Sell2Wales: {len(s2w)} release(s)")
            releases += s2w
        except Exception as e:
            log(f"Sell2Wales skipped: {e}")

    # keep only the newest release per procurement
    latest = {}
    for r in releases:
        k = r.get("ocid") or r.get("id")
        if not k: continue
        if k not in latest or str(r.get("date", "")) >= str(latest[k].get("date", "")):
            latest[k] = r

    notices = {n["id"]: n for n in data["notices"]}
    awards = {x["id"]: x for x in data["awards"]}
    buyer_ix = {nb(x["b"]): x["b"] for k in ("notices", "awards", "tools") for x in data[k]}
    sup_ix = {nb(s): s for k in ("awards", "tools") for x in data[k] for s in x.get("sup", [])}
    added, updated, closed, new_awards, skipped = 0, 0, 0, 0, 0

    for ocid, r in latest.items():
        try:
            n, aws, drop, wales = map_release(r, today)
        except Exception as e:
            log(f"  could not read {ocid}: {e}")
            continue
        if not wales:
            skipped += 1
            continue
        for aw in aws:
            aw["b"] = canon(aw["b"], buyer_ix)
            aw["sup"] = [canon(s, sup_ix) for s in aw["sup"]]
            if aw["id"] not in awards:
                aw["fa"] = today
                new_awards += 1
            else:
                aw["fa"] = awards[aw["id"]].get("fa")
                if not aw["fa"]: aw.pop("fa")
            awards[aw["id"]] = {k: v for k, v in aw.items() if v not in (None, [], "")} | {"las": aw["las"], "sup": aw["sup"], "cpv": aw["cpv"]}
        if drop:
            if notices.pop(ocid, None): closed += 1
            continue
        if not n:
            continue
        n["b"] = canon(n["b"], buyer_ix)
        if "jb" in n: n["jb"] = [canon(j, buyer_ix) for j in n["jb"]]
        if ocid in notices:
            old = notices[ocid]
            if old.get("fa"): n["fa"] = old["fa"]
            updated += 1
        else:
            # same tender listed by both services: keep the existing one, add any missing details
            twin = next((o for o in notices.values() if nb(o["b"]) == nb(n["b"]) and o.get("nd") == n.get("nd") and same_title(o["t"], n["t"])), None)
            if twin:
                twin["las"] = sorted(set(twin["las"]) | set(n["las"]), key=LAS.index)
                if not twin.get("v") and n.get("v"): twin["v"] = n["v"]
                continue
            n["fa"] = today
            added += 1
        notices[ocid] = {k: v for k, v in n.items() if v not in (None, "")}

    # ---- housekeeping
    keep_after = (now_uk.date() - dt.timedelta(days=7)).isoformat()
    for k in list(notices):
        n = notices[k]
        if n.get("st") in ("active", "pme") and n.get("nd") and n["nd"] < keep_after:
            del notices[k]; closed += 1
    for x in awards.values():
        if x.get("ce") and x["ce"] < today: x["cs_status"] = "closed"
    for x in data.get("tools", []):
        if x.get("end") and x["end"] < today: x["st"] = "closed"
        elif x.get("start") and x["start"] > today: x["st"] = "upcoming"
        elif x.get("st") != "closed": x["st"] = "active"
    # drop awards that ended more than three years ago to keep the file small
    cutoff = (now_uk.date() - dt.timedelta(days=3 * 365)).isoformat()
    awards = {k: v for k, v in awards.items() if not (v.get("ce") and v["ce"] < cutoff)}

    data["notices"] = list(notices.values())
    data["awards"] = sorted(awards.values(), key=lambda x: x.get("ad") or "", reverse=True)
    data["cpvDesc"] = CPV_DESC
    if ok_fts:
        data["generated"] = today
        data["kind"] = "live"
    data["updated"] = now_uk.strftime("%Y-%m-%d %H:%M")
    data["newToday"] = added

    new = [n for n in data["notices"] if n.get("fa") == today]
    with open(NEW_JSON, "w", encoding="utf-8") as f:
        json.dump({"date": today, "count": len(new), "notices": new}, f, ensure_ascii=False, indent=1)
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("window.WTP_DATA=")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    summary = dict(added=added, updated=updated, closed=closed, newAwards=new_awards, notWales=skipped,
                   notices=len(data["notices"]), awards=len(data["awards"]), tools=len(data.get("tools", [])))
    log("Summary: " + ", ".join(f"{k} {v}" for k, v in summary.items()))
    if ok_fts and not a.fixture:
        state.update(lastRunUtc=now_utc.isoformat(timespec="seconds"), lastRunDate=today)
    state["lastSummary"] = summary
    state["log"] = LOG[-60:]
    with open(STATE_JSON, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("### Wales Tender Pipeline update\n\n" + "\n".join(f"- {m}" for m in LOG) + "\n")
    return 0 if ok_fts else 1


if __name__ == "__main__":
    sys.exit(main())
