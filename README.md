# CONCOST 건설 기회정보 레이더

공공 입찰공고, 건설 주요뉴스, 조달 관련 제도와 법령을 수집하고 CONCOST의 QS 업무 기준으로 적합도를 계산하는 사내 정보 대시보드입니다.

## 주요 기능

- 나라장터·LH·한국도로공사·K-apt 공동주택 실제 입찰공고 수집
- 조달청·국토교통부 건설뉴스 수집
- 조달청 훈령·고시·행정예고와 국가법령정보 OPEN API 연동
- 공사비·견적·안전진단·재건축·재개발 키워드 기반 적합도 점수
- 중복 제거, 변경공고 감지, 검토 상태 관리
- 관리자 전용 API 설정과 비밀번호 변경
- CONCOST 브랜드 영상 대시보드
- 매일 오전 9시 20분 자료수집, 오전 10시 CONCOST 브랜드 이메일 브리핑
- 관리자 주소록, HTML 미리보기, 즉시 발송 및 발송 이력
- 신규 공고 우선 노출, 기존 알림 프로젝트 구분, 적합도 점수 표시

## Windows 로컬 실행

1. `.env.example`을 `.env`로 복사합니다.
2. 최초 설치라면 `ADMIN_BOOTSTRAP_PASSWORD`에 임시 관리자 비밀번호를 입력합니다.
3. `run_dashboard.cmd`를 더블클릭합니다.
4. 브라우저에서 <http://127.0.0.1:8765>를 엽니다.

현재 PC의 기존 데이터베이스에는 관리자 계정이 이미 있으므로 기존 로그인 정보를 그대로 사용할 수 있습니다.

```powershell
Copy-Item .env.example .env
notepad .env
python -m tender_radar.cli init
python -m tender_radar.cli serve --open
```

## 명령

- `python -m tender_radar.cli init`: 데이터베이스 초기화
- `python -m tender_radar.cli collect`: 공고·뉴스·법령 즉시 수집
- `python -m tender_radar.cli serve`: 대시보드 서버 실행
- `python -m tender_radar.cli run --interval 60`: 60분마다 자동 수집

## 보안

- `.env`, SQLite DB, 로그 및 API 인증값은 Git에서 제외됩니다.
- Windows 로컬 저장 인증값은 DPAPI로 암호화됩니다.
- Linux 호스팅에서는 API 인증값과 `ADMIN_BOOTSTRAP_PASSWORD`를 호스팅 서비스의 Secret 환경변수로 주입해야 합니다.
- 공개 저장소에 실제 인증값이나 비밀번호를 커밋하지 마세요.

## 배포 참고

GitHub Pages는 Python 서버를 실행하지 못합니다. 전체 기능 배포에는 Render, Railway, Fly.io 또는 사내 서버처럼 Python 프로세스와 영구 저장소를 제공하는 호스팅이 필요합니다. 배포 환경에서는 `HOST=0.0.0.0`, 서비스가 지정한 `PORT`, 영구 `DB_PATH`를 설정하세요.

저장소의 `Dockerfile`은 포트 `8080`과 `/data` 영구 볼륨을 기준으로 준비되어 있습니다. 호스팅 서비스에서 `/data`를 영구 디스크로 연결하고 다음 값을 Secret 환경변수로 등록합니다.

- `DATA_GO_KR_SERVICE_KEY`
- `LAW_API_OC`
- `ADMIN_USERNAME`
- `ADMIN_BOOTSTRAP_PASSWORD`
- `APP_SECRET_KEY` (서버 저장 인증값 암호화용 24자 이상 임의 문자열)
- `RESEND_API_KEY` (SMTP 대신 HTTPS로 메일을 보내는 Resend API 키)
- `DIGEST_FROM_EMAIL` (예: `CONCOST <news@con-cost.co.kr>`)
- `DIGEST_RECIPIENTS` (재시작 시 복원할 수신 주소, 쉼표 구분)
- `DIGEST_TRIGGER_TOKEN` (예약 발송 API 인증용 임의 문자열)

## 이메일 자동 알림

1. Resend에서 발신 도메인을 인증하고 API 키를 발급합니다.
2. 관리자 설정의 **매일 아침 자료 수집·이메일 브리핑**에서 발신 주소와 API 키를 저장합니다.
3. 알림 주소록에 사내 수신자를 등록하고 HTML 미리보기로 내용을 확인합니다.
4. GitHub Actions의 `CONCOST daily data collection`이 월~금 00:00~00:50 UTC, 즉 09:00~09:50 KST에 10분 간격의 독립 작업으로 최신 공고·뉴스·법령을 반복 수집합니다. 각 회차는 최대 5분 안에 끝나므로 마지막 회차는 09:55 전후까지 누적됩니다.
5. `CONCOST daily email digest`는 월~금 09:50·09:55 KST에 먼저 실행되어 10:00까지 대기하며, 누락 시 10:00·10:05·10:10에 재시도합니다. 날짜별 Resend 멱등성 키로 실제 메일은 하루 한 번만 발송합니다.
6. 메일 발송 요청은 수집을 다시 실행하지 않고 09시대에 저장된 스냅샷만 사용하므로, 느린 원기관 때문에 10시 메일이 함께 멈추지 않습니다.

Render 무료 Web Service는 SMTP 포트가 차단되어 있으므로 메일은 Resend HTTPS API로 전송합니다. GitHub 저장소의 Actions Secret과 Render 환경변수에 동일한 `DIGEST_TRIGGER_TOKEN`을 설정해야 합니다.

무료 Web Service의 SQLite 파일은 휴면·재시작·재배포 때 삭제됩니다. 따라서 운영 전에는 유료 Persistent Disk(`/var/data/tender_radar.db`)나 외부 PostgreSQL을 연결해야 주소록과 발송 이력이 안정적으로 유지됩니다. 임시 운영 중에는 `DIGEST_RECIPIENTS` 환경변수에 수신 주소를 쉼표로 등록하면 재시작 시 주소록이 복원됩니다.

`render.yaml`은 Render 무료 Web Service용 초기 구성입니다. 공식 데이터는 월~금 09:00~09:55 KST에 GitHub Actions가 반복 갱신합니다. 운영 단계에서는 유료 영구 디스크나 PostgreSQL 전환을 권장합니다.

Pexels 영상은 해당 콘텐츠 페이지와 제작자 링크를 화면에 표시해 출처를 고지합니다.
