# 저장소 게시 메모

2026-10-11 00시대 KST, 팀장 요청으로 우지가 전달한 `genguard-edge-v1.zip`을 이 폴더에 올렸다. 올린 파일은 원본 그대로다. `SHA256SUMS.txt`의 57개 파일이 해시와 일치한다.

용량 때문에 제외한 파일:

- `verification/*/*.sqlite`, `*.activity.lock`: 실험 기록 DB(약 270MB). `SHA256SUMS.txt`에는 `demo-final/fusion.sqlite`, `demo-final/vibration.sqlite`가 남아 있다. 원본은 우지가 보관한다.
- `data/public/marine-engine-fault/Marine_Engine_Fault_Data_v1.zip`(25MB): 공개 데이터 원본. [Zenodo 19857425](https://zenodo.org/records/19857425), CC BY 4.0에서 다시 받는다. 같은 폴더의 LICENSE·CITATION·출처 기록은 올렸다.
- `__pycache__/`, `*.pyc`.

**올린 뒤 바꾼 것 (팀장 요청, ClikaRT 연결):** `genguard/model.py`에서 점수 계산을 `raw_score`로 분리하고 `score_phase`·`predict`가 계산 함수를 받게 했다. `genguard/core.py`의 `EdgeRuntime`은 `GENGUARD_ENGINE`(기본 `numpy`, 선택 `clikart`)을 읽는다. `genguard/clikart_engine.py`는 새 파일이다. NumPy 기본 동작은 그대로이며 기존 테스트 53개가 통과한다. 이 두 파일은 `SHA256SUMS.txt`의 원본 해시와 달라졌다. 우지가 로컬에서 같은 파일을 고치고 있다면 합칠 때 이 변경을 확인한다.

이 모델을 UNO Q의 ClikaRT에서 실행한 방법과 결과는 [runtime/hardware/clikart/README.md](../../runtime/hardware/clikart/README.md)에 있다.
