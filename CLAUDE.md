# ezyreview — Claude Code 규칙

## 프로젝트 개요
이커머스 쇼핑몰의 주문 완료 웹훅을 수신하여 리뷰 요청 알림을 자동 발송하고,
AI로 리뷰를 분석하는 멀티테넌트 SaaS 백엔드.

---

## 기술 스택
- **API**: FastAPI (Python 3.11+)
- **태스크 큐**: Celery + Redis
- **DB**: PostgreSQL (멀티테넌트 DB 분리형)
- **ORM**: SQLAlchemy 2.0 (async)
- **AI**: OpenAI API
- **알림**: 카카오 알림톡 / SMTP
- **인프라**: Docker Compose

---

## 디렉토리 구조
```
ezyreview/
├── app/
│   ├── api/
│   │   ├── webhook.py       # 웹훅 수신
│   │   └── insights.py      # 인사이트 API
│   ├── core/
│   │   ├── db.py            # 테넌트 DB 동적 라우팅 (핵심)
│   │   ├── auth.py          # API 키 / JWT 인증
│   │   └── config.py
│   ├── models/
│   │   ├── main.py          # main_db 모델
│   │   └── tenant.py        # tenant_db 모델
│   └── schemas/
├── worker/
│   ├── tasks.py
│   ├── review_request.py
│   └── analytics.py
├── tests/
├── docker-compose.yml
├── .env.example
└── CLAUDE.md
```

### 확장 예정 (AI 인용 모니터링, `feature/ai-citation-monitor`)
전체 스펙은 `docs/ai-citation-monitor-extension.md` 참고 — 작업 시작 전 반드시 먼저 읽을 것.

기존 `app/`, `worker/` 구조를 그대로 유지하며 아래 위치에 신규 모듈을 추가한다.
별도 `backend/` 트리로 분리하지 않는다 — 이미 단일 백엔드 repo라 구분 실익이 없고,
기존 코드 전체를 옮기는 리네임 비용만 발생하기 때문.

```
app/
├── collectors/
│   └── ai_answer_collector.py   # Playwright + CDP, 셀프호스팅 Perplexica(Vane) 대상
├── models/
│   └── citation.py              # categories, brands, brand_alias, queries, citations
├── analysis/
│   └── citation_context.py      # 인용 문맥 감성 분석 (기존 OpenAI 연동 방식 재사용)
frontend/                        # 신규 최상위 디렉토리 — Next.js 대시보드, Vercel Root Directory 지정용
├── app/
└── components/
```

프론트엔드(`frontend/`)만 유일하게 신규 최상위 디렉토리로 추가한다 — Vercel 배포가
독립된 Root Directory를 요구하기 때문. Airflow DAG, Grafana, Perplexica(Vane) 등 운영/
크롤링 대상 서비스는 별도 Railway 서비스로 배포하며 이 repo의 코드 구조에는 편입하지 않는다.

**크롤링 대상은 실제 Perplexity가 아니라 셀프호스팅한 Perplexica(Vane, MIT 라이선스)다.**
Perplexity의 robots.txt가 `/search/new`, `/search*` 등 질의·응답 경로를 명시적으로 봇
차단하고 있어 실제 서비스를 자동화 대상으로 삼지 않기로 결정. 우리가 직접 운영하는
서버라 robots.txt/ToS 이슈가 발생하지 않으며, Playwright+CDP 자동화 기술 시연이라는
목적은 그대로 유지된다. Perplexica(Vane)는 실측 결과 WebSocket이 아니라 **SSE
(Server-Sent Events, `Content-Type: text/event-stream`)**로 스트리밍되므로, CDP에서
`Network.webSocketFrameReceived`가 아닌 **`Network.eventSourceMessageReceived`** 이벤트를
구독해 프레임을 수집한다.

Phase 1(고정 카테고리/브랜드 seed로 수집·분석 파이프라인 완성) → Phase 2(사용자가
카테고리/브랜드를 직접 등록하는 쓰기 API) 순으로 진행. Phase 2 전까지
`categories.parent_id` 등 미구현 확장 컬럼은 스키마에만 존재하고 로직은 만들지 않는다.

---

