"""
hangul-bypass — IME 없이 어디서든 한글 입력

Windows IME를 사용하지 않고, 영문 키보드 입력을 가로채어
실시간으로 한글로 변환·주입하는 도구.
게임(헬다이버즈2 등) 채팅처럼 IME가 불안정한 환경에서 유용.

주입 방식: keyboard.write(delay=0.01) — SendInput + KEYEVENTF_UNICODE
키 차단: keyboard.hook(suppress=True) — 저수준 키보드 훅

사용법:
    1. python hangul_bypass.py 실행
    2. R-Alt / Enter로 한/영 전환
    3. 한글 모드에서 타이핑 → 실시간 한글 주입
    4. Ctrl+C: 종료
"""

import argparse
import logging
import sys
import io
import time
import keyboard
from hangul_utils import convert_key

# Windows 콘솔 cp949 → UTF-8 강제 (유니코드 박스 문자 출력용)
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ── 설정 ──────────────────────────────────────────────────────
VERSION = "0.2.0"
TOGGLE_KEY = ["right alt", "hangul"]

# ── 한글 조합 매핑 (두벌식) ───────────────────────────────────
# 영문 키 → 한글 자모 매핑. 대문자는 쌍자음/쌍모음.
CONS = {
    'r':'ㄱ','R':'ㄲ','s':'ㄴ','e':'ㄷ','E':'ㄸ','f':'ㄹ','a':'ㅁ',
    'q':'ㅂ','Q':'ㅃ','t':'ㅅ','T':'ㅆ','d':'ㅇ','w':'ㅈ','W':'ㅉ',
    'c':'ㅊ','z':'ㅋ','x':'ㅌ','v':'ㅍ','g':'ㅎ'
}
VOWS = {
    'k':'ㅏ','o':'ㅐ','i':'ㅑ','O':'ㅒ','j':'ㅓ','p':'ㅔ','u':'ㅕ',
    'P':'ㅖ','h':'ㅗ','hk':'ㅘ','ho':'ㅙ','hl':'ㅚ','y':'ㅛ','n':'ㅜ',
    'nj':'ㅝ','np':'ㅞ','nl':'ㅟ','b':'ㅠ','m':'ㅡ','ml':'ㅢ','l':'ㅣ'
}
SHIFT_KEYS = {'R','E','Q','T','W','O','P'}  # Shift로 입력하는 쌍자음/쌍모음
KOREAN_KEYS = set(CONS.keys()) | {k for k in VOWS.keys() if len(k) == 1}

log = logging.getLogger(__name__)


def engkor(text):
    """영문 키 시퀀스 → 한글 조합 결과 분리.

    Returns:
        (fixed_str, cursor, split_index)
        - fixed_str: 확정된 글자 (조합 완료)
        - cursor: 아직 조합 중인 글자
        - split_index: korean_keys를 자를 위치
    """
    result_2 = convert_key(''.join(text), 'ko')
    result_1 = ''
    split_index = 0
    len_text = len(text)
    last_t = text[len_text - 1]
    before_last_t = text[len_text - 2] if len_text >= 2 else ''
    if len(result_2) == 2:
        # 2글자 → 앞 글자는 확정, 뒤 글자는 조합 중
        result_1 = result_2[0]
        result_2 = result_2[1]
        split_index = len_text - 1
        # 마지막이 모음이고 그 앞이 자음이면, 자음을 조합 중으로 유지
        if last_t in VOWS and before_last_t in CONS:
            split_index -= 1
    return result_1, result_2, split_index


