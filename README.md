# ezyreview

이커머스 쇼핑몰의 주문 완료 이벤트를 수신하여 리뷰 요청 알림을 자동 발송하고, AI로 수집된 리뷰를 분석하는 멀티테넌트 SaaS 백엔드입니다.

이커머스 쇼핑몰 판매자가 API 키 하나로 연동하면, 리뷰 수집부터 감성 분석·인사이트 제공까지 자동화됩니다.

---

## 만든 이유

실무에서 멀티채널 이커머스 자동화 플랫폼(OMS)을 9년간 운영하며, 판매자가 리뷰 관리에 들이는 수작업 비용이 크다는 것을 반복적으로 목격했습니다.

리뷰 요청 타이밍을 놓치거나, 부정 리뷰의 패턴을 파악하지 못하거나, 수천 개의 리뷰를 수동으로 분류하는 작업이 반복됐습니다.


이 프로젝트는 그 문제를 백엔드 관점에서 직접 설계하고 구현한 결과물입니다.

---

## 시스템 아키텍처

```
[이커머스 쇼핑몰]
      │ 주문완료 웹훅 (POST /webhook/{api_key})
      ▼
[FastAPI — 웹훅 수신 & 테넌트 인증]
      │ 테넌트 검증 → main_db 조회
      │ Celery 태스크 발행
      ▼
[Redis — 메시지 브로커]
      │
      ├──────────────────────────┐
      ▼                          ▼
[review_request_task]     [analytics_task]
 리뷰 요청 알림 발송        리뷰 AI 분석 (비동기)
 (알림톡 / 이메일)
      │                          │
      ▼                          ▼
[tenant_{id}_db]          [tenant_{id}_db]
 reviews, orders           review_analytics
 notifications
      │
      ▼
[FastAPI — 인사이트 API]
 판매자 대시보드용 집계 API
```

**설계 원칙**

- 웹훅 수신과 비즈니스 로직은 Celery로 분리 — API 응답 지연 없음
- 테넌트별 완전 격리 DB — 데이터 유출 구조적 차단
- AI 분석은 비동기 처리 — 리뷰 요청 발송 지연 없음

---

## 기술 스택

| 계층 | 기술 | 선택 이유 |
|------|------|-----------|
| API 서버 | FastAPI | 비동기 처리, 타입 안정성, 빠른 개발 속도 |
| 태스크 큐 | Celery + Redis | 웹훅 수신과 처리 분리, 재시도 전략 내장 |
| DB | PostgreSQL | 멀티테넌트 DB 분리 운영, 트랜잭션 신뢰성 |
| ORM | SQLAlchemy 2.0 | 동적 DB 라우팅 구현 |
| AI 분석 | OpenAI API | 감성 분석, 키워드 추출, 리뷰 요약 |
| 알림 | 카카오 알림톡 | 리뷰 요청 자동 발송 |
| 인프라 | Docker Compose | 로컬 개발 환경 일관성 |

---

## 멀티테넌시 전략

```
main_db
├── tenants          (테넌트 정보, API 키, 플랜)
└── webhook_logs     (수신 이력, 디버깅용)

tenant_{id}_db       (테넌트별 완전 격리)
├── orders           (주문 정보)
├── reviews          (수집된 리뷰)
├── notifications    (발송 이력)
└── review_analytics (AI 분석 결과)
```

**왜 DB 분리형을 선택했나**

| 전략 | 장점 | 단점 |
|------|------|------|
| DB 분리 (채택) | 완전한 데이터 격리, 테넌트별 백업/이전 용이 | DB 커넥션 수 증가 |
| 스키마 분리 | 커넥션 공유 가능 | PostgreSQL 스키마 관리 복잡 |
| Row-level | 단순한 구조 | 쿼리마다 tenant_id 필터 필수, 실수 시 전체 노출 위험 |

대형 유통사 포함 멀티테넌트 환경을 DB 분리형으로 운영한 경험을 바탕으로 선택했습니다.

---

## 전체 데이터 흐름

### 1. 웹훅 수신 → 태스크 발행

```
POST /webhook/{api_key}
  │
  ├── API 키 검증 (main_db 조회, Redis 캐시 활용)
  ├── 요청 중복 체크 (order_id 기준, Redis SET NX)
  ├── webhook_logs 기록
  └── Celery 태스크 발행
        └── review_request_task (즉시 — 알림톡 발송)
```

### 2. 리뷰 요청 알림 발송

```
review_request_task
  │
  ├── Gmail SMTP로 리뷰 요청 이메일 발송
  ├── notifications 테이블 기록 (sent / failed)
  └── 실패 시 자동 재시도 (max_retries=3, exponential backoff)
```

