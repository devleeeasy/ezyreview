# 브랜드 언급 파싱 — 답변 텍스트에서 brand_alias 매칭으로 언급 여부/순위/문맥 추출
#
# Vane 답변은 리스트형/줄글형이 매번 달라 XPath만으론 부족해 정규식 기반 텍스트
# 매칭으로 처리한다(스펙 3단계 주의사항). 브랜드 스코프 한정(같은 별칭이 다른
# 카테고리 브랜드와 겹치는 문제)은 이 함수 호출 전, brands 조회 시 category_id로
# 이미 좁혀서 넘기는 쪽에서 책임진다.
import re
from dataclasses import dataclass

CONTEXT_WINDOW = 40  # 매칭 지점 앞뒤로 포함할 문자 수(줄이 너무 길 때만 사용)
SHORT_LINE_LIMIT = 120  # 이보다 짧은 줄은 통째로 snippet으로 사용


@dataclass
class BrandRef:
    brand_id: int
    names: list[str]  # 브랜드명 + 별칭 전체 (매칭 후보)


@dataclass
class ParsedCitation:
    brand_id: int
    mentioned: bool
    mention_rank: int | None
    context_snippet: str | None


def _extract_snippet(text: str, start: int, end: int) -> str:
    """리스트형 답변("1. LG 그램 — ...")은 그 줄 전체를, 줄바꿈 없는 긴 줄글은
    매칭 지점 주변 CONTEXT_WINDOW만 잘라 반환한다. 단순 char 윈도우만 쓰면
    리스트 항목 사이 경계를 넘어가 다음 브랜드 문장까지 섞여 들어가는 문제가 있었음."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    if len(line) <= SHORT_LINE_LIMIT:
        return line
    ctx_start = max(line_start, start - CONTEXT_WINDOW)
    ctx_end = min(line_end, end + CONTEXT_WINDOW)
    return text[ctx_start:ctx_end].strip()


def parse_citations(answer_text: str, brands: list[BrandRef]) -> list[ParsedCitation]:
    """brands에 속한 모든 브랜드에 대해 언급 여부를 판정한다.
    mention_rank는 답변 텍스트 내 최초 등장 위치 순서(1부터), 미언급 브랜드는 None."""
    first_match: dict[int, tuple[int, str]] = {}  # brand_id -> (position, snippet)

    for brand in brands:
        best_pos: int | None = None
        best_snippet = ""
        for name in brand.names:
            m = re.search(re.escape(name), answer_text, re.IGNORECASE)
            if m and (best_pos is None or m.start() < best_pos):
                best_pos = m.start()
                best_snippet = _extract_snippet(answer_text, m.start(), m.end())
        if best_pos is not None:
            first_match[brand.brand_id] = (best_pos, best_snippet)

    rank_by_brand = {
        brand_id: rank + 1
        for rank, brand_id in enumerate(
            sorted(first_match, key=lambda bid: first_match[bid][0])
        )
    }

    results: list[ParsedCitation] = []
    for brand in brands:
        if brand.brand_id in first_match:
            results.append(
                ParsedCitation(
                    brand_id=brand.brand_id,
                    mentioned=True,
                    mention_rank=rank_by_brand[brand.brand_id],
                    context_snippet=first_match[brand.brand_id][1],
                )
            )
        else:
            results.append(
                ParsedCitation(
                    brand_id=brand.brand_id,
                    mentioned=False,
                    mention_rank=None,
                    context_snippet=None,
                )
            )
    return results
