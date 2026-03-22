"""
hangul-bypass — IME 없이 어디서든 한글 입력

사용법:
    1. 관리자 권한으로 실행
    2. 오른쪽 Alt로 한/영 전환
    3. 한글 모드에서 타이핑 → 실시간 한글 주입
    4. Ctrl+C: 종료
"""

import argparse
import logging
import time
from collections import deque
import keyboard
from hangul_utils import convert_key

# ── 설정 ──────────────────────────────────────────────────────
TOGGLE_KEY = ["right alt"]

# ── 한글 조합 ─────────────────────────────────────────────────
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
SHIFT_KEYS = {'R','E','Q','T','W','O','P'}
KOREAN_KEYS = set(CONS.keys()) | {k for k in VOWS.keys() if len(k) == 1}

log = logging.getLogger(__name__)


def engkor(text):
    """영문 키 배열 → 한글 조합"""
    result_2 = convert_key(''.join(text), 'ko')
    result_1 = ''
    split_index = 0
    len_text = len(text)
    last_t = text[len_text - 1]
    before_last_t = text[len_text - 2]
    if len(result_2) == 2:
        result_1 = result_2[0]
        result_2 = result_2[1]
        split_index = len_text - 1
        if last_t in VOWS and before_last_t in CONS:
            split_index -= 1
    return result_1, result_2, split_index


# ── 상태 관리 ─────────────────────────────────────────────────
class State:
    def __init__(self):
        self.mode = False         # True: 한글, False: 영문
        self.fixed = ''
        self.korean_keys = []

    def toggle(self):
        self._cursor()
        self.fixed = ''
        self.korean_keys.clear()
        self.mode = not self.mode

    def record(self, key):
        if key == 'backspace':
            self._backspace()
        elif key == 'space':
            cursor = self._cursor()
            self.fixed += cursor + ' '
            self.korean_keys.clear()
        elif len(key) == 1:
            self._insert(key)

    def current(self):
        cursor = self._cursor()    # fixed를 먼저 수정
        return self.fixed + cursor  # 수정된 fixed를 읽음

    def clear(self):
        self.fixed = ''
        self.korean_keys.clear()

    def _cursor(self):
        if not self.korean_keys:
            return ''
        fixed_str, cursor, split_index = engkor(self.korean_keys)
        self.fixed += fixed_str
        self.korean_keys = self.korean_keys[split_index:]
        return cursor

    def _backspace(self):
        if self.korean_keys:
            self.korean_keys.pop()
        else:
            self.fixed = self.fixed[:-1]

    def _insert(self, word):
        if self.mode:
            check = word if word in SHIFT_KEYS else word.lower()
            if check in KOREAN_KEYS:
                if word not in SHIFT_KEYS:
                    word = word.lower()
                self.korean_keys.append(word)
            else:
                cursor = self._cursor()
                self.fixed += cursor + word
                self.korean_keys.clear()
        else:
            cursor = self._cursor()
            self.fixed += cursor + word
            self.korean_keys.clear()


# ── 주입 (keyboard.write + 딜레이) ───────────────────────────
WRITE_DELAY = 0.01  # 글자 간 딜레이 (초)


def inject_diff(prev, curr):
    bs_count = len(prev) - len(_common_prefix(prev, curr))
    new_chars = curr[len(prev) - bs_count:]
    log.debug("inject_diff: %r → %r (bs=%d, new=%r)",
              prev, curr, bs_count, new_chars)

    for _ in range(bs_count):
        keyboard.press_and_release('backspace')
    if bs_count > 0 and new_chars:
        time.sleep(0.02)  # 백스페이스 처리 대기
    if new_chars:
        keyboard.write(new_chars, delay=WRITE_DELAY)
        log.debug("write: %r", new_chars)


def _common_prefix(a, b):
    i = 0
    for x, y in zip(a, b):
        if x != y:
            break
        i += 1
    return a[:i]


