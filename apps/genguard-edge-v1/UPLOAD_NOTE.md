# 저장소 게시 메모

2026-10-11 00시대 KST, 팀장 요청으로 우지가 전달한 `genguard-edge-v1.zip`을 이 폴더에 올렸다. 올린 파일은 원본 그대로다. `SHA256SUMS.txt`의 57개 파일이 해시와 일치한다.

용량 때문에 제외한 파일:

- `verification/*/*.sqlite`, `*.activity.lock`: 실험 기록 DB(약 270MB). `SHA256SUMS.txt`에는 `demo-final/fusion.sqlite`, `demo-final/vibration.sqlite`가 남아 있다. 원본은 우지가 보관한다.
- `data/public/marine-engine-fault/Marine_Engine_Fault_Data_v1.zip`(25MB): 공개 데이터 원본. [Zenodo 19857425](https://zenodo.org/records/19857425), CC BY 4.0에서 다시 받는다. 같은 폴더의 LICENSE·CITATION·출처 기록은 올렸다.
- `__pycache__/`, `*.pyc`.

이 모델을 UNO Q의 ClikaRT에서 실행한 방법과 결과는 [runtime/hardware/clikart/README.md](../../runtime/hardware/clikart/README.md)에 있다.
