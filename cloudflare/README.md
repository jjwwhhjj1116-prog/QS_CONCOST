# Cloudflare 이전 시험판 — 운영 전환 금지

브랜치: `codex/cloudflare-migration`. Render/main/운영 DNS/GitHub 예약메일은 변경하지 않음.
이 브랜치는 **1차 수집·저장·조회 분리 실험**이며 전체 기능 이전 완료본이 아니다.

## 구조

동일 웹사이트 → Worker 정적 화면/조회 API → D1 저장 자료

수집 요청 → Queue 기관별 작업 → Cloudflare Container의 기존 Python 파서 → D1

크롤러는 Render가 아니라 Cloudflare Containers에서 실행하도록 구성했다.
Python 파서와 적합도 규칙은 재사용한다. 웹 서버/SQLite/관리자 로그인/메일 발송은
크롤러 프로세스에 포함하지 않는다. 기관당 최대 180초, 수동 작업은 접수 시점부터
5분 기한으로 설정하며, 대기 작업도 기한이 지나면 expired가 된다.
지원COK 원기관은 하나의 큰 작업이 아니라 URL 기준으로 독립 실행한다.

## 먼저 확인한 문제 / 범위

- 기존 `Future.cancel()`은 실행 중인 스레드를 정지하지 못한다. 새 실행부는
  별도 프로세스를 종료하고 회수한다. 일부 파서가 남긴 스레드도 최종 JSON 출력 후 종료한다.
- 기존 서버 내부 작업 상태는 재시작에 취약하다. 시험판 작업 상태와 자료는 D1에 남긴다.
- 기관 오류/시간초과/적합도 제외/정상 빈 응답을 별도 기록한다.
- 지원COK 게시일 누락 시 오늘로 채우던 처리를 제거했다. 날짜가 하나뿐이면
  게시일만 기록하고 임의로 마감일을 만들지 않는다.
- 후보 수는 원기관 전체 공고 수가 아니라 **기존 파서가 반환한 후보 수**다.
- 기존 파서 내부의 페이지 상한, 날짜 해석, 부분 오류 은폐, 서울 지역 판별은
  아직 전 기관 대조 검증 전이다. 호스팅 변경으로 수집 완전성이 보장되지 않는다.
- 뉴스/법규/사전정보/공사비 파서 호출 경로는 포함했지만 Cloudflare 실환경 검증 전이다.

## 현재 안전장치

- `SCHEDULE_ENABLED=false`, `triggers.crons=[]`.
- 메일은 당일 자료 JSON 미리보기만 가능. Resend 호출 코드/발송 키가 없다.
- 기존 관리자 설정·주소록 변경·관심 상태 변경·로그인 기능은 아직 미이전:
  HTTP 501을 반환하며 화면 상단에 시험판 제한을 표시한다.
- 시험 수집/작업 상태/메일 미리보기는 `TRIAL_ADMIN_TOKEN` Bearer 인증 필수.
- API 인증키는 Worker Secrets에서 Container 환경으로만 전달.
- 이미지에는 `.env`, DB, Git 이력을 넣지 않는다. 원본 API 응답(raw)은 저장하지 않는다.
- Docker는 기존 모듈만 복사하며 기존 `serve`/자동메일을 시작하지 않는다.
- Worker/D1/Queue 이름에 모두 trial을 사용. 운영 도메인 route는 없다.

## 로컬 검증

```powershell
python -m unittest discover -s tests -q
cd cloudflare
npm ci --ignore-scripts
npm test
npx wrangler d1 migrations apply concost-migration-trial --local
npx wrangler dev --local --enable-containers=false --port 8791
```

이 로컬 명령은 Worker/D1 조회만 시험한다. `--enable-containers=false`에서는
수집 Queue→Container 통합이 동작하지 않는다. Windows Container 개발은 WSL/Docker 환경이 필요하다.

```powershell
# Worker 번들만 검증 (실제 배포 아님)
npx wrangler deploy --dry-run --containers-rollout=none
# Python 수집 컨테이너 이미지 검증: Docker Engine 실행 필요, repo root에서 실행
docker build -f cloudflare/Collector.Dockerfile -t concost-collector-trial .
```

## 클라우드 배포 전 필요한 승인/설정

2026-09-08 Wrangler 계정 조회: 로그인은 유효하나 Containers 목록 조회에서
`Workers Paid plan required`로 거부됨. 결제/업그레이드는 실행하지 않았다.
공식 요금: https://developers.cloudflare.com/containers/platform/pricing/

Workers Paid 승인/가입 후 **시험용** D1/Queue를 만들고 config의 0으로 된
database_id를 실제 trial ID로 교체해야 한다. Docker Engine도 실행해야 한다.
필요한 Secret은 `TRIAL_ADMIN_TOKEN`, `DATA_GO_KR_SERVICE_KEY`, `LAW_API_OC`이다.
운영 자동메일 키는 이 시험판에 복사하지 않는다.

시험 수집: `POST /api/trial/collect`에 `{ "lookback_hours": 168 }`.
반환된 status_url로 조회. `GET /api/trial/digest`는 메일을 보내지 않는다.
토큰은 `Authorization: Bearer ...` 헤더로만 전달한다.

## 검증 기록 (2026-09-08, 회사 PC에서 실행)

| 항목 | 확인 결과 |
|---|---|
| 기존 회귀 + Python 신규 테스트 | 90개 통과 |
| Worker 날짜/필터/오류 구분 테스트 | 5개 통과 |
| Wrangler Worker dry-run | 통과 (Container 이미지 제외) |
| 로컬 D1 스키마 적용 | 통과 |
| Worker 재시작 후 D1 조회 | 시험 레코드 보존 확인; 검증 후 시험 레코드 삭제 |
| 시험 API 인증/메일 미리보기 | 미인증 401, 인증 후 당일 JSON 확인, 발송 없음 |
| 나라장터 독립 프로세스 실제 조회 | 파서 후보 156건 → 적합 8건, 약 18초 |
| 누리장터 독립 프로세스 실제 조회 | 4건 → 4건, 약 6초 |
| 지원COK 부산 정비 원기관 | 후보 5건 → 기준 제외 5건, 약 0.8초 |
| 지원COK 부산광역시 원기관 | 파서 반환 0건, 약 1.5초 (사이트 전체 0건이라는 의미 아님) |
| Cloudflare 실배포/발신 | 미실시: 유료 플랜 승인 대기 |

## 운영 전환 합격 조건 — 아직 완료되지 않음

1. 모든 원기관별 실환경 상태/페이지 범위/날짜/적합도/링크를 원문과 대조.
2. Queue→Container→D1→실제 화면 E2E, D1 재시작 보존, 느린 기관 종료 검증.
3. 관리자 인증·주소록·발신 설정·API 키 저장·관심 상태 기능을 이전하고 회귀 검증.
4. 09:00~09:55 수집과 10:00 메일을 독립 실행. 한국시간/평일/당일 자료만 적용.
5. 영구 발송 이력 + 날짜별 원자적 잠금 + Resend idempotency 검증.
6. 승인된 시험 수신자에게만 실제 발송하고 이후 운영 예약을 **한 곳만** 활성화.
7. 사용자가 전환 승인한 후 GitHub/Render 기존 예약 중지 및 도메인 전환.

예약 시각은 발송 요청의 목표 시각이다. 외부 스케줄러 지연·메일 서버 수신 시각까지
정확히 10:00임을 보장하지 않는다. 늦은 재발송 정책은 운영 전환 때 명시적으로 결정한다.