### 3. 리뷰 수집

```
POST /reviews  (X-Api-Key 인증)
  │
  ├── 주문 존재 여부 확인
  ├── 중복 리뷰 방지
  ├── reviews 테이블 저장
  └── analytics_task 즉시 발행 → AI 분석 시작
```

### 4. 리뷰 AI 분석 (Celery beat — 매일 새벽 2시 + 리뷰 등록 즉시)

```
nightly_analytics_task
  │
  └── batch_analytics_task (테넌트별)
        │
        └── analytics_task (리뷰별)
              ├── OpenAI gpt-4o-mini 호출
              │     ├── 감성 분석 (긍정 / 부정 / 중립)
              │     ├── 키워드 추출 (상위 3개)
              │     └── 한줄 요약
              └── review_analytics 저장
```

### 5. 인사이트 API 응답

```
GET /insights/summary
  │
  ├── X-Api-Key 헤더 인증 (Redis 캐시 → main_db fallback)
  ├── tenant_{id}_db 집계 쿼리 (GROUP BY로 단일 쿼리 최적화)
  │     ├── 전체 리뷰 수 + 평균 평점
  │     └── 감성 분포 (positive / negative / neutral / unanalyzed)
  └── 응답 반환
```

---

## 주요 설계 포인트

**1. 웹훅 중복 수신 방지**

쇼핑몰 플랫폼은 네트워크 이슈 시 웹훅을 재전송합니다. `order_id` 기준으로 Redis `SET NX TTL`을 적용해 동일 주문의 중복 처리를 차단합니다.

**2. 테넌트 DB 동적 라우팅**

SQLAlchemy `Session`을 요청 컨텍스트에서 동적으로 생성합니다. API 키에서 테넌트 ID를 추출 → 해당 테넌트 DB 커넥션을 반환하는 방식으로, 코드 변경 없이 테넌트 수를 수평 확장할 수 있습니다.

**3. Celery 태스크 재시도 전략**

알림톡 외부 API 장애를 고려해 `autoretry_for`, `max_retries=3`, `countdown` 지수 백오프를 적용합니다. 최종 실패 시 `notifications` 테이블에 실패 상태를 기록해 수동 재발송이 가능합니다.

**4. AI 분석 비용 최적화**

리뷰 건당 OpenAI API를 호출하면 비용이 누적됩니다. 테넌트별 일 배치로 묶어 처리하고, 분석 완료 결과는 DB에 캐싱해 동일 리뷰의 재분석을 방지합니다.

---

## 로컬 실행

```bash
git clone https://github.com/devleeeasy/ezyreview
cd ezyreview
cp .env.example .env        # OPENAI_API_KEY 입력
docker compose up -d        # api / worker / beat / db / redis 일괄 기동
```

```bash
# 1. 테넌트 등록 → API 키 발급
curl -X POST http://localhost:8000/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "내 쇼핑몰", "plan": "basic"}'

# 2. 웹훅 수신 테스트
curl -X POST http://localhost:8000/webhook/{api_key} \
  -H "Content-Type: application/json" \
  -d '{"order_id": "ORD-001", "customer_phone": "010-1234-5678", "product_name": "테스트 상품"}'

# 3. JWT 발급
curl -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "{api_key}"}'

# 4. 인사이트 조회
curl http://localhost:8000/insights/summary \
  -H "X-Api-Key: {api_key}"
```

API 문서 (로컬): `http://localhost:8000/docs`

API 문서 (라이브): `https://ezyreview-production.up.railway.app/docs`

---

## 라이브 데모

**Base URL:** `https://ezyreview-production.up.railway.app`

```bash
# 헬스체크
curl https://ezyreview-production.up.railway.app/health

# API 문서
https://ezyreview-production.up.railway.app/docs
```

---

## 부하 테스트

```bash
pip install locust
locust -f tests/load_test.py --host http://localhost:8000
# http://localhost:8089 에서 Locust UI 접속
```

---

## 디렉토리 구조

