"""노드 정의 테이블 (템플릿 §3.2 A 맵, §4.2 B 맵).

각 항목: (label, node_type, value_type, required, question[, extra]).
`question` 은 LLM 채움 프롬프트의 기준이며, 사람이 직접 채울 때도 그대로 보인다.
"""

from __future__ import annotations

from typing import Any, TypedDict


class NodeSpec(TypedDict, total=False):
    label: str
    node_type: str
    value_type: str
    required: bool
    question: str
    enum_options: list[str]
    unit: str
    formula: str
    meta_only: bool
    weight_key: str  # 프로파일에서 weight 를 가져올 키 (w_flow_tech / w_evidence)


def _s(label: str, node_type: str, value_type: str = "text", required: bool = False,
       question: str = "", **extra: Any) -> NodeSpec:
    d: NodeSpec = {"label": label, "node_type": node_type, "value_type": value_type,
                   "required": required, "question": question}
    d.update(extra)  # type: ignore[typeddict-item]
    return d


# --------------------------------------------------------------------------- A 신규 투자 맵
A_SPEC: dict[str, NodeSpec] = {
    "A0": _s("투자 논제", "judgment", "text", True,
             "이 종목이 왜, 언제까지, 얼마로 가는가를 한 문장으로. 시장이 아직 반영하지 않은 것은 무엇인가?"),
    "A0.1": _s("투자유형", "judgment", "enum", True, "어떤 유형의 베팅인가 (프로파일 선택)",
               enum_options=["growth", "value", "turnaround", "momentum", "event"]),
    "A0.2": _s("투자기간", "judgment", "enum", True, "논제가 실현되는 데 필요한 시간",
               enum_options=["3M", "1Y", "3Y+"]),
    "A0.3": _s("확신도", "judgment", "enum", True, "근거의 질과 양 기준 (H/M/L)", enum_options=["H", "M", "L"]),
    "A0.4": _s("현재가", "fact", "price", True, "작성 시점 종가", unit="원"),

    "A1": _s("핵심 드라이버", "question", "text", True,
             "논제의 성패를 가르는 변수 3~5개. 이 변수가 틀리면 논제가 무너지는가? 아니면 빼라."),
    "A1.1": _s("드라이버 1", "estimate", "number", True, "이 변수가 틀리면 논제가 무너지는가?"),
    "A1.2": _s("드라이버 2", "estimate", "number", True, "이 변수가 틀리면 논제가 무너지는가?"),
    "A1.3": _s("드라이버 3", "estimate", "number", True, "이 변수가 틀리면 논제가 무너지는가?"),

    "A2": _s("컨센서스 vs 내 뷰", "question", "text", True, "기대수익의 원천: 시장과 어디서 다르게 보는가"),
    "A2.1": _s("현재 주가에 내재된 가정", "estimate", "text", True,
               "현재 주가가 성립하려면 시장은 무엇을 믿고 있어야 하나?"),
    "A2.2": _s("내가 다르게 보는 것", "judgment", "text", True, "어디서 다르게 보고, 근거는 무엇인가?"),
    "A2.3": _s("시장이 틀린 이유", "judgment", "enum", True, "내가 맞다면 왜 시장은 아직 모르는가?",
               enum_options=["시간축", "정보", "심리", "구조"]),

    "A3": _s("밸류에이션 & 시나리오", "question", "text", True, "확률·목표가로 숫자화된 세 시나리오"),
    "A3.1": _s("Bear", "estimate", "price", True, "무엇이 무너지면 어디까지 떨어지나 (가정·확률·목표가)"),
    "A3.2": _s("Base", "estimate", "price", True, "논제가 그대로 실현되면 (가정·확률·목표가)"),
    "A3.3": _s("Bull", "estimate", "price", True, "무엇이 추가로 잘되면 (가정·확률·목표가)"),
    "A3.4": _s("기대수익률", "computed", "percent", False, "", formula="sum(p_i * (TP_i / P0 - 1))"),
    "A3.5": _s("손익비", "computed", "number", False, "",
               formula="((TP_base - P0) / P0) / ((P0 - TP_bear) / P0)"),
    "A3.6": _s("밸류 근거", "question", "text", False, "멀티플 숫자 하나가 아니라 맥락"),
    "A3.6.1": _s("역사 밴드 위치", "fact", "percent", True, "현재 멀티플이 자기 역사 분포의 몇 백분위에 있나?", unit="%ile"),
    "A3.6.2": _s("피어 비교", "fact", "text", False, "피어 대비 프리미엄/디스카운트와 그 정당성은?"),
    "A3.6.3": _s("EPS 리비전 방향", "fact", "enum", True, "최근 3개월 컨센서스 EPS 방향", enum_options=["up", "flat", "down"]),

    "A4": _s("리스크 & 무효화 조건", "question", "text", True, "무엇이 논제를 깨고, 그걸 어떻게 미리 알 수 있나"),
    "A4.1": _s("하락 리스크 1", "estimate", "range", True, "무엇이, 어느 확률로, 얼마나 깎는가? {probability, impact_pct, text}"),
    "A4.2": _s("하락 리스크 2", "estimate", "range", True, "무엇이, 어느 확률로, 얼마나 깎는가? {probability, impact_pct, text}"),
    "A4.3": _s("하락 리스크 3", "estimate", "range", False, "무엇이, 어느 확률로, 얼마나 깎는가? {probability, impact_pct, text}"),
    "A4.4": _s("Kill criteria", "question", "text", True,
               "어떤 사실이 확인되면 손실 크기와 무관하게 논제를 버리나? (가격이 아닌 사실 기준)"),
    "A4.4.1": _s("무효화 조건 1", "rule", "text", True, "사실 기준 조건식 → exit"),
    "A4.5": _s("조기 경보 지표", "fact", "text", False, "그 사실을 미리 알려주는 지표는? 어디서, 얼마나 자주 확인?"),

    "A5": _s("촉매 & 타임라인", "question", "text", False, "날짜 박힌 이벤트와 진입 타이밍"),
    "A5.1": _s("촉매 이벤트", "question", "text", False, "날짜가 박힌 사건만 (실적발표·정책·신제품·락업·계약)"),
    "A5.2": _s("수급/기술", "question", "text", False, "진입 타이밍용 — 프로파일 가중치", weight_key="w_flow_tech"),
    "A5.2.1": _s("추세", "fact", "enum", False, "", enum_options=["상승", "횡보", "하락"]),
    "A5.2.2": _s("거래량 (20일 평균 대비)", "fact", "number", False, "", unit="배"),
    "A5.2.3": _s("외국인/기관 순매수 (20일 누적)", "fact", "number", False, "", unit="억원"),
    "A5.2.4": _s("공매도/신용잔고 (유통주식 대비)", "fact", "percent", False, "", unit="%"),

    "A6": _s("실행 계획", "question", "text", True, "비중·진입·손절·익절·판단"),
    "A6.1": _s("목표 비중", "judgment", "percent", True, "제안식 → 확정. {suggested, confirmed, reason}", unit="%"),
    "A6.2": _s("진입 계획", "judgment", "range", True, "어느 가격대에서 몇 번에 나눠 사나? {price_range, tranches}"),
    "A6.3": _s("손절 규칙", "rule", "text", True, "가격 기준 하나 + 사실 기준 하나"),
    "A6.4": _s("익절/축소 규칙", "rule", "text", False, ""),
    "A6.5": _s("판단", "judgment", "enum", True, "BUY / WATCH / PASS", enum_options=["BUY", "WATCH", "PASS"]),

    "A7": _s("모니터링 & 복기", "question", "text", False, ""),
    "A7.1": _s("체크포인트", "computed", "date", False, "", formula="sorted(A1.x.next_check ∪ A5.1.x.date)"),
    "A7.2": _s("업데이트 로그", "event", "text", False, ""),
    "A7.3": _s("사후 복기", "judgment", "text", False, "매도 완료 후: 논제가 맞았나, 틀렸다면 어느 가정이"),

    "A8": _s("근거 자료", "question", "text", False, "논제를 뒷받침하는 것만. 기본 접힘", weight_key="w_evidence"),
    "A8.1": _s("기업", "question", "text", False, ""),
    "A8.1.1": _s("비즈니스 모델", "fact", "text", False, "무엇을 팔아 어떻게 돈을 버나?"),
    "A8.1.2": _s("경쟁우위", "fact", "text", False, "마진을 지키는 것은 무엇이고 5년 뒤에도 유효한가?"),
    "A8.1.3": _s("경영진 / 지배구조 / 주주환원", "fact", "text", False, "자본배분 이력, 대주주 리스크, 배당·자사주"),
    "A8.1.4": _s("재무 (성장·수익·현금·건전성)", "fact", "text", False, "매출/이익 성장률, 마진, FCF, 부채비율"),
    "A8.2": _s("산업 → 섹터 맵", "link", "ref", False, "S.<시장>-<섹터> 링크"),
    "A8.3": _s("매크로 → 매크로 맵", "link", "ref", False, "M0 링크"),
}