# ── 메인 루프 ─────────────────────────────────────────────────
def main():
    state = State()
    prev_text = ''
    ctrl_held = False
    shift_held = False
    alt_held = False

    print("=" * 45)
    print("  hangul-bypass")
    print("=" * 45)
    print("  R-Alt : 한/영 전환")
    print("  Enter : 한/영 전환 (채팅 열기/송출)")
    print("  Esc   : 영문 모드 (채팅 닫기)")
    print("  Ctrl+C: 종료")
    print("  * 한글 모드에서 CapsLock 무시됨")
    print("=" * 45)

    MAX_LOG = 5
    mode_log = deque(maxlen=MAX_LOG)
    mode_log.append("[영문 모드]")
    printed_lines = 0

    def print_mode_log():
        nonlocal printed_lines
        if printed_lines > 0:
            print(f"\033[{printed_lines}A", end="")  # 이전 출력만큼 위로
        for msg in mode_log:
            print(f"\033[K{msg}")
        printed_lines = len(mode_log)

    def log_mode(msg):
        mode_log.append(msg)
        print_mode_log()

    print("[영문 모드]")
    printed_lines = 1

    def on_key(event):
        nonlocal prev_text, ctrl_held, shift_held

        try:
            return _on_key(event)
        except Exception as e:
            log.error("on_key 에러: %s", e)
            return True  # 에러 시 키 통과 (먹통 방지)

    def _on_key(event):
        nonlocal prev_text, ctrl_held, shift_held, alt_held

        key = event.name
        is_down = event.event_type == 'down'

        # 디버그: 모든 키 이름 출력 (--debug 모드)
        log.debug("event: name=%r type=%s", key, event.event_type)

        # Ctrl 추적
        if key in ('ctrl', 'left ctrl', 'right ctrl'):
            ctrl_held = is_down
            return True

        # Alt 추적
        if key in ('alt', 'left alt', 'right alt'):
            alt_held = is_down
            # R-Alt 토글은 아래에서 별도 처리
            if key != 'right alt':
                return True

        # Shift 추적
        if key in ('shift', 'left shift', 'right shift'):
            shift_held = is_down
            if state.mode:
                return False  # 한글 모드에서 suppress
            return True

        # key-up 무시
        if not is_down:
            return True

        # Alt: 한/영 전환 (Ctrl/Alt 조합 체크보다 먼저)
        if key in TOGGLE_KEY:
            state.toggle()
            prev_text = ''
            mode_str = "한글" if state.mode else "영문"
            log.debug("toggle → %s", mode_str)
            log_mode(f"[{mode_str} 모드] (R-Alt)")
            return True

        # Ctrl/Alt 조합 통과
        if ctrl_held or alt_held:
            return True

        # Enter: 한/영 토글 후 통과
        if key == 'enter':
            state.toggle()
            prev_text = ''
            mode_str = "한글" if state.mode else "영문"
            log_mode(f"[{mode_str} 모드] (Enter)")
            return True

        # Esc: 영문 모드로 전환 후 통과
        if key in ('escape', 'esc'):
            if state.mode:
                state.clear()
                prev_text = ''
                state.mode = False
                log_mode("[영문 모드] (Esc)")
            return True

        # 영문 모드: 통과
        if not state.mode:
            return True

        # ── 한글 모드: 키 suppress ──
        log.debug("key: %r  mode=한글", key)

        # Backspace
        if key == 'backspace':
            if not state.korean_keys and not state.fixed:
                return True  # 조합 중 아니면 통과
            state.record(key)
            curr_text = state.current()
            inject_diff(prev_text, curr_text)
            prev_text = curr_text
            return False  # suppress

        # Space: 조합 확정 + 실제 스페이스 키 전송
        if key == 'space':
            state.clear()
            prev_text = ''
            keyboard.press_and_release('space')
            return False  # suppress


        # 문자 키만 suppress
        if len(key) == 1:
            key = key.lower()   # CapsLock 무시
            if shift_held:
                key = key.upper()
            state.record(key)
            curr_text = state.current()
            inject_diff(prev_text, curr_text)
            prev_text = curr_text
            if not state.korean_keys:
                state.fixed = ''
                prev_text = ''
            return False  # suppress

        # 그 외 키(Tab, F키, 방향키 등): 조합 확정 후 통과
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
