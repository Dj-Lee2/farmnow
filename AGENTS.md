# AGENTS.md

이 저장소에서 일하는 모든 도구(Claude Code, Hermes, OpenClaw, Codex)가 따르는 규칙.

## 작업 위치

- 원본은 GitHub `Dj-Lee2/farmnow`의 main이다(remote 이름: 로컬 `github`, 서버 `origin`). 공공 GitLab `dlwogud/farmnow`는 같은 기록의 사본이다.
- 작업하는 곳: 로컬 클론 또는 서버의 `/srv/farmnow`(같은 저장소가 연결된 운영 폴더, 30분마다 `deploy/update.sh`가 수집·빌드).

## 순서

1. 시작 전에 GitHub main을 `git pull --ff-only`로 받는다.
2. 고친 뒤 `python -m pytest -q tests`를 통과시키고 커밋한다. 메시지는 짧은 제목 한 줄, AI 표기(Co-Authored-By 등)는 넣지 않는다.
3. GitHub과 GitLab 양쪽에 push한다. GitLab은 올릴 때 보안 검사(Semgrep `p/security-audit`·OSV·Trivy)를 하며 걸리면 push 전체가 거부된다.
   - 템플릿 링크: 사이트 안은 `href="./{{ x }}"`, 외부는 `href="https://{{ x|hp }}"`, 같은 쪽은 `href="#{{ x }}"`. `href="{{ x }}"`는 검사에 걸린다.
   - 해시는 SHA-256, `requirements.txt`는 하위 의존성까지 `==`로 고정한다.
4. 서버 반영: 서버 폴더에서 `git pull --ff-only` 후 다음 갱신(30분 이내)을 기다리거나 `deploy/update.sh`를 실행한다.

## 지키는 것

- `.env`(인증키), `data/`, `site/`, `logs/`, `vendor/`는 커밋하지 않는다.
- 서버 IP·계정명 같은 운영 정보를 저장소에 적지 않는다.