# --------------------------------------------------------------------------- B 보유 종목 맵
B_SPEC: dict[str, NodeSpec] = {
    "B0": _s("상태등", "computed", "enum", False, "", enum_options=["GREEN", "YELLOW", "RED"],
             formula="status.compute_light"),
    "B0.1": _s("오늘 현금으로 새로 사겠는가?", "judgment", "bool", True,
               "오늘 현금이 있다면 이 가격·이 비중에 이 종목을 새로 사겠는가?"),
    "B0.2": _s("보유 이유 (아니오일 때)", "judgment", "enum", False,
               "새로 사지 않을 종목을 왜 들고 있나? 정당한 이유는 세금·유동성·촉매 임박뿐",
               enum_options=["세금", "유동성", "촉매 임박", "없음"]),
    "B0.3": _s("현재 비중", "fact", "percent", True, "C1.1 에서 내려받음", unit="%"),
    "B0.4": _s("목표 비중", "judgment", "percent", True, "A6.1 스냅숏, 리밸런싱 시 갱신", unit="%"),
    "B0.5": _s("평가손익 (메타데이터)", "fact", "percent", False,
               "판단 입력 금지 — 세금·복기용", unit="%", meta_only=True),

    "B1": _s("매수 당시 논제 복원", "question", "text", True, "A 맵 스냅숏 또는 재구성 (불변)"),
    "B1.1": _s("원래 논제", "fact", "text", True, "A0 스냅숏"),
    "B1.2": _s("당시 드라이버 값", "fact", "range", True, "A1.x current → at_entry"),
    "B1.3": _s("당시 목표가/기간", "fact", "range", True, "A3 스냅숏"),
    "B1.4": _s("당시 무효화 조건", "question", "text", True, "A4.4 스냅숏"),
    "B1.5": _s("복원 품질", "judgment", "enum", True, "기록=A 맵 존재, 재구성=위자드로 사후 작성, 불가=논제 없음",
               enum_options=["기록", "재구성", "불가"]),
    "B1.6": _s("섹터·매크로 링크", "link", "ref", True, "A8.2 / A8.3 링크 복사 — C2 집계의 키"),

    "B2": _s("그 이후 무엇이 바뀌었나", "question", "text", True, "변화량이 본체"),
    "B2.1": _s("드라이버 (at_entry → now)", "question", "text", True,
               "at_entry 대비 now 가 내 추정에 가까워졌나 멀어졌나"),
    "B2.2": _s("주가 변화 분해", "computed", "range", False, "", formula="(1+dEPS)(1+dPE)-1 = r_total"),
    "B2.3": _s("논제 상태", "judgment", "enum", True,
               "강화/유효/약화/무효/변질. 변질 = 처음과 다른 이유로 들고 있음",
               enum_options=["강화", "유효", "약화", "무효", "변질"]),
    "B2.4": _s("사실 로그", "question", "text", False, "수집기가 자동 적재 (Event[])"),

    "B3": _s("앞으로의 기대수익", "question", "text", True, "과거 손익과 무관하게"),
    "B3.1": _s("Bear", "estimate", "price", True, "재추정"),
    "B3.2": _s("Base", "estimate", "price", True, "재추정"),
    "B3.3": _s("Bull", "estimate", "price", True, "재추정"),
    "B3.4": _s("기대수익률", "computed", "percent", False, "", formula="sum(p_i * (TP_i / P - 1))"),
    "B3.5": _s("손익비", "computed", "number", False, "", formula="((TP_base - P) / P) / ((P - TP_bear) / P)"),
    "B3.6": _s("밸류 상태", "computed", "enum", False, "", enum_options=["저평가", "적정", "고평가"],
               formula="band_percentile < low → 저평가; > high → 고평가"),
    "B3.7": _s("대안 비교", "question", "text", False, "현금 · 관심종목 1순위 · 섹터 대체 종목"),
    "B3.7.1": _s("현금", "link", "percent", False, "MMF 수익률", unit="%"),
    "B3.7.2": _s("관심종목 1순위", "link", "ref", False, "A 맵 ref + ER"),
    "B3.7.3": _s("섹터 대체 종목", "link", "ref", False, "S6 ref + ER"),
    "B3.8": _s("상대 우위", "computed", "percent", False, "", formula="ER_this - max(ER_alt)"),

    "B4": _s("포트폴리오 맥락", "question", "text", False, "C 에서 내려받음"),
    "B4.1": _s("비중 상태", "computed", "enum", False, "", enum_options=["과소", "적정", "과다"],
               formula="|w - w_target| > min(dev_abs, dev_rel * w_target)"),
    "B4.2": _s("평가익이 키운 비중", "computed", "percent", False, "", formula="price-driven share of weight increase"),
    "B4.3": _s("섹터/팩터 중복 · 상관", "fact", "text", False, "상위 보유종목과 상관"),
    "B4.4": _s("세금", "computed", "range", False, "", formula="{realized_gain, tax_rate, tax_amount, harvest_pair}"),
    "B4.5": _s("유동성 (분할 매도 일수)", "computed", "number", False, "", formula="qty / (ADV * participation)", unit="일"),

    "B5": _s("편향 체크 (보유자 전용)", "question", "text", True, "하나라도 true 면 결정 로그에 기록"),
    "B5.1": _s("매입가/본전에 앵커링?", "judgment", "bool", False, ""),
    "B5.2": _s("매몰비용?", "judgment", "bool", False, ""),
    "B5.3": _s("처분효과 (이익 종목만 팔고 싶은가)?", "judgment", "bool", False, ""),
    "B5.4": _s("보유효과 (안 갖고 있어도 이 뷰일까)?", "judgment", "bool", False, ""),
    "B5.5": _s("확증편향 (좋은 뉴스만 찾는가)?", "judgment", "bool", False, ""),
    "B5.6":_s("'본전 오면 판다'는 계획이 아님", "judgment", "bool", False, ""),

    "B6": _s("대응 결정", "judgment", "enum", True, "= f(논제 상태, 밸류 상태, 비중 상태). 매입가는 입력이 아님",
             enum_options=["add", "hold", "trim", "switch", "exit", "hedge"]),

    "B7": _s("사전 약속 (If-Then)", "question", "text", True, "사실 기준 규칙 1개 이상 필수"),

    "B8": _s("모니터링 & 결정 로그", "question", "text", False, ""),
    "B8.1": _s("정기 주기", "judgment", "text", False, "프로파일"),
    "B8.2": _s("이벤트 트리거", "judgment", "text", False, "가이던스 변경 · 경영진 교체 · 유상증자 · M&A · 지배구조"),
    "B8.3": _s("임계값", "judgment", "text", False, "비중 > __% · 밴드 이탈 · 드라이버 역전"),
    "B8.4": _s("결정 로그", "question", "text", False, "DecisionLog[] (HoldingMap.decision_log)"),
    "B8.5": _s("사후 복기", "judgment", "text", False, "exit 완료 시: 논제 적중 여부 · 틀린 가정 · 발생한 편향"),
}


