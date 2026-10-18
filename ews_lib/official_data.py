"""Official UK macroeconomic sources: live pulls from public APIs.

    Bank of England Database   Official Bank Rate (IUDBEDR), CSV
    Office for National Statistics time series API
        MGSX  unemployment rate, UK, 16+, seasonally adjusted (LMS)
        YCNC…YCNN, ZSFB  ILO unemployment rate by region (LMS)
        D7G7  CPI annual rate, all items (MM23)
        KAI9  average weekly earnings, regular pay, 3-month average growth (LMS)
    HM Land Registry UK House Price Index (linked-data API)  annual change by region

The demo book itself runs on a synthetic macro scenario (so its history is
reproducible); these pulls show the connectors working against the real
services and land in `bronze.macro_official_series`. A client build would feed
them straight into the regional macro features.

Every call has a short timeout. The last good result per source is kept in
data/official_cache.json, so the demo still shows values when offline.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ews_lib.config import DATA_DIR, REGIONS

USER_AGENT = "EarlyWarningStudio/1.0 (demo; contact: data-engineering)"
TIMEOUT = 12
CACHE = DATA_DIR / "official_cache.json"
ONS = "https://www.ons.gov.uk"
OGL = "Open Government Licence v3.0"

ONS_REGION_CODES = {  # ILO unemployment rate, all, seasonally adjusted, LFS (dataset LMS)
    "North East": "ycnc", "North West": "ycnd", "Yorkshire and The Humber": "ycne", "East Midlands": "ycnf",
    "West Midlands": "ycng", "East of England": "ycnh", "London": "ycni", "South East": "ycnj", "South West": "ycnk",
    "Wales": "ycnm", "Scotland": "ycnn", "Northern Ireland": "zsfb",
}
HPI_SLUGS = {r: r.lower().replace(" and the ", "-and-the-").replace(" ", "-") for r in REGIONS}

SOURCES = [
    dict(id="boe_bank_rate", provider="Bank of England", dataset="Bank of England Database (IADB)", series="IUDBEDR",
         title="Official Bank Rate", unit="%", frequency="Daily", format="CSV", licence="Bank of England Database terms of use",
         feeds="bank_rate, rate_sensitive, refix_shock_fwd",
         url="https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom={from}&Dateto=now"
             "&SeriesCodes=IUDBEDR&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"),
    dict(id="ons_unemployment", provider="Office for National Statistics", dataset="Labour market statistics (LMS)", series="MGSX",
         title="Unemployment rate, UK, aged 16+, seasonally adjusted", unit="%", frequency="Monthly (rolling 3-month)", format="JSON",
         licence=OGL, feeds="unemp_rate (national anchor)",
         url=ONS + "/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms/data"),
    dict(id="ons_regional_unemployment", provider="Office for National Statistics", dataset="Labour market statistics (LMS)",
         series="YCNC … YCNN, ZSFB", title="ILO unemployment rate by region, seasonally adjusted", unit="%", frequency="Monthly (rolling 3-month)",
         format="JSON", licence=OGL, feeds="unemp_rate, unemp_chg_12m, util_x_unemp",
         url=ONS + "/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/{code}/lms/data"),
    dict(id="ons_cpi", provider="Office for National Statistics", dataset="Consumer price inflation (MM23)", series="D7G7",
         title="CPI annual rate, all items", unit="%", frequency="Monthly", format="JSON", licence=OGL,
         feeds="affordability stress in DSR and essential-spend scenarios",
         url=ONS + "/economy/inflationandpriceindices/timeseries/d7g7/mm23/data"),
    dict(id="ons_earnings", provider="Office for National Statistics", dataset="Labour market statistics (LMS)", series="KAI9",
         title="Average weekly earnings, regular pay, 3-month average growth", unit="%", frequency="Monthly", format="JSON",
         licence=OGL, feeds="income_chg_vs_6m benchmark, wage growth scenario",
         url=ONS + "/employmentandlabourmarket/peopleinwork/earningsandworkinghours/timeseries/kai9/lms/data"),
    dict(id="hmlr_ukhpi", provider="HM Land Registry", dataset="UK House Price Index (linked-data API)", series="UK HPI",
         title="House price annual change by region", unit="%", frequency="Monthly", format="JSON", licence=OGL,
         feeds="hpi_yoy (collateral and wealth effect)",
         url="https://landregistry.data.gov.uk/data/ukhpi/region/{slug}/month/{month}.json"),
]
BY_ID = {s["id"]: s for s in SOURCES}


def _get(url):
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/csv, */*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read()
        return body, r.status, r.headers.get("Content-Type", ""), int((time.time() - t0) * 1000)


def _ons_series(url):
    body, status, ctype, ms = _get(url)
    d = json.loads(body)
    obs = [dict(period=m["date"], value=float(m["value"])) for m in d.get("months", []) if m.get("value") not in (None, "")]
    desc = d.get("description", {})
    return dict(observations=obs[-24:], title_at_source=desc.get("title"), release_date=(desc.get("releaseDate") or "")[:10],
                next_release=desc.get("nextRelease"), http_status=status, content_type=ctype, latency_ms=ms,
                raw_excerpt=json.dumps(dict(description={k: desc.get(k) for k in ("title", "cdid", "datasetId", "unit", "releaseDate",
                                                                                     "nextRelease")},
                                            months=d.get("months", [])[-3:]), indent=2))


def _fetch_boe(src):
    start = (dt.date.today() - dt.timedelta(days=760)).strftime("%d/%b/%Y")
    url = src["url"].format(**{"from": start})
    body, status, ctype, ms = _get(url)
    text = body.decode("utf-8", "replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0] != "DATE"]
    obs = [dict(period=r[0], value=float(r[1])) for r in rows if len(r) > 1 and r[1]]
    monthly = {}
    for o in obs:  # keep the last observation of each month for the chart
        monthly[dt.datetime.strptime(o["period"], "%d %b %Y").strftime("%Y-%m")] = o
    last_change = next((o for a, o in zip(obs, obs[1:]) if a["value"] != o["value"]), None)
    changes = [o for a, o in zip(obs, obs[1:]) if a["value"] != o["value"]]
    return dict(url=url, observations=list(monthly.values())[-24:], latest=obs[-1] if obs else None,
                last_change=changes[-1] if changes else last_change, http_status=status, content_type=ctype, latency_ms=ms,
                raw_excerpt="\n".join(text.strip().splitlines()[:1] + text.strip().splitlines()[-4:]))


def _fetch_ons(src):
    out = _ons_series(src["url"])
    out["url"] = src["url"]
    out["latest"] = out["observations"][-1] if out["observations"] else None
    return out


def _fetch_regional(src):
    t0 = time.time()

    def one(item):
        region, code = item
        try:
            r = _ons_series(src["url"].format(code=code))
            last = r["observations"][-1]
            prev12 = r["observations"][-13] if len(r["observations"]) >= 13 else None
            return dict(region=region, code=code.upper(), period=last["period"], value=last["value"],
                        change_12m=round(last["value"] - prev12["value"], 2) if prev12 else None)
        except Exception as e:
            return dict(region=region, code=code.upper(), error=str(e)[:120])

    with ThreadPoolExecutor(max_workers=6) as pool:
        regions = list(pool.map(one, ONS_REGION_CODES.items()))
    good = [r for r in regions if "value" in r]
    if not good:
        raise RuntimeError("No regional series could be fetched")
    return dict(url=src["url"].format(code="ycnc"), regions=regions, latest=dict(period=good[0]["period"], value=None),
                latency_ms=int((time.time() - t0) * 1000), http_status=200, content_type="application/json",
                raw_excerpt=json.dumps(regions[:3], indent=2))


def _fetch_hpi(src):
    t0 = time.time()
    today = dt.date.today().replace(day=1)
    months = [(today - dt.timedelta(days=31 * k)).strftime("%Y-%m") for k in range(1, 5)]
    month, uk = None, None
    for m in months:  # the index is published with a lag of about two months
        try:
            body, status, ctype, ms = _get(src["url"].format(slug="united-kingdom", month=m))
            t = json.loads(body)["result"]["primaryTopic"]
            if t.get("percentageAnnualChange") is not None:
                month, uk = m, t
                break
        except Exception:
            continue
    if month is None:
        raise RuntimeError("UK HPI not available for recent months")

    def one(region):
        try:
            body, *_ = _get(src["url"].format(slug=HPI_SLUGS[region], month=month))
            t = json.loads(body)["result"]["primaryTopic"]
            return dict(region=region, period=month, value=t.get("percentageAnnualChange"), average_price=t.get("averagePrice"))
        except Exception as e:
            return dict(region=region, period=month, error=str(e)[:120])

    with ThreadPoolExecutor(max_workers=6) as pool:
        regions = list(pool.map(one, REGIONS))
    return dict(url=src["url"].format(slug="united-kingdom", month=month), regions=regions,
                latest=dict(period=month, value=uk.get("percentageAnnualChange"), average_price=uk.get("averagePrice")),
                latency_ms=int((time.time() - t0) * 1000), http_status=200, content_type="application/json",
                raw_excerpt=json.dumps({k: uk.get(k) for k in ("refRegion", "refMonth", "averagePrice", "percentageAnnualChange",
                                                                "percentageChange", "housePriceIndex", "salesVolume")}, indent=2, default=str))


FETCHERS = {"boe_bank_rate": _fetch_boe, "ons_unemployment": _fetch_ons, "ons_regional_unemployment": _fetch_regional,
            "ons_cpi": _fetch_ons, "ons_earnings": _fetch_ons, "hmlr_ukhpi": _fetch_hpi}


def _load_cache():
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


_CACHE_LOCK = threading.Lock()


def _cache_put(source_id, result):
    """Merge one result into the cache. Parallel fetches finish together, so the read-modify-write is locked."""
    with _CACHE_LOCK:
        cache = _load_cache()
        cache[source_id] = result
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = CACHE.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(cache, indent=1, default=str))
            for attempt in range(5):  # Windows refuses the rename while another process is reading the file
                try:
                    tmp.replace(CACHE)
                    break
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))
            else:
                tmp.unlink(missing_ok=True)
        except Exception:
            pass


def fetch(source_id: str) -> dict:
    """Live pull of one source. On failure, returns the last good result marked as cached."""
    src = BY_ID[source_id]
    fetched_at = dt.datetime.now().isoformat(timespec="seconds")
    try:
        res = FETCHERS[source_id](src)
        out = dict(id=source_id, ok=True, live=True, fetched_at=fetched_at, **res)
        _cache_put(source_id, out)
        return out
    except Exception as e:
        prev = _load_cache().get(source_id)
        if prev:
            return dict(prev, live=False, error=f"{type(e).__name__}: {str(e)[:160]}", attempted_at=fetched_at)
        return dict(id=source_id, ok=False, live=False, error=f"{type(e).__name__}: {str(e)[:160]}", attempted_at=fetched_at)


def fetch_all() -> dict:
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        results = list(pool.map(fetch, [s["id"] for s in SOURCES]))
    return {r["id"]: r for r in results}


def cached() -> dict:
    return _load_cache()


def to_frame(results: dict):
    """Long table for bronze.macro_official_series: one row per source, region and period."""
    import pandas as pd
    rows = []
    for sid, r in results.items():
        if not r.get("ok"):
            continue
        src = BY_ID[sid]
        base = dict(source_id=sid, provider=src["provider"], dataset=src["dataset"], series=src["series"], title=src["title"],
                    unit=src["unit"], licence=src["licence"], fetched_at=r.get("fetched_at"), live_pull=bool(r.get("live")), url=r.get("url"))
        if r.get("regions"):
            for g in r["regions"]:
                if g.get("value") is not None:
                    rows.append(dict(base, region=g["region"], period=g["period"], value=g["value"]))
        for o in r.get("observations") or []:
            rows.append(dict(base, region="United Kingdom", period=o["period"], value=o["value"]))
        if not r.get("observations") and r.get("latest") and r["latest"].get("value") is not None:
            rows.append(dict(base, region="United Kingdom", period=r["latest"]["period"], value=r["latest"]["value"]))
    return pd.DataFrame(rows)
