"""국가법령정보 공동활용 Open API(law.go.kr)로 법령명 사전을 갱신한다.

사용하려면 open.law.go.kr 에서 Open API 사용 신청 후 발급받은 OC 값이 필요하다.

    python main.py update-laws --oc <OC값>
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

API_URL = "https://www.law.go.kr/DRF/lawSearch.do"
PAGE_SIZE = 100
MAX_PAGES = 20


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def parse_law_search_xml(xml_bytes: bytes) -> tuple[int, list[dict]]:
    """lawSearch 응답 XML에서 (전체 건수, [{name, abbr}]) 를 추출한다."""
    root = ET.fromstring(xml_bytes)
    total = 0
    laws: list[dict] = []
    for elem in root.iter():
        tag = _local(elem.tag)
        if tag == "totalCnt" and (elem.text or "").strip().isdigit():
            total = int(elem.text.strip())
        elif tag == "law":
            fields = {_local(c.tag): (c.text or "").strip() for c in elem}
            name = fields.get("법령명한글", "")
            if name:
                laws.append({"name": name, "abbr": fields.get("법령약칭명", "")})
    return total, laws


def fetch_laws(oc: str, query: str, timeout: int = 30) -> list[dict]:
    """키워드 하나에 대한 법령 검색 결과 전체를 가져온다."""
    results: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        params = urllib.parse.urlencode({
            "OC": oc,
            "target": "law",
            "type": "XML",
            "query": query,
            "display": PAGE_SIZE,
            "page": page,
        })
        with urllib.request.urlopen(f"{API_URL}?{params}", timeout=timeout) as resp:
            total, laws = parse_law_search_xml(resp.read())
        results.extend(laws)
        if not laws or len(results) >= total:
            break
    return results


def update_law_dict(
    oc: str,
    dict_path: str,
    output_path: Optional[str] = None,
    extra_queries: Optional[list[str]] = None,
) -> int:
    """기존 사전의 검색어(+추가 검색어)로 API를 조회해 사전을 갱신한다.

    Returns:
        갱신 후 사전의 법령 수. 한 건도 가져오지 못하면 기존 파일을 건드리지 않고 -1.
    """
    data: dict = {"laws": [], "queries": [], "exclusions": []}
    if os.path.isfile(dict_path):
        with open(dict_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    queries = list(dict.fromkeys(list(data.get("queries", [])) + list(extra_queries or [])))
    if not queries:
        logger.error("검색어가 없습니다. --query 로 검색어를 지정하세요.")
        return -1

    merged: dict[str, dict] = {item["name"]: item for item in data.get("laws", []) if item.get("name")}
    fetched = 0
    for query in queries:
        try:
            laws = fetch_laws(oc, query)
        except Exception as e:
            logger.warning("법령 검색 실패 ('%s'): %s", query, e)
            continue
        logger.info("'%s': %d건", query, len(laws))
        fetched += len(laws)
        for law in laws:
            merged[law["name"]] = law

    if fetched == 0:
        logger.error("API에서 법령을 한 건도 가져오지 못했습니다. OC 값과 네트워크를 확인하세요.")
        return -1

    from datetime import date

    data["laws"] = sorted(merged.values(), key=lambda d: d["name"])
    data["queries"] = queries
    data["fetched"] = date.today().isoformat()
    data.setdefault("source", "국가법령정보센터(law.go.kr) 법령 검색 결과")

    target = output_path or dict_path
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    logger.info("법령명 사전 저장: %s (%d건)", target, len(data["laws"]))
    return len(data["laws"])