# --------------------------------------------------------------------------- C / M / S (골격만)
C_SPEC: dict[str, NodeSpec] = {
    "C0": _s("포트폴리오", "computed", "range", False, "", formula="rollup"),
    "C1": _s("구성 & 집중도", "computed", "text"),
    "C1.1": _s("holdings[]", "computed", "range", False, "", formula="from B maps"),
    "C1.2": _s("상위 3종목 비중 합", "computed", "percent", False, "", formula="sum(top3 w)"),
    "C1.3": _s("HHI", "computed", "number", False, "", formula="sum(w_i^2)"),
    "C1.4": _s("최대 단일 비중 vs 상한", "computed", "range", False, "", formula="max(w) vs w_max"),
    "C2": _s("노출", "computed", "text"),
    "C2.1": _s("섹터별 비중", "computed", "range", False, "", formula="group by B1.6 sector link"),
    "C2.2": _s("팩터 노출", "computed", "range", False, "", formula="group by tags 팩터:*"),
    "C2.3": _s("통화 노출", "computed", "range", False, "", formula="group by tags 통화:*"),
    "C3": _s("대응 집계", "computed", "text"),
    "C3.1": _s("순매수 / 순매도", "computed", "range", False, "", formula="sum(B6 decision × amount)"),
    "C3.2": _s("실행 후 현금 비중", "computed", "percent", False, "", formula="cash + net_sell - net_buy"),
    "C3.3": _s("실행 순서", "computed", "text", False, "", formula="exit → trim → switch → add; 세금·유동성 고려"),
    "C3.4": _s("충돌 검사", "computed", "text", False, "", formula="cash shortfall; same-sector add+exit"),
    "C4": _s("리밸런싱 & 세금", "computed", "text"),
    "C4.1": _s("drift", "computed", "range", False, "", formula="w_now - w_target"),
    "C4.4": _s("손익통산 페어링", "computed", "range", False, "", formula="gain candidates × loss candidates"),
    "C5": _s("품질 지표", "computed", "text"),
    "C5.1": _s("논제 없는 보유", "computed", "range", False, "", formula="count/weight where B1.5 == 불가"),
    "C5.2": _s("stale 노드 비율", "computed", "percent"),
    "C5.3": _s("규칙 커버리지", "computed", "percent", False, "", formula="share of holdings with ≥1 B7 rule"),
    "C5.4": _s("포트 기대수익률", "computed", "percent", False, "", formula="sum(w_i × ER_i) + w_cash × ER_cash"),
    "C5.5": _s("평균 손익비", "computed", "number"),
    "C5.6": _s("매트릭스 오버라이드 빈도", "computed", "percent"),
    "C6": _s("복기", "computed", "text"),
    "C6.1": _s("결정 로그 통합", "computed", "range"),
    "C6.3": _s("편향 발생 빈도", "computed", "range"),
}