# ── 상태 관리 ─────────────────────────────────────────────────
class State:
    """한글 조합 상태를 추적.

    - mode: True=한글, False=영문
    - fixed: 확정된 텍스트 (화면에 이미 주입됨)
    - korean_keys: 현재 조합 중인 영문 키 시퀀스
    """

    def __init__(self):
        self.mode = False
        self.fixed = ''
        self.korean_keys = []

    def toggle(self):
        """한/영 전환. 조합 중이던 텍스트는 확정 후 상태 초기화."""
        self._cursor()
        self.fixed = ''
        self.korean_keys.clear()
        self.mode = not self.mode

    def record(self, key):
        """키 입력을 상태에 반영."""
        if key == 'backspace':
            self._backspace()
        elif key == 'space':
            cursor = self._cursor()
            self.fixed += cursor + ' '
            self.korean_keys.clear()
        elif len(key) == 1:
            self._insert(key)

    def current(self):
        """현재 화면에 표시되어야 할 전체 텍스트 반환."""
        cursor = self._cursor()
        return self.fixed + cursor

    def clear(self):
        """상태 초기화 (화면 텍스트는 유지)."""
        self.fixed = ''
        self.korean_keys.clear()

    def _cursor(self):
        """조합 중인 키를 한글로 변환. 확정된 부분은 fixed로 이동."""
        if not self.korean_keys:
            return ''
        fixed_str, cursor, split_index = engkor(self.korean_keys)
        self.fixed += fixed_str
        self.korean_keys = self.korean_keys[split_index:]
        return cursor

    def _backspace(self):
        """조합 중이면 마지막 키 제거, 아니면 확정 텍스트 마지막 글자 제거."""
        if self.korean_keys:
            self.korean_keys.pop()
        else:
            self.fixed = self.fixed[:-1]

    def _insert(self, word):
        """키를 한글 조합에 추가. 한글 키가 아니면 확정 후 그대로 삽입."""
        check = word if word in SHIFT_KEYS else word.lower()
        if self.mode and check in KOREAN_KEYS:
            if word not in SHIFT_KEYS:
                word = word.lower()
            self.korean_keys.append(word)
        else:
            # 비한글 문자: 조합 확정 후 문자 그대로 추가
            cursor = self._cursor()
            self.fixed += cursor + word
            self.korean_keys.clear()


# ── 주입 (keyboard.write + 딜레이) ───────────────────────────
WRITE_DELAY = 0.01   # 글자 간 딜레이 (초) — 게임 입력 안정성용
BS_SETTLE = 0.02     # 백스페이스 후 대기 (초) — 게임이 처리할 시간


def inject_diff(prev, curr):
    """이전 텍스트와 현재 텍스트의 차이만큼 백스페이스 + 재입력.

    공통 접두사를 유지하고, 달라진 부분만 교체하여 최소한의 키 이벤트 발생.
    """
    bs_count = len(prev) - len(_common_prefix(prev, curr))
    new_chars = curr[len(prev) - bs_count:]
    log.debug("inject_diff: %r → %r (bs=%d, new=%r)",
              prev, curr, bs_count, new_chars)

    for _ in range(bs_count):
        keyboard.press_and_release('backspace')
    if bs_count > 0 and new_chars:
        time.sleep(BS_SETTLE)
    if new_chars:
        keyboard.write(new_chars, delay=WRITE_DELAY)
        log.debug("write: %r", new_chars)


def _common_prefix(a, b):
    """두 문자열의 공통 접두사 반환."""
    i = 0
    for x, y in zip(a, b):
        if x != y:
            break
        i += 1
    return a[:i]


