# 2026-10-10 Edge Impulse 14299와 gym-pybullet-drones 검토

확인일: 2026-10-10 KST. 사용자가 지정한 두 공개 자료의 설정·시험 결과·문서·소스·유지 상태를 검토했다. 일반 교육용 동작 인식과 인간-컴퓨터 상호작용(HCI) 연구 도구로서의 평가다. 설치·모델 재학습·우리 장치 실행·사람 대상 시험은 하지 않았다. T4-6·T4-8의 발산 상태를 유지하며 후보를 선정하지 않는다.

**두 자료 모두 재사용할 연구 기반은 있지만, ‘사람이 조작을 빨리 배우는 완성 제품’이나 그 효과의 증거는 아니다.** Edge Impulse는 동작 분류 예제, gym-pybullet-drones는 물리 시뮬레이션·강화학습 연구 환경이다. 둘을 함께 사용한다는 사실만으로 독창성·학습 효과가 성립하지 않는다.

## 1. 역할과 빈 부분

| 자료 | 확인한 재사용 가치 | 확인되지 않은 기능·효과 |
|---|---|---|
| [Edge Impulse 공개 프로젝트 14299](https://studio.edgeimpulse.com/public/14299/latest) | 가속도 데이터, 특징 추출, 네 동작 분류, 이상 점수, 저장된 시험 결과 | 손 궤적의 연속 출력, 즉석 개인 동작의 소수 예시 등록, 초보자 학습시간 단축 |
| [gym-pybullet-drones](https://github.com/learnsyslab/gym-pybullet-drones) | 가상 환경, 상태 반환, 예제·시뮬레이션 로그 | 참가자 안내·입력 비교·학습 세션 관리가 갖춰진 완성 HCI 앱, 사람의 숙련 효과 |

아래의 현직자 검토 기준은 자료에 근거한 **우리 분석**이다. 현직자 인터뷰나 심사위원의 실제 평가를 새로 확보한 결과가 아니다.

## 2. Edge Impulse: continuous라는 이름을 정확히 읽기

검토 당시 공개 버전은 **14**, 기본 표시 모델은 float32다. 입력은 62.5Hz의 3축 가속도이며, **2,000ms 창을 240ms씩 이동**해 spectral features를 만든다. 출력은 `idle / snake / updown / wave` 네 분류와 별도의 K-means 이상 점수다. ‘다섯 동작 분류’가 아니다. [실제 impulse 설정](https://studio.edgeimpulse.com/public/14299/latest/impulse/1/create-impulse)

여기서 continuous는 연속 데이터에 대한 반복 분류다. 손의 방향·크기·궤적을 그대로 연속 출력하는 모델이 아니다. 공식 튜토리얼은 2,000ms/80ms를 안내하므로 **튜토리얼 값과 현재 프로젝트의 240ms를 혼동하면 안 된다.** 튜토리얼 자체도 관측 구간 도중 동작을 바꾸면 잘못된 결과가 나올 수 있다고 설명한다. [공식 튜토리얼](https://docs.edgeimpulse.com/tutorials/end-to-end/motion-recognition)

2초 창은 관측 맥락의 길이이며 모든 반응이 정확히 2초 늦는다는 뜻도 아니다. ‘반복 동작 가운데 잘 맞음’과 ‘시작·전환·종료가 자연스럽게 받아들여짐’은 별도 평가 대상이라는 해석이 적절하다.

### 99.8%가 실제로 뜻하는 것

- 데이터는 총 101개 녹음 파일, 15분 16초다. 훈련 85파일과 시험 16파일이며 참여자 101명이라는 뜻이 아니다. [훈련 목록](https://studio.edgeimpulse.com/public/14299/latest/acquisition/training), [시험 목록](https://studio.edgeimpulse.com/public/14299/latest/acquisition/testing)
- 저장된 시험 결과의 공개 상태에는 정답 501, 오답 1이 있다. **502개 창 중 501개 정답 = 약 99.8%**다. 인접 창은 88%가 중첩된다(2,000ms와 240ms로 계산). 502개의 독립 사용자 시험으로 해석할 수 없다.
- 정상 `updown.1` 약 5초 파일에서 12창은 updown, **1창은 anomaly로 거절**됐다. 미지 동작 `shake.1` 약 5초 파일은 13창 모두 anomaly였다. 미지 동작 시험 파일은 하나이므로 모든 불규칙한 움직임을 걸러낸다는 근거가 아니다. [저장된 시험 결과](https://studio.edgeimpulse.com/public/14299/latest/impulse/1/validation)

공개 분류 설정의 그룹 분할 키는 비어 있고 시험 상태에도 참여자·세션 구분 키가 없다. 문서상 그룹 키는 같은 사람·그룹의 데이터를 같은 분할에 유지할 수 있는 기능이다. **사람·세션 독립성이 입증되지 않았지만 실제 데이터 누수를 확인한 것도 아니다.** 이 예제 수치를 새로운 사용자·센서·착용 조건의 성능으로 옮겨 쓰지 않는다. [분류 설정](https://studio.edgeimpulse.com/public/14299/latest/impulse/1/learning/keras/35), [그룹 분할 설명](https://docs.edgeimpulse.com/studio/projects/learning-blocks)

### 5ms와 이상 점수의 한계

대시보드의 5ms·RAM 2.2K·Flash 15.5K는 Infineon PSoC6 CY8C624, Cortex-M4F 150MHz를 대상으로 표시된다. 공식 문서는 대상 장치 기반 추론 시간·메모리를 **추정치**라고 설명한다. Uno Q·NUCODE 실측도, 동작 시작부터 화면 반응까지의 총지연도 아니다. [대시보드](https://studio.edgeimpulse.com/public/14299/latest), [성능 추정 설명](https://docs.edgeimpulse.com/studio/projects/learning-blocks/blocks/classification)

K-means 이상 점수는 특징 공간에서 알려진 군집과 얼마나 떨어졌는지를 평가한다. 사용자의 의도나 ‘원하지 않은 명령’의 의미를 이해한 판정은 아니다. 정상 변형을 거절하는 경우와 미지 동작을 받아들이는 경우를 함께 보아야 한다. [공식 K-means 설명](https://docs.edgeimpulse.com/studio/projects/learning-blocks/blocks/anomaly-detection-k-means)

## 3. gym-pybullet-drones: 사람의 학습과 RL의 학습을 구분

검토 커밋은 **`7ebad1ecabd28a7000add2d05f888aa2e837c2cc`**(2026-09-06 UTC), 패키지 선언은 **2.2.0**이다. GitHub의 최신 게시 Release v1.0.0(2021)만 보고 방치된 저장소라고 판단하면 틀린다. 현재 main은 리팩터링 버전이며 원논문 코드는 README가 안내하는 paper/master와 구분한다. [커밋](https://github.com/learnsyslab/gym-pybullet-drones/commit/7ebad1ecabd28a7000add2d05f888aa2e837c2cc), [패키지 선언](https://github.com/learnsyslab/gym-pybullet-drones/blob/7ebad1ecabd28a7000add2d05f888aa2e837c2cc/pyproject.toml), [Release](https://github.com/learnsyslab/gym-pybullet-drones/releases/tag/v1.0.0)

원논문은 제어·강화학습 방법을 연구·비교하는 환경을 제안한다. 여기서 **learning은 모델의 강화학습**이며, 초보자가 조작을 배우는 속도를 검증한 인간 대상 연구가 아니다. 현재 예제의 학습 결과도 사용자 숙련 효과로 인용할 수 없다. [IROS 2021 원논문](https://arxiv.org/abs/2103.02142)

| 검토 항목 | 사실과 해석의 경계 |
|---|---|
| Mac 지원 | README는 Apple Silicon/macOS 26에서 테스트했다고 명시한다. 작성자의 지원 설명이며 우리 Mac의 설치·GUI 재현 결과는 아니다. |
| 자동 테스트 | 10/10 CI 성공을 확인했다. 워크플로는 Ubuntu/Python 3.12이고, 테스트는 모듈 import와 GUI를 끈 예제 호출이다. Mac GUI·외부 입력·인간 학습 실험의 통과를 뜻하지 않는다. |
| 입력·UI | 검토한 예제·파일 목록에서 참가자용 안내, 제스처 입력 비교, 개인별 학습 세션을 갖춘 앱은 확인되지 않았다. 디버그 슬라이더가 있다는 것과 학습용 UI가 있다는 것은 다르다. |
| 로그 | 기본 CSV 저장은 설정 주기에 맞는 균일한 시뮬레이션 시간축을 생성한다. 실제 입력→화면 지연을 측정한 로그로 사용할 수 없다. |

출처: [README](https://github.com/learnsyslab/gym-pybullet-drones/blob/7ebad1ecabd28a7000add2d05f888aa2e837c2cc/README.md), [워크플로](https://github.com/learnsyslab/gym-pybullet-drones/blob/7ebad1ecabd28a7000add2d05f888aa2e837c2cc/.github/workflows/test.yml), [10/10 CI](https://github.com/learnsyslab/gym-pybullet-drones/actions/runs/38019463004), [예제 테스트](https://github.com/learnsyslab/gym-pybullet-drones/blob/7ebad1ecabd28a7000add2d05f888aa2e837c2cc/tests/test_examples.py), [Logger 소스](https://github.com/learnsyslab/gym-pybullet-drones/blob/7ebad1ecabd28a7000add2d05f888aa2e837c2cc/gym_pybullet_drones/utils/Logger.py)

설치 부담도 남는다. Python 3.12 안내와 macOS PyBullet 소스 빌드 절차가 있으며 기본 의존성에 Torch·SB3 등도 포함된다. 범위형 의존성을 쓰므로 저장소 SHA만 고정해도 모든 패키지 버전이 고정되는 것은 아니다. 실제 M2 Mac 빌드 실패 이슈에서는 2024년에 Clang 사용으로 해결했다는 보고가 있고, 2025년 M4 사용자의 후속 질문에는 확인한 페이지에서 해결 답변이 없다. 오래된 이슈를 현재 버전의 재현된 결함으로 취급하지 않는다. [설치 문서](https://github.com/learnsyslab/gym-pybullet-drones#installation), [이슈 #234](https://github.com/learnsyslab/gym-pybullet-drones/issues/234)

## 4. Uno Q·ClikaRT·NUCODE와의 관계

**공식 Uno Q 지원이 있다고 이 두 자료가 우리 보드에서 즉시 연결된다고 볼 수는 없다.** Edge Impulse는 Uno Q/App Lab 배포 문서를 제공하지만 해당 안내의 주요 예시는 영상 객체 탐지다. 14299의 외부 가속도 입력·분류 전체가 같은 방식으로 바로 동작한다는 증거는 아니다. [공식 App Lab 배포](https://docs.edgeimpulse.com/hardware/deployments/run-arduino-app-lab)

Edge Impulse의 C++ export와 EIM에는 모델뿐 아니라 특징 추출 등도 들어간다. EIM은 대상 운영체제·아키텍처용 실행 파일이다. ClikaRT의 ONNX 실행 지원과 같은 파일 형식이 아니므로 **EIM을 그대로 ClikaRT에 넣을 수 있다**고 주장하지 않는다. 모델 형식·전처리·입출력·출력 일치성의 확인이 남지만 이번에는 통합 작업을 하지 않았다. [C++ export](https://docs.edgeimpulse.com/hardware/deployments/run-cpp), [EIM](https://docs.edgeimpulse.com/hardware/deployments/run-linux-eim), [ClikaRT ONNX 문서](https://docs.clika.io/clikart/how-to/run-an-onnx-model/)

관련 실제 실패도 있다. 2026-08-18 Uno Q/App Lab 0.10.0에서 키워드 모델 빌드는 성공했지만 설치·표시가 실패한 사례를 직원이 재현했고, 모델 블록을 바꾸는 우회책을 제안한 뒤 **9/4 보고자가 해결을 확인**했다. 이는 모델 빌드와 앱에서의 사용 가능성이 다를 수 있다는 사례다. 현재 미해결 오류나 모션 14299에도 같은 오류가 난다는 증거는 아니다. [공식 지원 포럼](https://forum.edgeimpulse.com/t/edge-impulse-failed-to-push-the-model-to-arduino-uno-q-via-app-lab/18577)

NU-40·NU-87 센서 구성, 우리 Uno Q의 버전·설정, ClikaRT 행사 배포본, 실제 연결·반응은 이번 두 프로젝트 검토로 확인되지 않는다. 보드 제공 사실과 호환성 실증을 분리한다.

## 5. 현직자 관점에서 남는 질문 — 분석자의 판단

| 주장하려는 가치 | 필요한 근거 | 지금 자료로 말할 수 있는 수준 |
|---|---|---|
| 사람이 빨리 배운다 | 설명·착용·보정·등록·오류 회복을 포함한 시간, 같은 성공 기준의 비교 | 두 자료 모두 해당 사용자 비교 결과 없음 |
| 다른 사람도 쉽게 쓴다 | 새 사람·다른 날·다시 착용한 조건을 구분한 결과 | Edge 공개 자료의 사람·세션 독립성 미확인 |
| 사용자가 자연스럽다고 느낀다 | 오류, 피로, 거부당한 입력, 사용자가 동작을 억지로 바꾸는 부담 | 네 반복 동작의 분류 정확도로 대체할 수 없음 |
| 나중에도 기억한다 | 시간 간격을 둔 재사용과 새로운 일반 과제에서의 확인 | 첫 시연·모델 학습 곡선으로 입증 불가 |
| 현장 지원 부담이 낮다 | 설치·버전·재등록·복구를 다른 사람이 수행한 기록 | 공식 문서·과거 지원 이슈는 있으나 우리 환경 재현 없음 |

일반 HCI 발산에서는 **처음 이해하기 쉬운 입력, 오류를 이해하고 회복하기 쉬운 입력, 다시 사용할 때 적응 부담이 적은 입력**을 각각 열어 둘 수 있다. 어느 것이 더 가치 있는지는 아직 가설이다. ‘인식기가 원하는 동작을 사용자가 억지로 배운 것’과 ‘사용자의 학습 부담을 줄인 것’을 구분해야 한다. 앞선 [빠른 학습 원연구 검토](2026-10-10-fast-learning-practitioner-review.md)와 함께 판단한다.

## 6. 재사용·검증 범위

Edge 프로젝트는 BSD-3-Clause-Clear, gym 저장소는 MIT로 표시된다. 고지 보존 등 각각의 조건을 지켜야 하며, 플랫폼·SDK·의존성 전부가 동일 라이선스라는 뜻은 아니다. BSD-3-Clause-Clear는 특허권을 부여하지 않는 조항도 명시한다. 실제 배포 번들의 라이선스는 별도로 확인해야 한다. [BSD-3-Clause-Clear](https://spdx.org/licenses/BSD-3-Clause-Clear.html), [gym LICENSE](https://github.com/learnsyslab/gym-pybullet-drones/blob/7ebad1ecabd28a7000add2d05f888aa2e837c2cc/LICENSE)

공개 설정·저장된 평가·소스·CI·지원 이슈를 읽은 정적 검토다. 계정 생성, 저장소 코드 실행, 모델 재학습·추론, 외부 센서·실기체 연결, 사용자 시험은 하지 않았다. Edge의 latest 주소와 공식 문서는 이후 바뀔 수 있고 gym은 위 SHA를 기준으로 한다. 정확도·추정 지연·upstream CI를 우리 구현의 성공으로 보고하지 않는다.