M_SPEC: dict[str, NodeSpec] = {
    "M0": _s("매크로 레짐", "judgment", "enum", True, "확장/둔화/침체/회복 × 리스크온/오프",
             enum_options=["확장", "둔화", "침체", "회복"]),
    "M1": _s("금리", "fact", "text", True, "기준금리(KR/US) · 10Y · 방향"),
    "M2": _s("환율", "fact", "text", True, "USDKRW · 방향 · 수출주/내수주 함의"),
    "M3": _s("유동성/경기", "fact", "text", False, "M2 · PMI · 경기선행지수 · 신용스프레드"),
    "M4": _s("정책/지정학", "question", "text", False, "Event[] — 날짜 박힌 것만"),
    "M5": _s("시장심리", "fact", "text", False, "VIX · 신용잔고 · 투자자예탁금 · 외국인 누적 순매수"),
    "M6": _s("레짐 변경 이력", "question", "text", False, "[{date, from, to, note}] — 변경 시 모든 B 맵 🟡 후보"),
}

S_SPEC: dict[str, NodeSpec] = {
    "": _s("섹터", "judgment", "text", True, "섹터 한 줄 요약"),  # 루트: S.<코드>
    "1": _s("TAM / 성장률", "fact", "text", True, ""),
    "2": _s("경쟁구도", "fact", "text", True, "상위 플레이어 · 점유율 · 진입장벽"),
    "3": _s("사이클 위치", "judgment", "enum", True, "", enum_options=["초기", "확장", "피크", "둔화", "바닥"]),
    "4": _s("섹터 밸류 밴드", "fact", "range", False, "PER/PBR 역사 분포 — A3.6.1·B3.6 의 비교 기준"),
    "5": _s("섹터 촉매/리스크", "question", "text", False, "Event[]"),
    "6": _s("섹터 내 종목 리스트", "question", "text", False, "[{A/B 맵 ref, ER, 상태}] — switch 후보 소스"),
}
