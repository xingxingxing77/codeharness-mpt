"""联网搜索：源 `search_engine.py`(145) + `search_engine_serper.py`(119) + `search_engine_ddg.py`(94)
三件收敛成一件（判 `改`）。

不搬源那套 `SearchEngineType` 枚举 + `model_validator` 里 `importlib` 选包的工厂仪式：
本仓只有两个引擎、一个调用方（`tools.search_internet`），一个 if 就够。选择语义照源：
配了 `SEARCH__SERPER_API_KEY` 走 serper，否则退回免 key 的 DDG HTML 端点。

依赖只用已装的 requests + bs4。源 ddg 件顶层 `import duckduckgo_search`（本机未装），
所以此前 `search_internet` 里那个 langchain `DuckDuckGoSearchRun` 在这台机器上恒返回降级文案（实测）。
"""
import asyncio
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from codeharness.configs.settings import settings

SERPER_URL = "https://google.serper.dev/search"
DDG_URL = "https://html.duckduckgo.com/html/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


def _serper(query: str, max_results: int) -> list[dict]:
    """源 search_engine_serper.py：POST google.serper.dev，取 organic 的 title/link/snippet。"""
    body = {"q": query, "num": max_results}
    if settings.search.tbs:
        body["tbs"] = settings.search.tbs
    r = requests.post(SERPER_URL, json=body, timeout=15,
                      headers={"X-API-KEY": settings.search.serper_api_key,
                               "Content-Type": "application/json"})
    r.raise_for_status()
    return [{"title": i.get("title", ""), "link": i.get("link", ""), "snippet": i.get("snippet", "")}
            for i in r.json().get("organic", [])[:max_results]]


def _ddg(query: str, max_results: int) -> list[dict]:
    """免 key 路径：DDG 的 html 端点。源用 duckduckgo_search 包，这里用已装的 requests+bs4 直取。

    ponytail: 天花板是「端点改版即解析不到」，届时换 ddgs 包（真引擎，带重试）而不是加正则。
    """
    r = requests.post(DDG_URL, data={"q": query}, timeout=15, headers={"User-Agent": UA})
    r.raise_for_status()
    rows = []
    for block in BeautifulSoup(r.text, "html.parser").select(".result"):
        a = block.select_one("a.result__a")
        if not a:
            continue                                  # 广告/相关搜索块没有结果链接
        snippet = block.select_one(".result__snippet")
        rows.append({"title": a.get_text(" ", strip=True), "link": _unwrap(a.get("href", "")),
                     "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
        if len(rows) == max_results:
            break
    return rows


def _unwrap(href: str) -> str:
    """DDG 结果链是 `//duckduckgo.com/l/?uddg=<真链接>`，还原成目标 URL。"""
    return unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])


def engine() -> str:
    return "serper" if settings.search.serper_api_key else "ddg"


async def search(query: str, max_results: int = 8) -> list[dict]:
    """按配置选引擎，返回 [{title, link, snippet}]。失败抛异常——降级文案由调用方决定。

    requests 阻塞，必须进线程：这条跑在 FastAPI worker 里，占住事件循环等于卡死整个服务。
    """
    run = _serper if engine() == "serper" else _ddg
    return await asyncio.to_thread(run, query, max_results)
