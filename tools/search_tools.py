import httpx
from tools.file_tools import write_file
from datetime import datetime
import os
import re
from urllib.parse import quote_plus

WORKSPACE = os.getenv('WORKSPACE_DIR', '/workspace')

# Attempt to import ddg from duckduckgo_search; provide a fallback if unavailable
try:
    from duckduckgo_search import ddg as ddg_search
except Exception:
    ddg_search = None

# Try to import BeautifulSoup for better fallback parsing
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None


def _fallback_ddg(query: str, max_results: int = 5):
    """Fallback DuckDuckGo scraping if duckduckgo_search package doesn't expose ddg."""
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        r = httpx.get(url, timeout=15)
        r.raise_for_status()
    except Exception:
        return []
    text = r.text
    results = []
    if BeautifulSoup:
        soup = BeautifulSoup(text, 'html.parser')
        # DDG result anchors often have class 'result__a'
        for a in soup.select('a.result__a'):
            href = a.get('href')
            title = a.get_text().strip()
            if href and title:
                results.append({'title': title, 'href': href})
            if len(results) >= max_results:
                break
        return results
    # crude regex fallback
    for m in re.finditer(r'<a[^>]*class=["\']result__a["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.S|re.I):
        href = m.group(1)
        title = re.sub(r'<.*?>','', m.group(2)).strip()
        results.append({'title': title, 'href': href})
        if len(results) >= max_results:
            break
    return results


def web_search(query: str, max_results: int = 5):
    if ddg_search:
        try:
            return ddg_search(query, max_results=max_results)
        except Exception:
            return _fallback_ddg(query, max_results=max_results)
    else:
        return _fallback_ddg(query, max_results=max_results)


def fetch_url(url: str) -> str:
    resp = httpx.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def save_research_report(title: str, content: str):
    now = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', title)[:40]
    filename = f"research/{now}_{slug}.md"
    write_file(filename, content)
    return filename
