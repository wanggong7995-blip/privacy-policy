"""
Claude 구독 요금제로 AI 요약/분석을 수행하는 공용 헬퍼 (이식 가능 버전).

API 크레딧(ANTHROPIC_API_KEY)을 쓰지 않고, 장기 OAuth 토큰
(CLAUDE_CODE_OAUTH_TOKEN, 1년 유효)으로 인증해 구독 한도 내에서 처리한다.
Windows / macOS / Linux 모두 동작하며, 특정 PC 경로에 의존하지 않는다.

--------------------------------------------------------------------------
새 환경(다른 PC 또는 다른 Claude 계정)에서 준비하는 방법
--------------------------------------------------------------------------
1) Node.js 설치 후 Claude Code CLI 설치
       npm install -g @anthropic-ai/claude-code

2) 사용할 Claude 계정으로 로그인 (구독 계정이어야 함: Pro/Max/Team/Enterprise)
       claude
       /login

3) 장기 토큰 발급 - 출력된 토큰을 복사
       claude setup-token

4) 환경변수로 저장
   Windows(PowerShell):
       setx CLAUDE_CODE_OAUTH_TOKEN "sk-ant-oat01-..."
   macOS/Linux(~/.zshrc 또는 ~/.bashrc에 추가):
       export CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-..."

5) 확인
       python claude_cli.py

주의: 토큰은 발급한 그 계정의 구독 한도를 사용한다. 계정마다 별도로 발급해야 한다.

--------------------------------------------------------------------------
사용법
--------------------------------------------------------------------------
    import claude_cli

    text = claude_cli.ask("다음 기사를 3줄로 요약해줘:\\n...")
    data = claude_cli.ask_json('아래 JSON 형식으로만 응답: {"summary": "..."}')

인증 우선순위 주의:
    ANTHROPIC_AUTH_TOKEN > ANTHROPIC_API_KEY > apiKeyHelper > CLAUDE_CODE_OAUTH_TOKEN
    앞의 두 변수가 환경에 남아 있으면 구독 토큰 대신 그쪽이 쓰여 크레딧 오류가 난다.
    이 모듈은 서브프로세스 환경에서 두 변수를 자동 제거하므로,
    load_dotenv()로 API 키를 읽어들이는 스크립트에서도 안전하다.
"""

import json
import os
import re
import shutil
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"
DEFAULT_TIMEOUT = 150

_cached_cmd: str | None = None


class ClaudeCliError(RuntimeError):
    """claude CLI 호출 실패. 인증/크레딧 문제인 경우 is_auth_error=True."""

    def __init__(self, message: str, is_auth_error: bool = False):
        super().__init__(message)
        self.is_auth_error = is_auth_error


def _candidate_paths() -> list[str]:
    """설치 위치가 PATH에 없을 때 탐색할 후보 경로들."""
    home = os.path.expanduser("~")
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        return [
            os.path.join(appdata, "npm", "claude.cmd"),
            os.path.join(home, ".local", "bin", "claude.exe"),
        ]
    return [
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
        os.path.join(home, ".local", "bin", "claude"),
        os.path.join(home, ".npm-global", "bin", "claude"),
    ]


def find_claude_cmd() -> str:
    """claude 실행 파일 경로. CLAUDE_CMD 환경변수로 직접 지정 가능."""
    global _cached_cmd
    if _cached_cmd:
        return _cached_cmd

    override = os.environ.get("CLAUDE_CMD", "").strip()
    if override:
        _cached_cmd = override
        return _cached_cmd

    found = shutil.which("claude")
    if found:
        _cached_cmd = found
        return _cached_cmd

    for path in _candidate_paths():
        if os.path.exists(path):
            _cached_cmd = path
            return _cached_cmd

    raise ClaudeCliError(
        "claude CLI를 찾을 수 없음. "
        "'npm install -g @anthropic-ai/claude-code'로 설치하거나 "
        "CLAUDE_CMD 환경변수로 경로를 지정하세요."
    )


