# EzyReview 확장 개발문서
## AI 검색엔진 브랜드 인용 모니터링 기능 추가

**목적**: 기존 EzyReview(커머스 리뷰 수집·분석 플랫폼)에 Playwright/CDP/XPath 기반 동적 크롤링 모듈을 추가하여, 어센트코리아(Ascent AI) 데이터 스크래핑 개발자 포지션 자격요건을 실증하는 포트폴리오로 확장한다.

**리포지토리 전략**: 새 프로젝트가 아닌 **기존 EzyReview 리포 확장**. `feature/ai-citation-monitor` 브랜치에서 작업 후 `main`에 merge. 기존 `app/`, `worker/` 구조를 그대로 유지하고 별도 `backend/` 트리로 분리하지 않는다 — 이미 단일 백엔드 repo라 구분 실익이 없고 전체 리네임 비용만 발생하기 때문 (CLAUDE.md에 결정 반영됨).

**진행 순서**: **Phase 1(MVP, 고정 카테고리/브랜드로 파이프라인 완성)** → **Phase 2(사용자가 카테고리/브랜드를 직접 추가하는 제품화 확장)**. Phase 2를 염두에 두고 Phase 1의 스키마를 설계해, 나중에 다시 뜯는 일이 없도록 한다.

---

## 리포지토리 구조

```
ezyreview/
├── app/
│   ├── api/                          (기존 — webhook.py, insights.py 등)
│   ├── core/                         (기존 — db.py, auth.py, config.py)
│   ├── collectors/
│   │   └── ai_answer_collector.py    (신규 — Playwright + CDP)
│   ├── models/
│   │   ├── main.py                   (기존 — main_db)
│   │   ├── tenant.py                 (기존 — tenant_db)
│   │   └── citation.py               (신규 — categories, brands, brand_alias, queries, citations)
│   ├── analysis/
│   │   └── citation_context.py       (신규 — 기존 OpenAI 연동 방식 재사용)
│   └── schemas/                      (기존)
├── worker/                           (기존 — Celery 태스크: tasks.py, review_request.py, analytics.py 등)
├── frontend/                         (신규 — Next.js 대시보드, Vercel Root Directory 지정)
│   ├── app/
│   └── components/
├── dags/                             (신규 — Airflow DAG 정의)
└── README.md                         (v2 확장 섹션 하단에 이어붙임)
```

**배포 매핑**:

| 구성요소 | 배포처 | 비고 |
|---|---|---|
| FastAPI 백엔드 | Railway (서비스 1) | `/citations`, 기존 `/docs` |
| Airflow | Railway (서비스 2) | 스케줄링 전용, 관리자용 |
| Grafana | Railway (서비스 3) | 기술 모니터링 전용, 관리자용 |
| Postgres | Railway (서비스 4) | 내부 연결용 |
| Perplexica(Vane) | Railway (서비스 5) | 셀프호스팅 크롤링 대상, 내부 연결용 |
| Next.js 대시보드 | Vercel | `frontend/` 폴더만 배포, 공개용 |

---

# Phase 1 — MVP (고정 카테고리/브랜드, 파이프라인 완성)

## 0단계 — 사전 준비

