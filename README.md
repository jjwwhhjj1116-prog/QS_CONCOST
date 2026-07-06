# CONCOST 건설 기회정보 레이더

공공 입찰공고, 건설 주요뉴스, 조달 관련 제도와 법령을 수집하고 CONCOST의 QS 업무 기준으로 적합도를 계산하는 사내 정보 대시보드입니다.

## 주요 기능

- 나라장터·LH·한국도로공사 실제 입찰공고 수집
- 조달청·국토교통부 건설뉴스 수집
- 조달청 훈령·고시·행정예고와 국가법령정보 OPEN API 연동
- 공사비·견적·안전진단·재건축·재개발 키워드 기반 적합도 점수
- 중복 제거, 변경공고 감지, 검토 상태 관리
- 관리자 전용 API 설정과 비밀번호 변경
- CONCOST 브랜드 영상 대시보드

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

`render.yaml`은 Render 무료 Web Service용 초기 구성입니다. 무료 환경에서는 DB가 재시작 시 초기화될 수 있어 시작 직후 및 60분마다 공식 데이터를 다시 수집합니다. 운영 단계에서는 유료 영구 디스크나 PostgreSQL 전환을 권장합니다.

Pexels 영상은 해당 콘텐츠 페이지와 제작자 링크를 화면에 표시해 출처를 고지합니다.
