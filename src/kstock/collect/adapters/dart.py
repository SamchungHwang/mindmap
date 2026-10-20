"""OpenDART 공시 어댑터 (스텁).

- 포털: https://opendart.fss.or.kr  — API 키 발급 후 환경변수 `DART_API_KEY`
- 주요 엔드포인트
    공시검색      GET https://opendart.fss.or.kr/api/list.json?crtfc_key=…&corp_code=…&bgn_de=YYYYMMDD&end_de=…
    고유번호      GET https://opendart.fss.or.kr/api/corpCode.xml  (zip, 종목코드 ↔ corp_code 매핑)
    단일회사 재무  GET https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?…&bsns_year=…&reprt_code=11011
- 구현 메모
    * corp_code 매핑은 corpCode.xml 을 받아 로컬 캐시(sqlite/json) 에 저장한다.
    * list.json 결과의 report_nm 이 RawDisclosure.title, rcept_no 로 원문 URL 을 만든다:
      https://dart.fss.or.kr/dsaf001/main.do?rcpNo=<rcept_no>
    * 사업/분기보고서의 영업이익률·수주잔고 같은 드라이버 값 추출은 llm.filler 가 한다 (수집기는 원문만).
"""

from __future__ import annotations

import os
from datetime import date

from ..base import DisclosureSource, NotConfigured, RawDisclosure

BASE = "https://opendart.fss.or.kr/api"


class DartDisclosureSource(DisclosureSource):
    def __init__(self, api_key: str | None = None, *, timeout: float = 10.0):
        self.api_key = api_key or os.environ.get("DART_API_KEY")
        self.timeout = timeout

    def _require(self) -> str:
        if not self.api_key:
            raise NotConfigured("DART_API_KEY 가 없습니다 (.env 참고)")
        return self.api_key

    def corp_code(self, ticker: str) -> str:
        """종목코드 → DART corp_code. corpCode.xml 캐시 구현 필요."""
        self._require()
        raise NotImplementedError("corpCode.xml 다운로드·캐시 후 매핑을 구현하세요")

    def list_disclosures(self, ticker: str, since: date) -> list[RawDisclosure]:
        key = self._require()
        # TODO: httpx.get(f"{BASE}/list.json", params={"crtfc_key": key, "corp_code": self.corp_code(ticker),
        #                 "bgn_de": since.strftime("%Y%m%d"), "end_de": date.today().strftime("%Y%m%d"),
        #                 "page_count": 100}, timeout=self.timeout)
        #       → [RawDisclosure(ticker, date.fromisoformat(r["rcept_dt"] 재포맷), r["report_nm"],
        #                        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}", source="DART")]
        raise NotImplementedError(f"DART list.json 호출 구현 필요 (key={'set' if key else 'none'})")


__all__ = ["DartDisclosureSource", "BASE"]