- [x] 기존 EzyReview 리포 클론, 로컬 구동 확인
- [x] `feature/ai-citation-monitor` 브랜치 생성
- [x] 대상 AI 검색 서비스 선정 및 robots.txt / 이용약관 확인
  - 1차 후보: **Perplexity** → robots.txt 확인 결과 `/search/new`, `/search*` 등 자동화 대상 경로가 명시적으로 봇 접근 금지되어 있음 확인. 이용약관도 403으로 직접 열람 불가.
  - **판단**: robots.txt를 어기고 진행하는 건 리스크(IP 차단·계정 정지·ToS 위반) 대비 실익이 없고, 특히 이 채용공고 자격요건에 "데이터 수집 관련 법적·윤리적 이슈 이해"가 명시되어 있어 오히려 역효과. → **대상 전환**
  - **최종 대상: Perplexica(오픈소스 Perplexity 클론, 최근 Vane으로 리브랜딩, MIT 라이선스)를 직접 셀프호스팅**하여 그 화면에 자동화를 건다. 우리가 운영하는 서버이므로 robots.txt/ToS 이슈 자체가 발생하지 않음.
  - Perplexica 자체 API(3001 포트)가 있지만 의도적으로 사용하지 않음 — 이 프로젝트의 목적은 "API가 없는 실제 서비스(예: ChatGPT 등)를 가정한 수집 역량" 실증이므로, API가 있어도 웹 UI 자동화 경로를 선택. README/자소서에 이 판단 근거를 한 줄로 남길 것.
  - 셀프호스팅 특성상 실제 소비자가 보는 진짜 Perplexity 응답이 아닌 자체 구성한 데모 데이터라는 한계는 README에 명시.
- [x] Perplexica(Vane) Docker Compose로 로컬에 셀프호스팅 (SearXNG 내장, Chat Model: `gpt-4.1-mini`, Embedding Model: `text-embedding-3-small`)
- [x] 개발자도구(F12) Network 탭에서 직접 질의 던져보며 응답 형식 관찰
  - **실측 결과: WebSocket이 아니라 SSE(`Content-Type: text/event-stream`)로 스트리밍됨.** 사전 가정(WebSocket)은 틀렸음 — 2단계 CDP 구독 대상을 `Network.eventSourceMessageReceived`로 정정.

## 1단계 — DB 스키마 설계

**핵심 설계 원칙**: 브랜드는 카테고리에 종속된다. Phase 2에서 사용자가 카테고리를 만들 때 브랜드도 함께 입력하는 구조를 그대로 쓸 수 있도록, 처음부터 관계형으로 설계한다.

```
categories (id, tenant_id, name, parent_id[NULL], source[seed|user], created_at)
    │
    ├──< brands (id, category_id FK, name)
    │        │
    │        └──< brand_alias (id, brand_id FK, alias)
    │
    └──< queries (id, category_id FK, text, active)
                │
                └──< citations (id, tenant_id, query_id FK, brand_id FK,
                                 ai_source, response_raw, mentioned,
                                 mention_rank, context_snippet, collected_at)
```

- [x] 위 5개 테이블 정의 (`app/models/citation.py`, `TenantBase` 소속)
  - Alembic은 이 repo에 실제로 설정되어 있지 않음 확인 — 대신 기존 관행(`app/core/db.py`의 `create_tenant_db()` / `migrate_all_tenants()`가 `TenantBase.metadata.create_all` + idempotent `ALTER TABLE`로 시작 시 자동 마이그레이션)을 그대로 따름. 신규 테이블은 모델을 `TenantBase.metadata`에 등록하기만 하면 `create_all`이 자동으로 생성 — 별도 마이그레이션 파일 불필요.
  - `created_at`/`updated_at` 컨벤션: 모든 테이블에 `created_at` 포함. row 수정이 가능한 테이블(`categories`, `brands` — Phase 2 편집 대비, `queries` — `active` 토글)에는 `updated_at`도 추가. `brand_alias`(추가/삭제만, 수정 없음)와 `citations`(수집 즉시 불변 기록, `collected_at`이 사실상 created_at 역할)는 `updated_at` 제외.
- [x] 기존 `main_db`/`tenant_{id}_db` 분리 구조에 맞춰 반영 — tenant_db 소속, `tenant_id`는 `WeeklyReport`와 동일하게 FK 없는 bare 컬럼(DB 자체가 테넌트 경계라 cross-DB FK 불가)
- [ ] pgvector 컬럼 검토 (4단계 인용 맥락 임베딩용) — Phase 1 범위 아님, 4단계에서 진행
- [x] **seed 데이터 스크립트 작성** — `scripts/seed_citation_data.py` (아래 예시와 동일한 데이터)

