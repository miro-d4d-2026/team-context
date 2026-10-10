# 저장소 게시 메모

2026-10-10 23시대 KST, 팀장 요청으로 우지가 전달한 `GenGuard-Monitoring-Research-v2.zip`을 이 폴더에 올렸다. 나머지 파일은 원본 그대로이며 수정하지 않았다.

- **제외한 파일:** `sources/army-10kw-requirement.pdf` (육군 군수사령부 구매요구서 PRD 6115-4016, 2025.4.29 개정). 1쪽에 비밀 표기는 없지만 입수 경로(공개 조달 공고 여부)를 우지에게 확인한 뒤 올린다. 그 전까지 `MANIFEST.json`과 `source-index.json`의 해당 항목은 파일 없이 남는다.
- **게시 시 확인:** JSON 11개 파싱 성공. `MANIFEST.json`의 나머지 24개 파일은 SHA-256이 일치했다. 패키지의 `verify_spec.py`, `build_spec.py`, `ai-study/compare_synthetic.py`는 이번 게시 과정에서 실행하지 않았다.
- **원본 파일 안의 로컬 경로:** `army-coverage.md/.json`, `source-index.json`에 작성자 컴퓨터의 원본 PDF 경로가 기록돼 있다. 해시 대조를 위해 원본을 고치지 않았다.

이 폴더는 연구 스펙과 시연 설계 자료다. GenGuard 앱이나 `contracts/v1` 런타임에 적용된 업데이트가 아니다. 적용 순서는 [MIRO_HANDOFF.md](MIRO_HANDOFF.md)를 따른다.
