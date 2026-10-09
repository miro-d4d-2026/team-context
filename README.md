# D4D 2026 팀 컨텍스트

팀원이 같은 배경과 최신 결정을 읽고, 각자의 AI와 준비를 이어 가기 위한 저장소입니다. 공통 배경의 기준 파일은 [CONTEXT.md](CONTEXT.md)입니다. 현재 기준일은 2026-10-09이며 최종 트랙·문제·역할은 아직 미확정입니다.

## 처음 시작하기

비공개 저장소입니다. 팀장의 초대를 수락하고, 접근 권한이 있는 본인 GitHub 계정으로 인증한 뒤 실행합니다. 팀원 초대는 GitHub 아이디를 받은 후 진행합니다.

```sh
git clone https://github.com/miro-d4d-2026/team-context.git
cd team-context
```

1. 사람은 [TEAM_BRIEFING.md](TEAM_BRIEFING.md)를 먼저 읽습니다.
2. 저장소를 읽는 AI에는 아래 요청을 전달합니다.
3. 내 준비 상태는 [updates/README.md](updates/README.md)의 양식으로 남깁니다.

```text
이 저장소의 AGENTS.md를 읽고, 안내에 따라 최신 상태를 확인한 뒤
CONTEXT.md와 TEAM_BRIEFING.md를 읽어 줘.
확정 사실·미결정·내가 확인해야 할 항목을 구분하고,
내 역할과 시간에 맞는 작은 준비 작업 하나를 도와줘.
어느 커밋을 읽었는지, 원격 갱신에 성공했는지도 알려 줘.
```

파일을 읽을 수 없는 채팅 AI에는 **CONTEXT.md 전체를 첨부하거나 붙여 넣으면** 됩니다. AGENTS.md를 자동으로 읽는지는 도구마다 다르므로 위처럼 명시적으로 요청합니다.

## 다음 작업 전에 갱신하기

저장소 폴더에서 실행합니다.

```sh
git status --short
git pull --ff-only
git log -1 --format='%h %ad %s' --date=iso-strict
```

clone은 처음 한 번 받는 동작입니다. 이후 팀장이 변경 사항을 push하면 각자 pull해서 받습니다. **이미 열린 AI 대화의 기억은 자동 갱신되지 않습니다.** 갱신 후 AI에게 “AGENTS.md, CONTEXT.md와 최근 변경 사항을 다시 읽어 줘”라고 요청합니다. 원격에 접속하지 못했다면 로컬 자료의 기준일·커밋을 함께 확인합니다.

수정 중인 파일이 있거나 pull이 실패하면 작업을 보존하고 원인을 확인합니다. 강제 reset·덮어쓰기·자동 stash로 해결하지 않습니다.

## 자료를 업데이트하는 방법

| 파일 | 용도 | 갱신 기준 |
|---|---|---|
| [CONTEXT.md](CONTEXT.md) | AI와 팀이 공유하는 배경·규칙·결정 | 공식 공지 또는 팀장이 확인한 결정·상태 반영 |
| [TEAM_BRIEFING.md](TEAM_BRIEFING.md) | 사람이 빠르게 읽는 브리핑 | 공통 컨텍스트가 바뀌면 함께 요약 갱신 |
| [AGENTS.md](AGENTS.md) | AI의 읽기·갱신·검증 방식 | 협업 방식이 바뀔 때 |
| [updates/](updates/README.md) | 각 팀원의 준비 상태와 실행 근거 | 본인이 확인한 결과 기록 |

문서를 고친 사람은 날짜·근거·변경 이유를 명확히 남깁니다. 개인의 추천을 팀 결정으로 바꾸지 않습니다. 새로운 공식 공지와 팀장의 확정 결정이 기존 내용과 다르면 CONTEXT.md와 브리핑을 함께 수정합니다. 충돌하는 근거가 있으면 확인할 항목으로 남깁니다.

저장소 쓰기(Write) 권한을 받은 팀원은 본인 상태 파일을 작성한 뒤 다음처럼 공유할 수 있습니다. 아래 `my-handle`은 본인 GitHub 아이디로 바꿉니다.

```sh
git add updates/my-handle-2026-10-09.md
git commit -m "docs: share my preparation status"
git pull --ff-only
git push origin main
```

pull 또는 push가 거절되면 강제로 밀어 넣지 말고 오류와 작업 상태를 팀장에게 공유합니다. 여러 사람이 동시에 수정한다면 별도 브랜치와 Pull Request로 합칩니다.

## 이 저장소의 범위

이곳은 준비·회의·AI 인수인계용 문서 저장소이며, 실제 구현이나 행사 제출이 완료됐다는 뜻은 아닙니다. 구현 저장소가 정해지면 링크와 실행 방법을 추가합니다. 비공개 저장소를 제출에 사용할 때는 심사자의 접근 방법을 확인해야 합니다.

메일 원문, 개인정보가 든 캡처, 라이선스 키·토큰, 대용량 3D 파일은 올리지 않습니다. 공식 출처 링크와 필요한 분석 요약은 CONTEXT.md에 포함돼 있습니다.
