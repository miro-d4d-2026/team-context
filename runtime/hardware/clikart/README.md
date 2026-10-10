# UNO Q에 ClikaRT 설치·검증

작성 2026-10-11 KST. 근거는 CLIKA 오프라인 문서(0.6.4 기준)의 System requirements, Get ClikaRT, License the runtime, Runtime licenses, Run an ONNX model 페이지와, 아래 "실측 기록"의 보드 실행 결과다.

## 실측 기록 (2026-10-11 00시대, 보드 233936836)

- 보드: Debian 13, aarch64, Python 3.13.5, glibc 2.41, RAM 3.6GB, 홈 여유 17GB. **pip·venv 없음, 시스템 Python은 externally managed.** 그래서 venv 대신 Mac에서 패키지를 풀어 `~/clikart/site`로 올리고 `PYTHONPATH`로 쓴다.
- `clika_runtime-0.6.4-cp313-cp313-manylinux_2_28_aarch64.whl` SHA-256 일치, numpy 2.5.3과 함께 450MB를 올렸다.
- `import clika_runtime` 성공, 버전 0.6.4, CPU 4스레드. Adreno 702 GPU는 Vulkan 1.0만 지원해 쓰지 않고 CPU로 실행한다.
- 라이선스 없이 실행하면 `LICENSE_FAILED`로 멈추는 것을 확인했다.
- **보드 시계가 2025-09-09였다.** 배터리 백업 시계가 없고(RTC 1970) Wi-Fi가 끊겨 NTP 동기화가 안 된 상태였다. 팀장이 3단계의 `sudo date`로 맞췄다. 이때 `arduino` 계정의 만료된 초기 비밀번호를 새로 정했다.
- 팀장이 Offline 번들을 직접 설치했다(signed bundle, 2026-10-17T14:59:00Z 만료).
- **오프라인 검증 통과 (2026-10-10T15:04Z, 보드 Wi-Fi disconnected, 기본 경로 없음, DNS 불가):** 작은 모델 출력 `[[5.25, 5.25, 5.25], [0, 0, 0]]`이 기대값과 일치, 첫 호출 1.04ms. 결과 [results/verify-offline.json](results/verify-offline.json).

## 0. 준비물 (사람이 직접)

| 준비물 | 얻는 곳 | 주의 |
|---|---|---|
| `clika_runtime` 휠 | CLIKA 플랫폼 Download ClikaRT, **Linux arm64, 비CUDA(`0.6.4`), CPython 3.13** | 다운로드 창의 SHA-256을 같이 복사. `+cu12x/+cu13x`는 받지 않는다 |
| Offline 라이선스 번들 | 플랫폼 프로젝트 Licenses에서 **Offline** 종류로 발급 | **발급 화면에서 한 번만 보인다.** 저장소·공유 문서에 붙이지 않는다 |
| USB 연결 | UNO Q를 Mac에 USB-C로 연결 | 보드 인터넷은 필요 없다 |

팀 몫은 Offline 라이선스 2개다(10/10 CLIKA 채널 공지). Online 키는 실행마다 플랫폼 접속이 필요해 오프라인 시연에 쓸 수 없다.

## 1~2. 설치와 첫 검증 (Mac에서)

```sh
bash runtime/hardware/clikart/setup_unoq.sh ~/Downloads/<받은 zip 또는 whl> --sha256 <다운로드 창의 값>
```

Arduino 도구의 adb로 보드 1대 확인 → 휠이 보드(aarch64, CPython 버전)와 맞는지 확인 → Mac에서 보드용 numpy와 휠을 풀기 → 보드 `~/clikart/site`로 복사 → `verify_clikart.py` 실행 → 결과 JSON을 `results/<시각>/`으로 복사. Mac에는 lzma가 되는 Python 3.10 이상이 필요하다(`uv pip`은 이 휠을 못 읽는다).

## 3. 시계 맞추기와 라이선스 넣기 (사람이 직접)

먼저 보드 시계를 맞춘다. 둘 중 하나다.

