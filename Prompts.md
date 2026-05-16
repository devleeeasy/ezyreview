# ezyreview — Claude Code 주차별 프롬프트

---

## 1주차 — 멀티테넌트 기반

### 목표
- Docker Compose 환경 구성
- main_db / tenant_db 모델 정의
- 테넌트 DB 동적 라우팅 핵심 로직 구현
- API 키 인증 미들웨어

### 프롬프트

```
ezyreview 프로젝트 1주차 작업을 시작합니다.
CLAUDE.md 규칙을 반드시 준수해 주세요.

목표: 멀티테넌트 백엔드 기반 구축

아래 순서대로 진행해 주세요.

1. Docker Compose 구성
   - FastAPI (포트 8000)
   - PostgreSQL (main_db)
   - Redis
   - 모든 서비스에 TZ=Asia/Seoul 환경변수 추가

2. main_db 모델 생성 (app/models/main.py)
   - Tenant: id, name, api_key, plan, is_active, created_at
   - WebhookLog: id, tenant_id, order_id, payload, status, created_at

3. tenant_db 모델 생성 (app/models/tenant.py)
   - Order: id, order_id, customer_phone, product_name, status, created_at
   - Review: id, order_id, content, rating, created_at
   - Notification: id, order_id, channel, status, sent_at, error_message
   - ReviewAnalytics: id, review_id, sentiment, keywords, summary, created_at

4. 테넌트 DB 동적 라우팅 구현 (app/core/db.py)
   - get_tenant_session(tenant_id): 테넌트 ID로 해당 DB 세션 반환
   - 테넌트 DB가 없으면 자동 생성 (create_tenant_db)
   - main_db 조회 결과는 Redis 캐싱 (TTL 300초)

5. API 키 인증 미들웨어 (app/core/auth.py)
   - X-API-Key 헤더에서 키 추출
   - main_db에서 테넌트 조회 (Redis 캐시 우선)
   - 유효하지 않으면 401 반환

완료 기준:
- docker compose up 후 /health 엔드포인트 200 응답 확인
- 테넌트 생성 시 tenant_{id}_db 자동 생성 확인
- 완료 선언 전 모든 컨테이너 로그 확인
```

---

## 2주차 — 웹훅 수신 + Celery 파이프라인

### 목표
- 웹훅 수신 엔드포인트 구현
- 중복 수신 방지 (Redis SET NX)
- Celery 태스크 구조 구성
- 알림톡 발송 연동

### 프롬프트

```
ezyreview 프로젝트 2주차 작업을 시작합니다.
CLAUDE.md 규칙을 반드시 준수해 주세요.

목표: 웹훅 수신 → Celery 파이프라인 → 알림 발송

아래 순서대로 진행해 주세요.

1. 웹훅 수신 엔드포인트 (app/api/webhook.py)
   - POST /webhook/{tenant_api_key}
   - 요청 바디: order_id, customer_phone, product_name
   - 처리 흐름:
     1) API 키로 테넌트 인증
     2) order_id 중복 체크 (Redis SET NX TTL 86400)
     3) WebhookLog 기록
     4) Celery 태스크 발행
     5) 즉시 {"status": "accepted"} 반환
   - 응답 목표: 200ms 이하

2. Celery 태스크 구성 (worker/tasks.py)
   - review_request_task(tenant_id, order_id)
     : 알림 발송 설정 조회 → 알림톡 발송 → notifications 기록
     : max_retries=3, exponential backoff
     : 실패 시 notifications에 error_message 기록
   - analytics_task(tenant_id, review_id)
     : 3주차 구현 예정 — 함수 시그니처와 로깅만 정의하고 pass 처리
     : 주석으로 "# 3주차 구현 예정" 명시

3. 카카오 알림톡 발송 (worker/review_request.py)
   - 알림톡 API 연동
   - 발송 성공/실패 반환

완료 기준:
- 웹훅 호출 시 notifications 테이블에 발송 이력 기록 확인
- 동일 order_id 재호출 시 중복 처리 차단 확인
- Celery worker 컨테이너 rebuild 후 태스크 정상 수신 확인
```

---

## 3주차 — AI 분석 + 인사이트 API

### 목표
- OpenAI 감성 분석 / 키워드 추출
- 인사이트 집계 API
- JWT 인증 엔드포인트

### 프롬프트

```
ezyreview 프로젝트 3주차 작업을 시작합니다.
CLAUDE.md 규칙을 반드시 준수해 주세요.

목표: AI 리뷰 분석 + 인사이트 API

아래 순서대로 진행해 주세요.

1. AI 분석 태스크 구현 (worker/analytics.py)
   - 2주차에 정의한 analytics_task(tenant_id, review_id) 본격 구현
   - OpenAI API 호출:
     - 감성 분석: 긍정 / 부정 / 중립
     - 키워드 추출: 상위 5개
     - 한줄 요약
   - 결과를 review_analytics 테이블에 저장
   - 동일 review_id 재분석 방지 (analytics 존재 여부 확인 후 스킵)
   - Celery beat로 테넌트별 미분석 리뷰 일 배치 처리

2. 인사이트 API (app/api/insights.py)
   - GET /insights/summary
     - JWT 인증 (Authorization: Bearer)
     - 응답: 리뷰 작성률, 감성 분포(기간별), 상위 키워드 Top 10
   - GET /insights/reviews
     - 리뷰 목록 (페이지네이션, 감성 필터)

3. JWT 발급 엔드포인트
   - POST /auth/token
   - API 키 → JWT 교환

완료 기준:
- 리뷰 저장 후 analytics_task 실행 시 review_analytics 테이블에 감성/키워드/요약 저장 확인
- /insights/summary 응답 정상 확인
- 완료 선언 전 Celery worker 컨테이너 rebuild 및 beat 스케줄 동작 확인
```

---

## 4주차 — 마무리

### 목표
- 부하 테스트 (Locust)
- 성능 수치 확보
- README 최종 업데이트

### 프롬프트

```
ezyreview 프로젝트 4주차 마무리 작업을 시작합니다.
CLAUDE.md 규칙을 반드시 준수해 주세요.

목표: 부하 테스트 + 수치 확보 + 문서화

아래 순서대로 진행해 주세요.

1. Locust 부하 테스트 (tests/load_test.py)
   - 시나리오:
     - 웹훅 수신 엔드포인트 동시 호출
     - 동일 order_id 중복 호출 (중복 차단 검증)
     - 인사이트 API 조회
   - 목표 수치:
     - 웹훅 응답 p99 200ms 이하
     - 동시 50 users 기준 에러율 1% 미만

2. 테스트 결과 정리
   - Locust 리포트 캡처
   - 병목 구간 발견 시 개선 후 재측정

3. README 최종 업데이트
   - 실측 성능 수치로 교체 (추정값 제거)
   - 로컬 실행 최종 검증
   - 기술 의사결정 내용 보완

완료 기준:
- Locust 리포트 캡처 완료
- README 성능 목표 섹션에 실측 수치 반영
```