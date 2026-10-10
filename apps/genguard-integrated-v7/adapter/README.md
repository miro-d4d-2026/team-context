# 실제 IMU → 하이브리드 분석 API 연결

이 어댑터는 **UNO Q에서 이미 실행 중인 IMU 수신기**를 구독한다. MCU 펌웨어나 미로의 기존 웹 수집 코드를 수정하지 않는다. 센서 구독과 분석 서버를 둘 다 UNO Q Linux에서 실행하면 PC는 화면과 모의 계측값 입력을 담당한다.

```text
IMU → 기존 UNO Q gateway (127.0.0.1:9877 JSONL)
                  ├→ 기존 미로 웹 수집 경로 유지
                  └→ adapter.imu_to_api
                       → 2초 RMS·CV → /api/imu/windows
                                      ↑
                                웹에서 수온·유압 등 모의값
```

## 확인한 기존 수집 형식

[팀 gateway 원문](https://github.com/miro-d4d-2026/team-context/blob/27f16199954c5c40eceae25cc7665eed4539f098/prototypes/t4-6-vibration-lab/gateway/unoq_qwiic_gateway.py)의 **고정 커밋 `27f16199954c5c40eceae25cc7665eed4539f098`**을 직접 열어 확인했다. 이 경로는 저장소 기본 브랜치에서는 조회되지 않아, 현재 미로 PC의 최신 파일과 동일하다고 가정하지 않는다. 현재 gateway가 아래 형식을 유지하면 바로 연결된다.

- TCP loopback `127.0.0.1:9877`, 한 줄에 JSON 한 개, 여러 구독자 지원.
- `meta`: `odr_hz`, `accel_range_g`. 확인한 설정은 **208 Hz / ±4 g**.
- `sample`: `seq`, `t_us`, `ax_g`, `ay_g`, `az_g`. 가속도 단위는 **g**.
- `gateway`: `sensor_ready`, `router_connected`, 오류 카운터.
- `error`: 센서 또는 MCU 연결 오류.

기존 gateway가 Arduino Router 연결을 소유한다. 이 어댑터는 TCP만 읽으므로 Router를 중복 점유하지 않는다. 포트·형식이 달라진 경우 이 연결부를 실제 수신기 형식에 맞춰 수정해야 한다.

## UNO Q에서 실행

먼저 패키지 루트의 설명대로 분석 서버를 실행한다. 다음 명령은 **또 다른 UNO Q Linux 터미널**에서, 패키지 루트를 현재 폴더로 두고 실행한다.

```sh
cd /home/arduino/genguard-unoq-hybrid-v2
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1

# 서버에 토큰을 설정했다면 같은 값을 이 터미널에도 설정한다.
export GENGUARD_API_TOKEN='replace-with-your-team-token-2026'

.venv/bin/python -m adapter.imu_to_api \
  --installation phone-mount-001 \
  --api-url http://127.0.0.1:9879
```

- 서버가 토큰 없이 loopback으로 실행 중이면 `GENGUARD_API_TOKEN` 설정을 생략한다.
- `--installation`은 부착 위치·방향·받침·센서 설정을 식별하는 이름이다. 다시 붙이거나 위치를 바꾸면 새 이름을 사용한다.
- gateway가 다른 포트이면 `--gateway-port`로 지정한다. 기본 host는 `127.0.0.1`이다.
- `accepted`, `window_seq` 로그가 약 2초마다 증가하면 서버가 창을 받았다는 뜻이다. 이 로그만으로 발전기 고장 진단 성능을 입증하지는 않는다.
- 웹의 진동 출처는 어댑터가 보내는 **실시간 IMU / 기록 재생**을 자동으로 따른다. 기준 진동을 유지한 채 기준 수집을 시작한다. 이후 수온·유압 등은 웹에서 바꾸고 진동은 아이폰 진동 앱에서 바꾼다.

운영 시 통신 토큰을 명령줄 인자나 URL에 넣지 않는다. 어댑터는 환경 변수에서 읽으며 토큰을 로그에 출력하지 않는다.

## 단절·재연결·품질

- TCP 단절, 3초 무수신, MCU 시각·순번 초기화, 센서 설정 변경, API 전송 실패: **어댑터 종료, 미완성 창 폐기**.
- 이때 웹의 마지막 숫자를 새 실측값으로 다시 보내지 않는다. 분석 서버의 freshness 검사에 따라 센서 수신 중단/판단 불가가 표시된다.
- 케이블과 gateway를 확인한 뒤 위 명령을 다시 실행한다. 새 `stream_id`가 생성되므로 이전 진동 기준을 자동 재사용하지 않는다. 웹에서 기준 진동을 다시 수집한다.
- 통신이 잠시 실패해도 오래된 창을 대량 재전송하지 않는다. 동일 body에 대한 서버 중복 처리와 별개로, 이 어댑터는 전송 실패 시 종료한다.
- 샘플 결손·불규칙한 MCU 시각·포화·오류 카운터 증가가 있는 창은 `quality.valid=false`와 `null` 특징값을 보낸다. 이를 0 g의 정상 진동으로 바꾸지 않는다.
- 실제 계측 종료 시각은 첫 샘플 수신 시각과 MCU의 경과 시간을 조합한다. 전송 대기 때문에 오래된 창이 새 시각으로 바뀌지 않는다. 3초 이상 밀린 버퍼는 폐기한다.

API와 어댑터를 같은 UNO Q에서 실행하는 구성이 기본이다. 서로 다른 컴퓨터에 둔다면 시계 동기화가 추가로 필요하다.

## 계산하는 진동 특징

- **RMS (g)**: 2초 창에서 X/Y/Z 축 평균을 각각 제거한 뒤 `sqrt(var(x) + var(y) + var(z))`.
- **Envelope CV (무차원)**: 100ms 구간별로 다시 축 평균을 제거한 RMS 20개의 모집단 표준편차 / 평균.
- 창은 네트워크 도착 간격이 아닌 MCU `t_us`로 구성한다. 정상 uint32 rollover는 처리하고 MCU reset은 새 스트림을 요구한다.
- 기대 샘플 수의 80–120%, 최소 1.8초 coverage, 100ms 구간당 5개 이상, ±full-scale의 98% 미만 등을 검사한다. 이는 시제품 데이터 품질 정책이며 고장 진단 한계값이 아니다.

특징 정의는 이전 실측 아이폰 진동 분석과 기존 GenGuard edge 어댑터를 유지했다. 6,660Hz 칩 사양만으로 현재 수집률을 주장하지 않고, 실제 `sample_rate_hz`도 별도로 전송한다.

## CSV로 연결만 재현할 때

보드가 없는 개발 환경에서 이전 측정 CSV를 재생할 수 있다. 화면 출처는 반드시 **기록 재생**으로 표시된다. 이 모드의 시각은 원본 계측 시각이 아닌 재생 시각이며 원본 기록을 현재 실측이라고 주장하지 않는다.

```sh
.venv/bin/python -m adapter.imu_to_api \
  --installation recorded-phone-mount-001 \
  --csv /원본경로/20261010T104429-70687ed6.csv \
  --recorded-odr-hz 208 \
  --recorded-range-g 4
```

CSV 필수 열은 `seq,t_us,ax_g,ay_g,az_g`다. 원본에는 다른 열이 있어도 된다. 다른 단위의 `ax` 등을 가속도 g라고 추정하지 않는다. 파일에서 ODR·full scale을 알 수 없으므로 실제 기록 설정을 인자로 지정한다. 위 208/4는 확인된 팀 gateway 설정이다.

약 2초마다 한 창씩 전송하며 `source`는 코드에서 `recorded_imu`로 고정한다. `--live` 등으로 출처를 바꾸는 옵션은 없다. 파일 끝의 2초 미만 구간은 폐기한다.

## 검증

```sh
.venv/bin/python -m unittest -v adapter.test_adapter
```

14개 테스트: 중력 제거, CV 계산 일치, 포화·결손, MCU rollover/reset, 설정 변경, gateway 단절·무수신·오류, 지연 버퍼, CSV 단위 확인, 기록 출처와 재생 속도, API 주소 검증.

기존 `20261010T104429-70687ed6.csv`를 이 파서와 특징 계산기에 넣은 별도 사전 검사에서는 **완성 창 146개 / 품질 통과 146개**였다. 이것은 파일 형식과 특징 계산 호환성 확인이며, 실제 UNO Q 실행·센서 통합 시험이나 발전기 진단 정확도 결과가 아니다.