```
ezyreview/
├── app/
│   ├── api/
│   │   ├── webhook.py       # 웹훅 수신 엔드포인트
│   │   ├── insights.py      # 인사이트 API
│   │   ├── auth.py          # JWT 발급 엔드포인트
│   │   ├── tenants.py       # 테넌트 등록
│   │   ├── reviews.py       # 리뷰 수집 엔드포인트
│   │   └── admin.py         # 배치 분석 수동 트리거 (운영용)
│   ├── core/
│   │   ├── db.py            # 테넌트 DB 동적 라우팅 핵심
│   │   ├── auth.py          # API 키 / Redis 캐시 인증
│   │   └── config.py
│   ├── models/
│   │   ├── main.py          # main_db 모델 (Tenant, WebhookLog)
│   │   └── tenant.py        # tenant_db 모델 (Order, Review, Notification, ReviewAnalytics)
│   └── schemas/
├── worker/
│   ├── celery_app.py        # Celery 설정 + beat 스케줄
│   ├── tasks.py             # Celery 태스크 정의
│   ├── review_request.py    # 카카오 알림톡 발송 로직
│   └── analytics.py        # OpenAI AI 분석 로직
├── tests/
│   └── load_test.py         # Locust 부하 테스트
├── scripts/
│   └── seed_reviews.py      # 테스트 데이터 삽입
├── docker-compose.yml
└── .env.example
```

---

## 환경 변수

| 변수 | 설명 |
|------|------|
| `DATABASE_URL` | main_db PostgreSQL URL |
| `REDIS_URL` | Redis (Celery 브로커) |
| `OPENAI_API_KEY` | OpenAI API 키 |
| `GMAIL_USER` | 리뷰 요청 발신 Gmail 주소 (로컬 전용 — Railway SMTP 포트 차단) |
| `GMAIL_APP_PASSWORD` | Gmail 앱 비밀번호 (로컬 전용) |
| `KAKAO_API_KEY` | 카카오 알림톡 키 (실서비스 전환용, 현재 미사용) |
| `JWT_SECRET` | JWT 서명 키 |

---

## 성능 측정 결과

Locust 부하 테스트 — 동시 50 users, 60초, 로컬 Docker Desktop (Windows)

| 엔드포인트 | Median | p95 | p99 | 에러율 |
|---|---|---|---|---|
| 웹훅 수신 (신규) | 92ms | 1,000ms | 1,200ms | 0% |
| 웹훅 수신 (중복 차단) | 56ms | 360ms | 500ms | 0% |
| 인사이트 API | 41ms | 880ms | 1,000ms | 0% |
| **전체 집계** | **71ms** | **920ms** | **1,100ms** | **0%** |

- 처리량: **86.9 req/s** (5,075 requests / 60초)
- p99가 높은 것은 Docker Desktop WSL2 네트워킹 오버헤드 영향
- `GET /insights/summary` DB 쿼리 5회 → 2회 최적화 후 median 170ms → 41ms 개선

**병목 개선 전후 비교**

| | 개선 전 | 개선 후 |
|---|---|---|
| insights median | 170ms | 41ms |
| webhook 신규 median | 210ms | 92ms |
| 처리량 | 73.5 req/s | 86.9 req/s |

**배치 AI 분석**: 리뷰 100건 기준 약 90초 이내 완료 (gpt-4o-mini 기준)

---

## 트러블슈팅 기록

실제 배포 과정에서 마주친 이슈와 해결 방법을 정리합니다.

**1. Celery + asyncio 이벤트 루프 충돌**

Celery 워커는 태스크마다 `asyncio.run()`으로 새 이벤트 루프를 생성합니다. 모듈 수준에서 캐시된 SQLAlchemy 엔진(asyncpg)과 `AsyncOpenAI` 클라이언트가 이전 루프에 바인딩된 상태로 재사용되어 `InterfaceError: another operation is in progress` 에러가 발생했습니다.

→ **해결**: `NullPool` 적용으로 태스크마다 새 DB 커넥션 생성, `AsyncOpenAI` 클라이언트도 호출 시점에 생성하도록 변경

**2. Railway SMTP 포트 차단**

Railway를 포함한 대부분의 클라우드 플랫폼은 스팸 방지 목적으로 아웃바운드 SMTP 포트(465, 587)를 차단합니다. Gmail SMTP 설정이 로컬에서는 정상 동작하지만, Railway 배포 환경에서는 `Network is unreachable` 에러가 발생합니다.

→ **해결**: SendGrid HTTP API로 전환 (SMTP 대신 HTTPS 사용)
→ **추가 이슈**: SendGrid 무료 계정 + 단일 발신자 인증만으로는 스팸 분류될 수 있음. 실서비스에서는 도메인 인증(SPF/DKIM 레코드 설정) 필요

**3. Railway `$PORT` 환경변수 미확장**

Railway는 `startCommand`를 exec form으로 실행하므로 `$PORT`가 리터럴 문자열로 전달됩니다. `${PORT:-8000}` 형식도 확장되지 않아 uvicorn이 포트 파싱에 실패했습니다.

→ **해결**: Dockerfile CMD를 shell form으로 변경 (`CMD uvicorn ... --port ${PORT:-8000}`)