- 보드를 Wi-Fi에 잠깐 연결해 NTP가 맞추게 한다. 이후 Wi-Fi를 끊어도 전원이 켜져 있는 동안은 맞는 시간이 유지된다.
- 또는 Mac 시간을 보드에 넣는다. 보드 `arduino` 계정의 sudo 비밀번호를 직접 입력한다.

```sh
ADB=~/Library/Arduino15/packages/arduino/tools/adb/32.0.0/adb
$ADB shell -t "sudo date -u -s @$(date +%s)"
```

**보드를 재부팅하면 시계가 다시 틀어진다.** 시연 직전에 `date`로 확인한다.

그다음 라이선스를 넣는다. 아래 명령은 입력을 기다리므로 발급받은 `CLIKA1-...` 문자열을 직접 붙여 넣는다. 보드의 `~/.clika/runtime/license`에 소유자만 읽을 수 있게 저장된다.

```sh
$ADB shell -t /home/arduino/clikart/site/clika_runtime/bin/clikart-license-init
```

## 4. 오프라인 검증 (SC1 필수 조건)

보드 인터넷이 끊긴 상태(Wi-Fi 끔, USB adb만 연결)에서 새 프로세스로 실행한다.

```sh
$ADB shell 'cd ~/clikart && PYTHONPATH=$HOME/clikart/site python3 verify_clikart.py --require-offline --output verify-offline.json'
$ADB pull /home/arduino/clikart/verify-offline.json runtime/hardware/clikart/results/
```

`passed: true`, `network.looked_offline: true`, `tiny_mlp.matches_expected: true`가 함께 나와야 오프라인 실행 증거다. 기대 출력은 `[[5.25, 5.25, 5.25], [0, 0, 0]]`이다.

## 5. GenGuard 판정 모델을 ClikaRT로 실행

[genguard_clikart.py](genguard_clikart.py)는 우지의 [genguard-edge-v1](../../../apps/genguard-edge-v1/genguard/model.py) 학습 결과 JSON(`context-ridge-residual-covariance/1`)을 ClikaRT 빌더로 ONNX 그래프(1.3KB)로 만들고 실행한다. 점수 계산(조건 정규화 → 기대값 → 표준화 편차 → 마할라노비스 거리)은 ClikaRT가, 상태 규칙(미학습 운전 단계, 학습 범위 밖 조건, 임계값 비교)은 원본 `predict`와 같은 Python 규칙이 맡는다.

```sh
$ADB shell 'cd ~/clikart/genguard && PYTHONPATH=$HOME/clikart/site python3 genguard_clikart.py --model fusion-model.json --profile fusion-demo.json --frames fusion-windows.jsonl --output compare.json'
```

**실측 (2026-10-10T15:17Z, 보드 Wi-Fi disconnected):** 우지 데모 DB의 3,900개 창 중 원본 모델이 점수를 낸 running 창 1,319개를 다시 계산했다.

| 항목 | 결과 |
|---|---|
| 원본 판정과 일치 | 1,319/1,319 (change 353, unknown 181, within_baseline 785) |
| 점수 최대 차이 | 절대 6.8e-5, 상대 8.7e-5 (원본 float64, ClikaRT float32) |
| 실행 시간 | 1,319개 한 번에 4.97ms, 1개씩 중앙값 0.92ms |

결과: [results/genguard-edge/compare.json](results/genguard-edge/compare.json), 그래프: [genguard_edge_score.onnx](results/genguard-edge/genguard_edge_score.onnx). 입력 창(`fusion-windows.jsonl`, 15MB)은 우지 데모 DB에서 뽑았고 저장소에는 올리지 않았다.

## 검증 범위

확인한 것: ClikaRT 설치, Offline 라이선스, 네트워크 없는 실행, 우지 모델 점수 계산의 ClikaRT 이식과 원본 대비 같은 판정. **데모 DB는 합성 시나리오 데이터(`data_domain: scenario`)다.** 이 결과는 계산이 원본과 같다는 증거이고, 실제 발전기 고장 탐지 정확도가 아니다. GenGuard 웹 앱의 판정 경로에 연결하는 작업과 SC1 제출물 구성은 아직 남아 있다.