def _build_env() -> dict:
    """서브프로세스용 환경변수. 구독 토큰이 확실히 쓰이도록 API 키 계열을 제거한다."""
    env = os.environ.copy()

    # Task Scheduler / cron 등 PATH가 축소된 환경 대비: CLI 위치를 PATH에 보강
    try:
        cmd_dir = os.path.dirname(find_claude_cmd())
        if cmd_dir and cmd_dir.lower() not in env.get("PATH", "").lower():
            env["PATH"] = cmd_dir + os.pathsep + env.get("PATH", "")
    except ClaudeCliError:
        pass

    # 이 둘이 남아 있으면 CLAUDE_CODE_OAUTH_TOKEN보다 우선 적용되어
    # 크레딧 부족(400) 또는 인증 오류가 난다.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    return env


def is_ready() -> tuple[bool, str]:
    """호출 가능한 상태인지 사전 점검. (가능 여부, 사유) 반환."""
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if not token:
        return False, ("CLAUDE_CODE_OAUTH_TOKEN 미설정. "
                       "'claude setup-token'으로 발급 후 환경변수로 저장하세요.")
    if not token.startswith("sk-ant-oat01-"):
        return False, (f"CLAUDE_CODE_OAUTH_TOKEN 형식 이상 "
                       f"(길이 {len(token)}, 접두사 불일치). 값이 잘렸을 수 있습니다.")
    try:
        cmd = find_claude_cmd()
    except ClaudeCliError as e:
        return False, str(e)
    return True, f"ok (cmd={cmd}, token len={len(token)})"


def ask(prompt: str, timeout: int = DEFAULT_TIMEOUT, model: str | None = None) -> str:
    """
    프롬프트를 claude CLI에 전달하고 응답 텍스트를 반환한다.
    model을 주면 --model 로 해당 모델을 지정한다. 실패 시 ClaudeCliError 발생.
    """
    cmd = find_claude_cmd()

    # Windows의 .cmd는 셸을 거쳐야 실행된다. POSIX는 리스트 인자가 더 안전.
    if IS_WINDOWS:
        popen_args = f'"{cmd}" -p'
        if model:
            popen_args += f' --model "{model}"'
        use_shell = True
    else:
        popen_args = [cmd, "-p"]
        if model:
            popen_args += ["--model", model]
        use_shell = False

    try:
        proc = subprocess.run(
            popen_args,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=use_shell,
            env=_build_env(),
        )
    except subprocess.TimeoutExpired:
        raise ClaudeCliError(f"claude CLI 응답 시간 초과 ({timeout}초)")
    except Exception as e:
        raise ClaudeCliError(f"claude CLI 호출 실패: {type(e).__name__}: {e}")

    if proc.returncode != 0:
        # 인증/크레딧 오류 메시지는 stderr가 아니라 stdout으로 오는 경우가 많다.
        stdout_msg = (proc.stdout or "").strip()
        stderr_msg = (proc.stderr or "").strip()
        detail = stdout_msg or stderr_msg or "(오류 메시지 없음)"
        lowered = detail.lower()
        is_auth = any(k in lowered for k in
                      ("authenticate", "authentication", "401", "invalid bearer",
                       "oauth", "credit balance"))
        if is_auth:
            raise ClaudeCliError(
                f"claude CLI 인증 실패: {detail[:200]} "
                "/ CLAUDE_CODE_OAUTH_TOKEN 재발급 필요 (claude setup-token)",
                is_auth_error=True,
            )
        raise ClaudeCliError(f"claude CLI 오류 (exit {proc.returncode}): {detail[:300]}")

    result = (proc.stdout or "").strip()
    if not result:
        raise ClaudeCliError("claude CLI가 빈 응답을 반환함")
    return result


def ask_json(prompt: str, timeout: int = DEFAULT_TIMEOUT, model: str | None = None) -> dict:
    """
    JSON 응답을 요구하는 프롬프트용. 코드블록 마크다운을 제거하고 파싱해 반환한다.
    프롬프트에 "JSON만 출력" 지시를 포함시키는 것을 권장.
    """
    raw = ask(prompt, timeout=timeout, model=model)
    clean = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        raise ClaudeCliError(f"JSON 파싱 실패: {e} / 응답 앞부분: {clean[:200]}")


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ok, reason = is_ready()
    print(f"사전 점검: {reason}")
    if not ok:
        sys.exit(1)
    print("응답 테스트:", ask("한 문장으로 자기소개 해줘.", timeout=90))