> **TODO (미구현, 스키마만 예약)**: `categories.parent_id`는 나중에 카테고리 계층화(예: 전자제품 > 노트북)가 필요해질 때를 대비해 컬럼만 미리 넣어둔다. Phase 1/2 모두 `parent_id`는 항상 NULL로 두고, 계층 조회 로직·UI는 만들지 않는다. 카테고리가 10개 이상으로 늘어나 필터링 니즈가 생기면 그때 값 채우기 + 재귀 쿼리 + 대시보드 필터 UI를 별도 Phase로 진행.

**완료 기준**: 마이그레이션 적용 후 seed 데이터 insert, 카테고리→브랜드→질의 join 조회 정상 동작 — ✅ 확인 완료 (tenant_1_db, categories 1 / brands 5 / brand_alias 7 / queries 10)

### Seed 데이터 예시 (카테고리: 노트북)

```sql
INSERT INTO categories (name, source) VALUES ('노트북', 'seed');

INSERT INTO brands (category_id, name) VALUES
  (1, '삼성전자'), (1, 'LG전자'), (1, '애플'), (1, '레노버'), (1, '에이수스');

INSERT INTO brand_alias (brand_id, alias) VALUES
  (2, 'LG 그램'), (2, '그램'),
  (1, '갤럭시북'), (1, '삼성'),
  (3, '맥북'), (3, '맥북에어'), (3, '맥북프로');

INSERT INTO queries (category_id, text) VALUES
  (1, '출장용으로 가벼운 노트북 추천해줘'),
  (1, '대학생 인강 듣기 좋은 노트북 뭐가 있어?'),
  (1, '배터리 오래가는 노트북 뭐가 있을까'),
  (1, '개발자한테 좋은 노트북 추천'),
  (1, '100만원대 가성비 노트북 뭐가 좋아?'),
  (1, '영상 편집용 노트북 추천해줘'),
  (1, '무게 1kg 이하 노트북 있어?'),
  (1, '국내 AS 잘되는 노트북 브랜드는?'),
  (1, '맥북이랑 갤럭시북 중에 뭐가 나아?'),
  (1, '게이밍 겸용 가능한 노트북 추천');
```

두 번째 카테고리(청소기, 브랜드: 삼성전자·LG전자·다이슨·샤크·일렉트로룩스)도 `CATEGORIES` dict에 추가해 구현 완료. `python scripts/seed_citation_data.py [카테고리명]`으로 카테고리를 선택해 실행 (미지정 시 노트북).

## 2단계 — 수집 모듈 (Playwright + CDP)

- [ ] Playwright 브라우저 세션 기동, 셀프호스팅한 Perplexica(Vane) 인스턴스(`http://localhost:3000`) 접속
- [ ] `queries` 테이블에서 활성 질의 조회 → for loop 순회
- [ ] 페이지 접속 → 질의 입력창 자동 채움 → 전송
- [ ] CDP 세션 연결, `Network.enable` 후 **SSE 메시지 이벤트(`Network.eventSourceMessageReceived`) 구독** (Perplexica는 WebSocket이 아닌 SSE 스트리밍 — 0단계 실측으로 확인)
- [ ] 원본 이벤트 청크 누적 → 최종 응답 텍스트 재구성
- [ ] User-Agent 로테이션, 요청 간 랜덤 딜레이 적용 (자체 서버 대상이라 필수는 아니지만, 실제 서비스 대상 상황을 가정한 역량 실증 차원에서 구현)
- [ ] 실패/타임아웃 재시도 로직 (기존 "점진적 재시도 전략" 패턴 재사용)

**완료 기준**: 질의 1건에 대해 raw 응답 텍스트 확인 가능

## 3단계 — 파싱 모듈 (XPath / 텍스트 분석)

- [ ] DOM 렌더링 결과면 XPath로 리스트/카드 구조 파싱
- [ ] 순수 텍스트면 `brand_alias` 기반 키워드 매칭 (해당 브랜드의 category_id로 스코프 한정)
- [ ] `mention_rank` 계산 (텍스트 내 등장 순서)
- [ ] 결과를 `citations`에 저장 (query_id, brand_id 참조)

