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


# ── 주입 ─────────────────────────────────────────────────────
def send_char(char):
    log.debug("send_char: %r (U+%04X)", char, ord(char))
    keyboard.write(char, delay=0)


def send_backspace():
    log.debug("send_backspace")
    keyboard.press_and_release('backspace')


def inject_diff(prev, curr):
    common = 0
    for a, b in zip(prev, curr):
        if a == b:
            common += 1
        else:
            break

    bs_count = len(prev) - common
    new_chars = curr[common:]
    log.debug("inject_diff: %r → %r (common=%d, bs=%d, new=%r)",
              prev, curr, common, bs_count, new_chars)

    for _ in range(bs_count):
        send_backspace()
    for ch in new_chars:
        send_char(ch)


# ── 메인 루프 ─────────────────────────────────────────────────
def main():
    state = State()
    prev_text = ''
    ctrl_held = False

    print("=" * 45)
    print("  hangul-bypass")
    print("=" * 45)
    print(f"  R-Alt : 한/영 전환")
    print(f"  Ctrl+C: 종료")
    print("=" * 45)
    print("[영문 모드]")

    def on_key(event):
        nonlocal prev_text, ctrl_held

        key = event.name
        is_down = event.event_type == 'down'

        # Ctrl 추적
        if key in ('ctrl', 'left ctrl', 'right ctrl'):
            ctrl_held = is_down
            return

        # key-up 무시
        if not is_down:
            return

        # Ctrl 조합 통과
        if ctrl_held:
            return

        # Alt: 한/영 전환
        if key in TOGGLE_KEY:
            state.toggle()
            prev_text = ''
            mode_str = "한글" if state.mode else "영문"
            log.debug("toggle → %s", mode_str)
            print(f"\r[{mode_str} 모드]")
            return

        # 영문 모드: 통과
        if not state.mode:
            return

        # ── 한글 모드 ──
        log.debug("key: %r  mode=한글", key)

        # Backspace
        if key == 'backspace':
            leaked_prev = prev_text[:-1] if prev_text else ''
            state.record(key)
            curr_text = state.current()
            inject_diff(leaked_prev, curr_text)
            prev_text = curr_text
            return

        # Space
        if key == 'space':
            state.record('space')
            curr_text = state.current()
            inject_diff(prev_text + ' ', curr_text)
            state.fixed = ''
            prev_text = ''
            return

        # 문자 키
        if len(key) == 1:
            state.record(key)
            curr_text = state.current()
            inject_diff(prev_text + key, curr_text)
            prev_text = curr_text
            if not state.korean_keys:
                state.fixed = ''
                prev_text = ''
            return

    keyboard.hook(on_key)
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