# ── 메인 루프 ─────────────────────────────────────────────────
def main():
    state = State()
    prev_text = ''        # 화면에 주입된 텍스트 (inject_diff 기준점)
    ctrl_held = False
    shift_held = False
    alt_held = False

    # ── ANSI 색상 ──
    C_RESET = "\033[0m"
    C_BOLD  = "\033[1m"
    C_DIM   = "\033[2m"
    C_CYAN  = "\033[36m"
    C_GREEN = "\033[32m"
    C_YELLOW = "\033[33m"
    C_WHITE = "\033[37m"

    # ── 배너 (터미널 폭 반응형) ──
    import os
    import re
    import unicodedata

    def vlen(s):
        """ANSI 이스케이프 제외한 터미널 표시 폭 계산."""
        clean = re.sub(r'\033\[[0-9;]*m', '', s)
        w = 0
        for c in clean:
            cat = unicodedata.east_asian_width(c)
            w += 2 if cat in ('F', 'W') else 1
        return w

    def pad(s, width):
        """문자열 s를 터미널 폭 width에 맞게 공백 패딩."""
        return s + ' ' * max(0, width - vlen(s))

    term_w = os.get_terminal_size().columns
    TW = term_w - 2          # 박스 안쪽 폭 (양쪽 │ 제외)
    L = (TW * 2) // 5        # 왼쪽 패널 40% (Claude Code 비율)
    R = TW - L - 1           # 오른쪽 패널 폭 (중앙 │ 제외)

    def row(left="", right=""):
        return f"│{pad(left, L)}{C_DIM}│{C_RESET}{pad(right, R)}│"

    # 모드 표시줄 (오른쪽에 Enter 키바인딩 표시)
    MODE_RIGHT = f" {C_YELLOW}Enter{C_RESET}  {C_DIM}·{C_RESET} 영문 모드 (채팅 열기/송출)"

    def mode_row(is_korean):
        if is_korean:
            left = f"   {C_GREEN}● 현재 상태: 한글 모드{C_RESET}"
        else:
            left = f"   {C_WHITE}○ 현재 상태: 영문 모드{C_RESET}"
        return row(left, MODE_RIGHT)

    # 타이틀을 상단 테두리에 삽입
    title = f" HD2 Hangul Bypass v{VERSION} "
    top_border = f"╭───{C_CYAN}{C_BOLD}{title}{C_RESET}{'─' * (TW - len(title) - 3)}╮"

    print(top_border)
    print(row())
    print(row(f"   {C_WHITE}{C_BOLD}슈퍼 지구에 오신 것을 환영합니다{C_RESET}",
              f" {C_WHITE}키 바인딩{C_RESET}"))
    print(row(f"   {C_DIM}[ 자유. 평등. 한글. ]{C_RESET}",
              f" {C_DIM}{'─' * (R - 1)}{C_RESET}"))
    print(row("",
              f" {C_YELLOW}R-Alt{C_RESET} / {C_YELLOW}한/영{C_RESET} {C_DIM}·{C_RESET} 한/영 전환"))
    print(mode_row(False))  # ← 모드 표시줄 (나중에 덮어씀)
    print(row("",
              f" {C_YELLOW}Esc{C_RESET}    {C_DIM}·{C_RESET} 영문 모드 (채팅 닫기 등)"))
    print(row("",
              f" {C_YELLOW}Ctrl+C{C_RESET} {C_DIM}·{C_RESET} 종료"))
    print(row(f"   {C_DIM}IME 없이 어디서든 한글 입력{C_RESET}",
              f" {C_DIM}* CapsLock은 한글 모드에서 무시됨{C_RESET}"))
    print(f"╰{'─' * TW}╯")

    # 모드줄 위치 계산:
    # top(1) empty(2) 슈퍼지구(3) 자유(4) mode(5)
    # Enter(6) Esc(7) Ctrl+C(8) IME(9) ╰(10) cursor(11)
    # top(1) empty(2) 슈퍼지구(3) 자유(4) R-Alt(5) mode(6)
    # Esc(7) Ctrl+C(8) IME(9) ╰(10) cursor(11)
    # MODE_LINE_UP = 11 - 6 = 5
    MODE_LINE_UP = 5

    # ── 모드 전환 표시 ──
    import ctypes

    def set_title(mode_str):
        """콘솔 타이틀에 현재 모드 표시 (작업표시줄에서 확인 가능)."""
        indicator = "🟢 한글 모드" if mode_str == "한글" else "⚪ 영문 모드"
        ctypes.windll.kernel32.SetConsoleTitleW(f"hangul-bypass — {indicator}")

    def log_mode(is_korean, source):
        """모드 전환 시 박스 내 모드줄 + 타이틀 업데이트."""
        mode = "한글" if is_korean else "영문"
        set_title(mode)
        # 커서를 모드줄로 이동 → 덮어쓰기 → 원위치
        print(f"\033[{MODE_LINE_UP}A\r{mode_row(is_korean)}\033[{MODE_LINE_UP}B\r",
              end="", flush=True)
        log.debug("모드 전환 → %s (%s)", mode, source)
        log.debug("모드 전환 → %s (%s)", mode, source)

    log_mode(False, "시작")

    # ── 키보드 훅 ──
    def on_key(event):
        """키 이벤트 래퍼. 에러 발생 시 키를 통과시켜 먹통 방지."""
        try:
            return _on_key(event)
        except Exception as e:
            log.error("on_key 에러: %s", e)
            return True

    def _on_key(event):
        """키 이벤트 핵심 처리.

        반환값:
            True  → 키를 OS/앱에 전달 (통과)
            False → 키를 차단 (suppress)
        """
        nonlocal prev_text, ctrl_held, shift_held, alt_held

        key = event.name
        is_down = event.event_type == 'down'

        log.debug("event: name=%r type=%s", key, event.event_type)

        # ── 수식키 추적 (항상 통과) ──

        if key in ('ctrl', 'left ctrl', 'right ctrl'):
            ctrl_held = is_down
            return True

        if key in ('alt', 'left alt', 'right alt'):
            alt_held = is_down
            if key != 'right alt':  # R-Alt는 토글용, 아래에서 별도 처리
                return True

        if key in ('shift', 'left shift', 'right shift'):
            shift_held = is_down
            return True

        # key-up은 처리하지 않음
        if not is_down:
            return True

        # ── 모드 전환 키 ──

        # R-Alt: 한/영 토글 (Ctrl/Alt 조합 체크보다 먼저 처리)
        if key in TOGGLE_KEY:
            state.toggle()
            prev_text = ''
            log_mode(state.mode, "R-Alt")
            return True

        # Ctrl/Alt 조합은 무조건 통과 (Ctrl+C, Alt+Tab 등)
        if ctrl_held or alt_held:
            return True

        # Enter: 영문 모드 강제 전환 (채팅 송출 후 바로 게임 조작 가능)
        if key == 'enter':
            if state.mode:
                state.clear()
                prev_text = ''
                state.mode = False
                log_mode(False, "Enter")
            return True

        # Esc: 영문 모드 강제 전환 (게임 채팅 닫기)
        if key in ('escape', 'esc'):
            if state.mode:
                state.clear()
                prev_text = ''
                state.mode = False
                log_mode(False, "Esc")
            return True

        # ── 영문 모드: 모든 키 통과 ──
        if not state.mode:
            return True

        # ── 한글 모드: 키 가로채기 ──
        log.debug("key: %r  mode=한글", key)

        # Backspace: 조합 중이면 마지막 키 제거, 아니면 통과
        if key == 'backspace':
            if not state.korean_keys and not state.fixed:
                return True
            state.record(key)
            curr_text = state.current()
            inject_diff(prev_text, curr_text)
            prev_text = curr_text
            return False

        # Space: 조합 확정 + 실제 스페이스 키 전송
        # keyboard.write(' ')는 게임에서 무시되므로 press_and_release 사용
        if key == 'space':
            state.clear()
            prev_text = ''
            keyboard.press_and_release('space')
            return False

        # 문자 키: 한글 조합 처리
        if len(key) == 1:
            key = key.lower()       # CapsLock 무시: 항상 소문자 기준
            if shift_held:
                key = key.upper()   # Shift만 대문자 기준 (쌍자음 등)
            state.record(key)
            curr_text = state.current()
            inject_diff(prev_text, curr_text)
            prev_text = curr_text
            if not state.korean_keys:
                # 조합 완료 (비한글 문자 등): 상태 리셋
                state.fixed = ''
                prev_text = ''
            return False

        # 그 외 키 (Tab, F키, 방향키 등): 조합 확정 후 통과
        state.clear()
        prev_text = ''
        return True

    keyboard.hook(on_key, suppress=True)
    keyboard.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="hangul-bypass")
    parser.add_argument("--debug", action="store_true", help="디버그 로그 출력")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    main()
