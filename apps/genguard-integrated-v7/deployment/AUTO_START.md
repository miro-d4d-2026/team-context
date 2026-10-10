# UNO Q 자동 시작과 복구

이 배포는 기존 앱 폴더, 학습된 모델·검토 이력이 들어 있는 `state/`, 부착 기준 파일을 그대로 사용한다. 새로운 모델이나 빈 DB를 덮어씌우지 않는다. 웹·ML·센서 수신은 보드의 `arduino` 사용자로 실행하고, Mac은 USB 접속과 화면 표시만 한다.

## 구조

사용자 systemd `genguard.service` → 전용 감독기 → 기존 gateway + 웹·ML 서버 + 실측 IMU 어댑터.

- 로그인 없이 전원 부팅 시 시작하려면 `arduino` 사용자의 `Linger=yes`가 필요하다.
- 시스템의 기존 `arduino-router.service`는 그대로 둔다. 감독기가 Router 준비와 실제 센서 샘플을 확인하고 시작한다.
- gateway와 웹 API는 `127.0.0.1`에서만 수신한다. 네트워크 전체에 새 포트를 공개하지 않는다.
- 어댑터가 종료되면 그 프로세스만 되살리지 않고, 감독기가 소유한 묶음을 종료한다. systemd가 5초 뒤 다시 시작하여 서버가 기존 부착 기준 파일을 다시 읽게 한다. 첫 실제 패킷의 부착·센서 설정 검증은 계속 적용한다.
- 프로세스 PID 파일을 믿고 죽이거나 이름으로 모든 Python을 종료하지 않는다. 자기 자신이 실행한 자식만 정리한다. 점유 포트가 있으면 오류로 멈추고 재시도한다.
- 로그는 journal로 보내고 이 서비스의 로그 출력 빈도를 제한한다. 앱 폴더에 끝없이 커지는 새 로그 파일을 만들지 않는다. journal 보관 용량은 보드의 기존 journald 설정을 따른다.

## 최초 등록

보드의 기존 `arduino` 계정에서 실행한다. `sudo`로 설치하면 안 된다.

```sh
loginctl enable-linger arduino
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
sh /home/arduino/genguard-v8-20261011/app/deployment/install-autostart.sh
```

`enable-linger`가 권한 부족으로 거절되면 이 한 단계는 보드 관리자에게 요청한다. 설치기는 linger와 사용자 서비스 관리자, 기존 실행 파일·상태 저장소를 먼저 확인한다. `~/.config/systemd/user/genguard.service`를 등록하고 자동 시작만 활성화한다. 기존 수동 프로세스는 자동 종료하지 않는다.

처음 전환할 때는 일관된 SQLite 백업을 남기고, 9877·9883 포트의 명령줄·소유자·경로를 확인한다. 이 설치에 해당하는 기존 수동 센서 → 웹 서버 → gateway PID만 종료한 뒤 아래 명령으로 시작한다. 다른 작업이 사용하는 프로세스면 중단하고 소유자와 조율한다.

```sh
systemctl --user start genguard.service
systemctl --user status genguard.service --no-pager
journalctl --user -u genguard.service -n 60 --no-pager
```

현재는 서비스가 시작됐는지, 자동 시작이 설정됐는지, 실제 전원 재부팅 뒤 복구됐는지가 서로 다른 검증 단계다. 실제 완료 상태는 해당 배포의 `CURRENT_STATUS.md`를 따른다.

## 평상시 관리

```sh
# 상태 및 부팅 자동 시작 확인
systemctl --user is-active genguard.service
systemctl --user is-enabled genguard.service
loginctl show-user arduino -p Linger

# 이 앱 묶음만 중지 / 재시작
systemctl --user stop genguard.service
systemctl --user restart genguard.service

# 자동 시작을 해제하면서 이 앱만 정지
systemctl --user disable --now genguard.service
```

`start-on-unoq.sh`도 서비스가 등록돼 있으면 해당 서비스 시작으로 연결한다. 수동 서버를 하나 더 띄우지 않는다. 자동 복구·중지·재시작은 학습 모델을 다시 만들거나 기록을 삭제하지 않는다. 다만 중단 동안 실측값은 수집되지 않으므로 그 시간을 실제 측정으로 채워 넣지 않는다.

USB를 다시 연결한 Mac에서는 기존 장치에만 화면 접속 터널을 연다.

```sh
adb -s 233936836 forward --no-rebind tcp:9883 tcp:9883
```

전용 외부 전원으로 보드가 계속 켜져 있다면 Mac 브라우저를 닫아도 보드 프로세스는 계속 동작한다. USB가 유일한 전원인 상태에서 케이블을 빼면 보드 자체가 꺼진다.

## 검증 범위

`python3 -m unittest deployment.test_autostart -v`는 자식 종료 시 전체 복구 요청, 자기 자식만 종료, 외부 포트 점유 보호, 누락된 상태를 빈 DB로 대체하지 않음, localhost 바인딩 및 서비스 설정을 검사한다. 이 테스트만으로 실제 부팅 성공을 주장하지 않는다. 보드에서는 서비스 활성·실제 샘플 수신·모델 ID 보존을 확인하고, 재부팅 검증을 별도 기록한다.