**응답 예시** (질의: "출장용으로 가벼운 노트북 추천해줘"):

> 1. **LG 그램** — 가벼운 무게로 국내에서 특히 출장용으로 인기
> 2. **맥북 에어** — 뛰어난 배터리 지속시간
> 3. **삼성 갤럭시북** — 국내 AS 접근성이 좋음

파싱 결과:

| brand | mentioned | mention_rank | context_snippet |
|---|---|---|---|
| LG전자 | true | 1 | "가벼운 무게로 국내에서 특히 출장용으로 인기" |
| 애플 | true | 2 | "뛰어난 배터리 지속시간" |
| 삼성전자 | true | 3 | "국내 AS 접근성이 좋음" |
| 레노버 | false | - | - |
| 에이수스 | false | - | - |

**완료 기준**: 브랜드 5개 × 질의 10개 조합에 대해 정확히 기록됨

**주의사항**:
- 답변 형식이 매번 다름(리스트/줄글) → XPath만으론 부족, 정규식 병행
- 브랜드 표기 변형은 `brand_alias`로 매칭
- AI 응답은 매번 달라질 수 있어 정기 반복 수집·추이 분석이 필요 (5단계 스케줄링 근거)

## 4단계 — 분석 모듈

- [ ] `citation_context.py` 작성 (기존 OpenAI 연동 방식 참고)
- [ ] 언급 문맥 긍정/중립/부정 분류 (OpenAI)
- [ ] pgvector로 유사 질의 클러스터링

**완료 기준**: 인용 건별 맥락 라벨 조회 가능

## 5단계 — 스케줄링 & 모니터링

- [ ] Airflow DAG 작성 — 매일 1회 전체 질의 세트(카테고리 무관 전체) 실행
- [ ] 실패 태스크 재처리(backfill)
- [ ] Grafana 대시보드 — 수집 성공률, 응답 지연, 카테고리별 인용 건수
- [ ] Google Chat Webhook 알림 (기존 패턴 재사용)

**완료 기준**: DAG 무인 실행, 실패 시 알림 확인

## 6단계 — 백엔드 배포 (Railway)

- [ ] `/citations`, `/categories` 조회 엔드포인트 추가
- [ ] Perplexica(Vane), Airflow, Grafana 별도 서비스로 배포
- [ ] Dockerfile에 `playwright install --with-deps` 반영

**완료 기준**: Railway 배포 후 API 정상 응답, Perplexica/Airflow/Grafana 정상 접속

## 7단계 — 프론트엔드 대시보드 (Vercel, 조회 전용)

- [ ] `frontend/`에 Next.js 생성, Vercel Root Directory 지정
- [ ] Railway API fetch 연동
- [ ] 카테고리 선택 드롭다운 (seed 카테고리 목록)
- [ ] 브랜드별 인용률 추이 라인 차트
- [ ] 최근 질의×답변 리스트, 브랜드 비교 뷰

**완료 기준**: 라이브 URL에서 실데이터 대시보드 확인 가능 → **여기까지가 Phase 1 완료, 이력서에 반영 가능한 상태**

---

# Phase 2 — 사용자 카테고리/브랜드 추가 기능 (제품화 확장)

Phase 1의 스키마를 그대로 사용. `categories.source`가 `seed`가 아닌 `user`로 들어가는 경로만 추가하는 개념.

## 8단계 — 카테고리 생성 API (Write Path)

- [ ] `POST /categories` — 카테고리명 + 브랜드 목록(최대 5개 제한) 동시 입력받아 생성
  - `brands` row 생성, `brand_alias`는 사용자가 직접 입력하거나 MVP에서는 생략(정확 매칭만)
- [ ] `POST /categories/{id}/queries` — 질의 세트 입력 (또는 기본 질의 템플릿 자동 제안)
- [ ] 입력값 검증 (브랜드 개수 제한, 중복 카테고리명 처리)

**완료 기준**: API로 새 카테고리+브랜드+질의 생성 후 DB에 정상 반영

