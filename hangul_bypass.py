"""
hangul-bypass — IME 없이 게임 채팅에 한글 입력
- IME를 막아둔 게임/프로그램에서 한글 채팅 가능
- 오버레이 없이 게임 채팅창에 바로 표시됨
- WM_CHAR로 유니코드 직접 주입

설치:
    pip install keyboard hangul-utils

사용법:
    1. 관리자 권한으로 실행
    2. 게임에서 Enter로 채팅창 열기
    3. 한글 타이핑 → 게임 채팅창에 실시간 표시
    4. \\ 로 전송, ESC로 취소
    5. Alt로 한/영 전환
"""

import ctypes
import ctypes.wintypes
import time
import keyboard
from hangul_utils import convert_key

# ── 설정 ──────────────────────────────────────────────────────
GAME_TITLE = "HELLDIVERS™ 2"
SEND_KEY   = "\\"
EXIT_KEY   = "esc"
TOGGLE_KEY = ["alt", "right alt"]

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
        self.mode = True          # True: 한글, False: 영문
        self.fixed = ''
        self.korean_keys = []

    def record(self, key):
        if key in TOGGLE_KEY:
            self.mode = not self.mode
            return
        if key == 'backspace':
            self._backspace()
        elif key == 'space':
            self._insert(' ')
        elif len(key) == 1:
            self._insert(key)

    def current(self):
        return self.fixed + self._cursor()

    def flush(self):
        result = self.fixed + self._cursor()
        self.clear()
        return result

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
            if word not in SHIFT_KEYS:
                word = word.lower()
            self.korean_keys.append(word)
        else:
            cursor = self._cursor()
            self.fixed += cursor + word
            self.korean_keys.clear()


# ── WM_CHAR 주입 ──────────────────────────────────────────────
user32 = ctypes.windll.user32
WM_CHAR    = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP   = 0x0101
VK_BACK    = 0x08
VK_RETURN  = 0x0D


def find_window():
    hwnd = user32.FindWindowW(None, GAME_TITLE)
    return hwnd if hwnd else None


def send_char(hwnd, char):
    user32.PostMessageW(hwnd, WM_CHAR, ord(char), 1)
    time.sleep(0.003)


def send_backspace(hwnd):
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_BACK, 1)
    time.sleep(0.003)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_BACK, 1)
    time.sleep(0.003)


def send_enter(hwnd):
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 1)
    time.sleep(0.005)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 1)


def inject_diff(hwnd, prev, curr):
    """이전/현재 문자열 차이만큼 백스페이스 + 새 글자 주입"""
    common = 0
    for a, b in zip(prev, curr):
        if a == b:
            common += 1
        else:
            break

    for _ in range(len(prev) - common):
        send_backspace(hwnd)

    for ch in curr[common:]:
        send_char(hwnd, ch)


# ── 메인 루프 ─────────────────────────────────────────────────
def main():
    state = State()
    typing = False
    prev_text = ''

    print("=" * 45)
    print(f"  hangul-bypass → {GAME_TITLE}")
    print("=" * 45)
    print(f"  enter : 채팅창 열기")
    print(f"  {SEND_KEY}     : 전송")
    print(f"  {EXIT_KEY}    : 취소")
    print(f"  alt   : 한/영 전환")
    print(f"  Ctrl+C: 종료")
    print("=" * 45)

    def on_key(event):
        nonlocal typing, prev_text

        key = event.name
        hwnd = find_window()

        if not hwnd:
            return

        # ── 채팅 중이 아닐 때 ──
        if not typing:
            if key == 'enter':
                send_enter(hwnd)
                typing = True
                state.clear()
                prev_text = ''
                print("[채팅 모드 ON]")
                return keyboard.block_key(key)
            return

        # ── 채팅 중 ──
        if key == SEND_KEY:
            text = state.flush()
            inject_diff(hwnd, prev_text, text)
            time.sleep(0.02)
            send_enter(hwnd)
            typing = False
            prev_text = ''
            print(f"\n[전송] {text}")
            return keyboard.block_key(key)

        if key == EXIT_KEY:
            inject_diff(hwnd, prev_text, '')
            state.clear()
            typing = False
            prev_text = ''
            print("\n[취소]")
            return

        if key in TOGGLE_KEY:
            state.record(key)
            return keyboard.block_key(key)

        if key == 'backspace':
            state.record(key)
            curr_text = state.current()
            inject_diff(hwnd, prev_text, curr_text)
            prev_text = curr_text
            print(f"\r[버퍼] {curr_text}    ", end='', flush=True)
            return keyboard.block_key(key)

        if key == 'space':
            state.record('space')
            curr_text = state.current()
            inject_diff(hwnd, prev_text, curr_text)
            prev_text = curr_text
            print(f"\r[버퍼] {curr_text}    ", end='', flush=True)
            return keyboard.block_key(key)

        if len(key) == 1:
            state.record(key)
            curr_text = state.current()
            inject_diff(hwnd, prev_text, curr_text)
            prev_text = curr_text
            print(f"\r[버퍼] {curr_text}    ", end='', flush=True)
            return keyboard.block_key(key)

    keyboard.on_press(on_key, suppress=True)
    keyboard.wait()


if __name__ == "__main__":
    main()