## 코딩 규칙

### 기본
- 모든 코드는 **Python 3.11+** 기준
- 타입 힌트 필수 — 모든 함수에 인자/반환 타입 명시
- Pydantic v2 사용 (v1 문법 금지)
- 비동기 우선 — DB, 외부 API 호출은 전부 `async/await`

### 네이밍
- 파일명: `snake_case`
- 클래스명: `PascalCase`
- 함수/변수: `snake_case`
- 상수: `UPPER_SNAKE_CASE`

### DB
- main_db: 테넌트 메타데이터만 저장
- tenant_db: 테넌트별 완전 격리 — `tenant_{id}_db` 형식
- DB 라우팅은 반드시 `app/core/db.py`의 `get_tenant_session()` 사용
- 직접 DB URL 하드코딩 금지

### API
- 웹훅 수신 응답은 200ms 이하 목표 — 무거운 로직은 Celery로 위임
- 웹훅 중복 방지: `order_id` 기준 Redis `SET NX TTL` 적용
- 모든 엔드포인트 Pydantic 스키마로 요청/응답 검증

### Celery
- 태스크 재시도: `max_retries=3`, exponential backoff
- 태스크 실패 시 반드시 DB에 실패 상태 기록

### 에러 처리
- 외부 API (알림톡, OpenAI) 호출은 전부 try/except
- HTTPException은 FastAPI 표준 방식 사용
- 로깅은 `logging` 모듈 사용 (print 금지)

---

## 멀티테넌시 핵심 규칙
- 테넌트 간 데이터 절대 혼용 금지
- API 키 → 테넌트 ID 추출 → 해당 DB 세션 생성 흐름 준수
- main_db 조회 결과는 Redis에 캐싱 (TTL 300초)

---

## 타임존
- 서버, DB, 모든 컨테이너 타임존은 **Asia/Seoul** 고정
- Docker Compose 모든 서비스에 `TZ=Asia/Seoul` 환경변수 추가
- PostgreSQL `timezone = 'Asia/Seoul'` 설정
- Python 코드 내 datetime은 항상 `Asia/Seoul` 기준
- 절대 UTC 기본값 그대로 사용 금지

---

## 포트폴리오 관점 주의사항
- 과도한 추상화 금지 — 읽기 쉬운 코드 우선
- 주석은 "왜"를 설명 — "무엇"은 코드로 표현
- 각 모듈 상단에 한 줄 역할 설명 주석 추가
- 실제로 동작하는 코드만 커밋 — 미완성 stub 금지

---

## Docker / 컨테이너 워크플로우
- 코드 변경 후 반드시 해당 서비스 **rebuild** — restart만 하면 변경사항 미반영
- 빌드 명령어: `docker compose build <service> && docker compose up -d <service>`
- 완료 선언 전 반드시 확인: 로그 확인, 엔드포인트 호출, 컨테이너 내 파일 검증
- Celery worker 코드 변경 시 worker 컨테이너도 반드시 rebuild

---

## 완료 전 검증 규칙
- "완료", "done" 선언 전 건드린 모든 컴포넌트 검증 필수
- 멀티파일 수정 시 (예: 필드명 변경) 구 이름으로 전체 repo grep 후 완료 선언
- 백엔드 수정 시 프론트엔드 연관 부분도 함께 확인
- 마이그레이션 필요 여부 항상 체크

---

## 구현 전 계획 확인
- 멀티스테이지 또는 스펙이 필요한 작업은 파일 생성 전 반드시 계획 확인
- 스펙 문서가 언급되면 먼저 읽고 시작
- 범위가 모호할 때는 2~3줄 bullet으로 접근 방식 확인 후 구현

---

## Git / PR 워크플로우
- `gh` CLI 미설치 — `gh pr create` 사용 금지
- PR 대신 GitHub compare URL 출력: `https://github.com/devleeeasy/ezyreview/compare/main...<branch>`
- 새 작업 단계 시작 전 반드시 새 브랜치 생성
- 브랜치명 불명확할 때는 사용자에게 확인 후 생성
- 신규 작업은 반드시 신규 브랜치 생성 후 커밋 — main 브랜치 직접 커밋 금지