## 9단계 — Rate Limit & 비동기 즉시 실행

- [ ] Redis에 세션/IP당 `last_triggered_at` 기록 → 트리거 빈도 제한 (예: 1일 1회)
- [ ] 카테고리 생성 즉시 수집을 트리거하되, 동기 대기 대신 **비동기 처리**
  - 기존 Celery 경험 재사용: 수집 작업을 태스크 큐에 등록
  - 완료 시 WebSocket(EzyFlow의 Redis pub/sub 브릿지 패턴 재사용) 또는 폴링으로 프론트에 알림
- [ ] 남용 방지를 위한 질의 개수/카테고리 개수 상한 정책

**완료 기준**: 사용자가 카테고리 생성 → 자동으로 큐잉되어 수집 실행 → 완료 시 알림, Rate Limit 정상 동작

## 10단계 — 프론트엔드 입력 폼

- [ ] Vercel 대시보드에 "카테고리 추가" 폼 (카테고리명, 브랜드 목록, 질의 목록 입력)
- [ ] 생성 후 진행 상태 표시 (수집 중 → 완료)
- [ ] 완료 시 결과 화면으로 자동 전환

**완료 기준**: 사용자가 폼 입력만으로 새 카테고리 결과를 확인 가능

---

## 11단계 — 문서화 & 이력서 반영 (Phase 1/2 공통 마무리)

- [ ] README에 아키텍처 다이어그램 추가 (Phase 1/2 구분 명시)
- [ ] 기존 README 하단에 "v2 확장" 섹션으로 이어붙이기
- [ ] GitHub PR/커밋 히스토리를 의미 단위로 정리
- [ ] 이력서 핵심역량의 "Selenium/Playwright 실습 중" 문구를 실제 성과로 교체
- [ ] 이력서 포트폴리오 구성:
  - **EzyReview (v2, 대표작)** — 상단, Phase 1+2 내용 포함 상세 서술
  - **EzyFlow** — 하단, 비동기·병렬 처리 역량 위주
  - 나머지 프로젝트 — GitHub 링크만 참고용
- [ ] 자소서 [직무수행역량]/[지원동기]에 성과 반영 검토

---

## 참고 — 실무와의 비교 (설계 근거)

- 실무 AI 가시성 트래커(OtterlyAI, Siftly 등)도 사전 정의된 프롬프트 세트를 자동 실행 → 응답 캡처 → 브랜드 언급 파싱, 동일 원리
- 실무는 공식 API가 있으면 우선 사용, 웹 UI 실제 경험과 다르거나 API가 없을 때만 브라우저 자동화 — 본 프로젝트는 그 케이스를 의도적으로 다룸(CDP 역량 실증)
- robots.txt가 명시적으로 자동화를 막는 서비스는 실제로 자동화하지 않고, 셀프호스팅 오픈소스 클론으로 대체 — 데이터 수집의 법적·윤리적 경계에 대한 판단 근거를 프로젝트 안에 남김
- 실무는 규모 확장 시 워커 풀 병렬화 + 프록시 로테이션 + 큐 기반 분배 추가 — 현재 스코프에선 불필요, 면접에서 구술 보완

---

## 참고 — 단계별 예상 소요

| 단계 | 예상 소요 |
|---|---|
| **Phase 1** | |
| 0단계 (준비) | 0.5일 |
| 1단계 (스키마+seed) | 0.5~1일 |
| 2단계 (Playwright+CDP 수집) | 2~3일 |
| 3단계 (파싱) | 1일 |
| 4단계 (분석) | 1일 |
| 5단계 (스케줄링/모니터링) | 1일 |
| 6단계 (Railway 배포) | 1일 |
| 7단계 (Vercel 대시보드-조회) | 1~1.5일 |
| **Phase 2** | |
| 8단계 (카테고리 생성 API) | 1일 |
| 9단계 (Rate Limit+비동기 트리거) | 1.5일 |
| 10단계 (입력 폼) | 1일 |
| 11단계 (문서화) | 0.5일 |
