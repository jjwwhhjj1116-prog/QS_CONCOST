# Cloudflare 무료 이전 시험판
브랜치: `codex/cloudflare-migration`. 운영 Render/main/DNS/예약메일은 변경하지 않았다.

시험 주소: https://concost-migration-trial.jjwwhhjj1116.workers.dev

## 현재 단계
**무료 Worker + D1 + Queue 배포 완료. API 인증키 전송 승인 대기로 실수집 검증 전.**
유료 Containers/SDK/Docker 의존성을 배포 구성에서 제거했다.
실제 메일 발송 코드/키는 없고, 크론도 비활성화했다. 이 시험판을 운영으로 전환하면 안 된다.

## 구조
동일한 웹 화면 → Worker 조회 API → D1
수집 요청 → Queue → Worker가 나라장터·누리장터 API 한 페이지 조회 → D1 저장 → 다음 페이지 Queue

- 5개 API 분야(나라장터 공사/용역, 누리장터 공사/용역/기타), 날짜별 분리.
- 한 번에 20건. 무료 CPU 제한을 고려한 크기이며 실제 CPU 사용량 검증은 아직 필요.
- 이전 Python 적합도 사전은 JSON으로 공유하며 JS 판정과 점수·사유 일치 테스트를 수행.
- 40점 이상 + 안전진단 서울 제한을 유지. 기존 지역 판별의 동명 자치구 문제는 별도 검수 대상.
- 수동 수집 5분 기한. API 호출 15초 제한. 수집 페이지마다 데이터와 다음 페이지 번호를 원자적으로 저장.
- Queue 중복 전달/일시 실패는 페이지 체크포인트와 짧은 임대로 처리.
- API 오류, 빈 응답, 페이지 누락, 필터 제외, 기한 초과를 구분.
- 일일 시험 수집 2회 / API 페이지 1,000회 상한. 계정 전체 무료 한도는 다른 앱과 공유됨.
- 상한/기한에 걸리면 부분 결과를 보존하고 실패/만료로 표시하며 완전 수집으로 주장하지 않는다.
- 운영 메일은 보내지 않음. `/api/trial/digest`는 게시일이 오늘인 데이터의 JSON 미리보기만 반환.

## 미완료 범위
지원COK/뉴스/법규/LH/K-apt/도로공사/K-water/사전 사업정보/공사비 분석의 Workers 수집기는 아직 미이전.
관리자 로그인/주소록/설정 저장/관심 상태/실제 메일/운영 스케줄도 미이전.
기존 화면을 재사용하므로 탭이 남아 있지만 **미연결 탭의 0은 수집 결과가 아니다**.
상단 시험 안내와 API 미설정 오류로 이를 명시한다.

## 검증 (2026-09-08)
- Python 회귀·교차 런타임 검증 93개 통과.
- Node 날짜/응답/페이지/적합도 검증 11개 통과.
- 무료 Worker 빌드 및 workers.dev 실제 배포 성공.
- 신규 무료 trial D1 스키마 적용 성공; trial Queue 2개 생성.
- 실제 브라우저와 공개 상태 API에서 인증키 승인 대기 상태 확인. 화면에 0건 확정으로 표시하지 않음.
- 기존 유료 시제품의 로컬 Python 실행에서 나라장터 8건/누리장터 4건을 확인한 이력은 있으나,
  **이 결과는 무료 Worker 실환경 수집 결과가 아니다**.
- 실환경 API 수집/Queue 처리/CPU 한도/원문 링크 대조는 인증키 연결 승인 뒤 수행해야 한다.
- 결제/플랜 업그레이드/운영 도메인 전환/메일 발송은 하지 않았다.

## 다음에 필요한 승인
보안 승인 단계에서 기존 `DATA_GO_KR_SERVICE_KEY`를 새 시험 Worker Secret으로
전송·영구 저장하는 작업이 차단됐다. 사용자에게 해당 목적지로 키를 옮기는 승인을 받아야 한다.
키는 현재 전송되지 않았다. 승인을 우회하는 다른 경로로 업로드하지 않는다.

승인 후에만 repo root에서:
```powershell
python -X utf8 cloudflare/live_check.py https://concost-migration-trial.jjwwhhjj1116.workers.dev --install-trial-secrets
```
이 명령은 API 키를 시험 Worker Secret으로 저장하고 시험 관리자 토큰을 회전시킨 뒤 실제 수집을 실행한다.
키는 CLI stdin/인증 헤더로만 전달하며 파일/출력에 남기지 않는다. 메일 키는 복사하지 않는다.

## 재현 명령
```powershell
python -m unittest discover -s tests -q
cd cloudflare
npm ci --ignore-scripts
npm test
npx wrangler deploy --dry-run
npx wrangler d1 migrations apply concost-migration-trial --local
npx wrangler dev --local
```
`tender_radar/isolated_collector.py`는 이전 Python 결과를 대조하는 로컬 진단용으로만 남겼다.
무료 Worker에서는 실행되지 않는다. Docker 설치나 Workers Paid가 필요하지 않다.

## 운영 전환 합격 조건
1. 실제 API의 전체 페이지 수/후보 수/선별 수를 원문과 대조하고 CPU/Queue/D1 제한 확인.
2. 중복 전달·재시작·시간초과 중 부분 데이터 보존과 화면 표시 확인.
3. 나머지 기관 및 관리자/주소록/설정/메일 기능 순서대로 이전.
4. 영구 발송 이력/날짜별 원자적 잠금/Resend idempotency 검증 후 승인된 시험 수신자에게만 발송.
5. 사용자 승인 뒤에만 운영 예약을 한 곳으로 통합하고 도메인 전환.

예약은 목표 시각이며 외부 지연과 수신함 도착 시각까지 정각임을 보장하지 않는다.

공식 무료 한도:
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/d1/platform/pricing/
- https://developers.cloudflare.com/queues/platform/pricing/
