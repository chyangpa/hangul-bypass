"""
hangul-bypass — HELLDIVERS™ 2 전용 한글 입력기

게임 채팅창에서 Windows IME가 동작하지 않는 문제를 우회.
영문 키보드 입력을 가로채 실시간으로 한글로 변환·주입한다.

구조:
    훅 스레드  — 키 억제 결정 + 모드 전환 (블로킹 없음)
    처리 스레드 — 큐에서 키를 꺼내 한글 조합 + inject_diff 순차 실행

주입 방식: keyboard.write() — SendInput + KEYEVENTF_UNICODE
키 차단:   keyboard.hook(suppress=True) — 저수준 키보드 훅
"""

import argparse
import ctypes
import ctypes.wintypes
import logging
import os
import queue
import re
import sys
import io
import threading
import time
import unicodedata
import keyboard
import mouse
from hangul_utils import convert_key

# Windows 콘솔 cp949 → UTF-8 강제 (유니코드 박스 문자 출력용)
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ── 설정 ──────────────────────────────────────────────────────
VERSION = "0.7.4"
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
WRITE_DELAY = 0        # 글자 간 딜레이 (초)
PASTE_DELAY = 0.0029   # 붙여넣기 글자 간 딜레이 (초) — 30fps 기준 최적값
BS_SETTLE = 0.034      # 백스페이스 후 대기 (초) — 30fps 기준 1프레임


def _send_unicode(text, delay=0):
    """SendInput + KEYEVENTF_UNICODE로 유니코드 문자열 직접 전송.

    keyboard.write()와 달리 눌린 키를 release/re-press하지 않아
    훅의 수식키 추적에 간섭하지 않음.
    """
    INPUT_KEYBOARD = 1
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                     ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                     ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                     ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                     ("time", ctypes.c_ulong),
                     ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class INPUT(ctypes.Structure):
        class _INPUT(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
        _fields_ = [("type", ctypes.c_ulong), ("ii", _INPUT)]

    for char in text:
        code = ord(char)
        inp_down = INPUT(type=INPUT_KEYBOARD)
        inp_down.ii.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)
        inp_up = INPUT(type=INPUT_KEYBOARD)
        inp_up.ii.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
        arr = (INPUT * 2)(inp_down, inp_up)
        ctypes.windll.user32.SendInput(2, arr, ctypes.sizeof(INPUT))
        if delay:
            time.sleep(delay)


def get_clipboard_text():
    """Win32 API로 클립보드 유니코드 텍스트 읽기."""
    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # 64비트 Windows: 포인터/핸들 타입 명시 (기본 c_int → 잘림/오버플로 방지)
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not user32.OpenClipboard(0):
        log.debug("clipboard: OpenClipboard 실패")
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            log.debug("clipboard: GetClipboardData 실패 (CF_UNICODETEXT 없음)")
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            log.debug("clipboard: GlobalLock 실패")
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


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
    chat_open = False     # 채팅창 열림 여부
    chat_mode = True     # 채팅창 모드 (True=한글, False=영문) — 기본 한글
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
    L = TW // 2               # 왼쪽 패널 50%
    R = TW - L - 1           # 오른쪽 패널 폭 (중앙 │ 제외)

    def row(left="", right=""):
        return f"│{pad(left, L)}{C_DIM}│{C_RESET}{pad(right, R)}│"

    # ── 배너 행 정의 ──
    # 각 행은 (id, left, right) 튜플. id가 있으면 런타임에 덮어쓸 수 있음.
    def mode_left(is_korean):
        if is_korean:
            return f"   {C_GREEN}● 현재 상태: 한글 모드{C_RESET}"
        return f"   {C_WHITE}○ 현재 상태: 영문 모드{C_RESET}"

    def chat_left(is_open, chat_mode=False):
        mode_str = "한글" if chat_mode else "영문"
        if is_open:
            return f"   {C_YELLOW}▶ 채팅창: 열림 ({mode_str} 모드){C_RESET}"
        return f"   {C_DIM}■ 채팅창: 닫힘 ({mode_str} 모드){C_RESET}"

    banner_rows = [
        (None,   "",
                 ""),
        (None,   f"   {C_WHITE}{C_BOLD}슈퍼 지구에 오신 것을 환영합니다{C_RESET}",
                 f" {C_WHITE}키 바인딩{C_RESET}"),
        (None,   f"   {C_DIM}[ 자유. 평등. 한글. ]{C_RESET}",
                 f" {C_DIM}{'─' * (R - 1)}{C_RESET}"),
        (None,   "",
                 f" {C_YELLOW}Enter{C_RESET}  {C_DIM}·{C_RESET} 채팅창 열기 / 보내기"),
        ("mode", mode_left(False),
                 f" {C_YELLOW}R-Alt(한/영){C_RESET} {C_DIM}·{C_RESET} 한/영 전환"),
        ("chat", chat_left(False, True),
                 f" {C_YELLOW}Esc, L/R-클릭{C_RESET} {C_DIM}·{C_RESET} 채팅창 닫기"),
        (None,   "",
                 f" {C_YELLOW}Ctrl+V{C_RESET} {C_DIM}·{C_RESET} 클립보드 붙여넣기"),
        (None,   "",
                 ""),
        (None,   f"   {C_CYAN}게임(HELLDIVERS™ 2) 창이 활성화된 상태에서만 동작합니다{C_RESET}",
                 f" {C_DIM}* 상태가 꼬이면 Esc/클릭으로 초기화{C_RESET}"),
    ]

    # id → 커서 기준 위로 몇 줄 (배너 출력 후 자동 계산)
    row_offset = {}
    total = len(banner_rows) + 2  # +2: top_border + bottom_border
    for i, (rid, _, _) in enumerate(banner_rows):
        if rid:
            # 커서는 bottom_border 다음 줄 → 위로 (total - 1 - i) 줄
            row_offset[rid] = total - 1 - i

    def update_row(rid, left, right=None):
        """배너 내 특정 행을 런타임에 덮어쓴다."""
        up = row_offset[rid]
        # right가 None이면 해당 행의 원래 right 사용
        if right is None:
            for r_id, _, r_right in banner_rows:
                if r_id == rid:
                    right = r_right
                    break
        line = row(left, right)
        print(f"\033[{up}A\r{line}\033[{up}B\r", end="", flush=True)

    # ── 배너 출력 ──
    title = f" HD2 Hangul Bypass v{VERSION} "
    top_border = f"╭───{C_CYAN}{C_BOLD}{title}{C_RESET}{'─' * (TW - len(title) - 3)}╮"

    print(top_border)
    for _, left_text, right_text in banner_rows:
        print(row(left_text, right_text))
    print(f"╰{'─' * TW}╯")

    # ── 모드 전환 표시 ──

    def set_title(mode_str):
        """콘솔 타이틀에 현재 모드 표시 (작업표시줄에서 확인 가능)."""
        indicator = "🟢 한글 모드" if mode_str == "한글" else "⚪ 영문 모드"
        ctypes.windll.kernel32.SetConsoleTitleW(f"hangul-bypass — {indicator}")

    def log_mode(is_korean, source):
        """모드 전환 시 배너 모드줄 + 타이틀 업데이트."""
        mode = "한글" if is_korean else "영문"
        set_title(mode)
        update_row("mode", mode_left(is_korean))
        log.debug("모드 전환 → %s (%s)", mode, source)

    def log_chat(is_open):
        """채팅 상태 변경 시 배너 채팅줄 업데이트."""
        update_row("chat", chat_left(is_open, chat_mode))
        log.debug("채팅창 → %s", "열림" if is_open else "닫힘")

    def log_chat_mode(is_korean):
        """채팅 모드 변경 시 배너 업데이트 (채팅창 행에 합침)."""
        update_row("chat", chat_left(chat_open, is_korean))
        log.debug("채팅 모드 → %s", "한글" if is_korean else "영문")

    # ── 포커스 체크 (윈도우 타이틀 방식) ──
    ALLOWED_TITLES = {"HELLDIVERS™ 2"}

    def get_foreground_title():
        """포커스된 윈도우의 타이틀 반환."""
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value

    def is_allowed_focus():
        """허용된 윈도우가 포커스인지 확인."""
        return get_foreground_title() in ALLOWED_TITLES

    log_mode(False, "시작")

    # ── 처리 큐 + 처리 스레드 ──
    key_queue = queue.Queue()

    def process_loop():
        """처리 스레드: 큐에서 키를 꺼내 한글 조합 + inject_diff 순차 실행."""
        prev_text = ''
        while True:
            cmd = key_queue.get()
            if cmd == '__clear__':
                prev_text = ''
                continue
            if isinstance(cmd, tuple) and cmd[0] == '__paste__':
                if state.korean_keys or state.fixed:
                    state.clear()
                    inject_diff(prev_text, '')
                _send_unicode(cmd[1], delay=PASTE_DELAY)
                prev_text = ''
                continue
            # cmd = 처리할 키 (문자, 'backspace', 'space')
            state.record(cmd)
            curr_text = state.current()
            inject_diff(prev_text, curr_text)
            prev_text = curr_text
            if not state.korean_keys:
                state.fixed = ''
                prev_text = ''

    process_thread = threading.Thread(target=process_loop, daemon=True)
    process_thread.start()

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
        nonlocal chat_open, chat_mode, ctrl_held, shift_held, alt_held

        key = event.name
        if key is None:
            return True
        is_down = event.event_type == 'down'

        log.debug("event: name=%r type=%s  fg=%r", key, event.event_type, get_foreground_title())

        # ── TOGGLE_KEY (R-Alt, 한/영): HD2 포커스 시 항상 suppress ──
        # OS에 도달하면 Windows IME가 토글되어, 영문 모드에서 후속 키가
        # IME 합성창에 흡수돼 게임이 키를 못 받음 = "키보드 먹통".
        # down/up 페어 모두 차단해야 OS IME 상태 안 바뀜.
        # 다른 앱 포커스에서는 통과시켜 Korean IME 정상 동작 보장.
        if key in TOGGLE_KEY:
            if key == 'right alt':
                alt_held = is_down
            if not is_allowed_focus():
                return True
            if not is_down:
                return False
            if not chat_open:
                if state.mode:
                    state.clear()
                    key_queue.put('__clear__')
                    state.mode = False
                    log_mode(False, "R-Alt(채팅 닫힘)")
                return False
            state.toggle()
            key_queue.put('__clear__')
            log_mode(state.mode, "R-Alt")
            chat_mode = state.mode
            log_chat_mode(chat_mode)
            return False

        # ── 수식키 추적 (포커스 무관하게 항상 추적) ──

        if key in ('ctrl', 'left ctrl', 'right ctrl'):
            ctrl_held = is_down
            return True

        if key in ('alt', 'left alt'):  # right alt는 위 TOGGLE_KEY에서 처리
            alt_held = is_down
            return True

        if key in ('shift', 'left shift', 'right shift'):
            shift_held = is_down
            return True

        # 포커스 체크: 허용된 윈도우가 아니면 나머지 키 통과
        if not is_allowed_focus():
            return True

        # key-up은 처리하지 않음
        if not is_down:
            return True

        # Ctrl+V: 채팅창에서 클립보드 붙여넣기
        if ctrl_held and key == 'v' and chat_open:
            text = get_clipboard_text()
            if text:
                # 줄바꿈 → 공백 (Enter가 채팅 송출을 트리거하므로)
                text = text.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ').strip()
            if text:
                log.info("paste: %d chars", len(text))
                key_queue.put(('__paste__', text))
                return False
            return True

        # Ctrl/Alt 조합은 무조건 통과 (Ctrl+C, Alt+Tab 등)
        if ctrl_held or alt_held:
            return True

        # Enter: 채팅 열기/송출 토글
        if key == 'enter':
            if chat_open:
                # 송출: 영문 전환 + 채팅 닫기
                chat_open = False
                log_chat(False)
                if state.mode:
                    state.clear()
                    key_queue.put('__clear__')
                    state.mode = False
                    log_mode(False, "Enter(송출)")
            else:
                # 열기: chat_mode 복원
                chat_open = True
                log_chat(True)
                if chat_mode:
                    state.mode = True
                    log_mode(True, "Enter(열기)")

            return True

        # Esc: 채팅 닫기 + 영문 전환
        if key in ('escape', 'esc'):
            if chat_open:
                chat_open = False
                log_chat(False)
            if state.mode:
                state.clear()
                key_queue.put('__clear__')
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
            key_queue.put('backspace')
            return False

        # Space: 조합 확정 + 공백 주입
        if key == 'space':
            key_queue.put('space')
            return False

        # 문자 키: 한글 조합 처리
        if len(key) == 1:
            key = key.lower()       # CapsLock 무시: 항상 소문자 기준
            if shift_held:
                key = key.upper()   # Shift만 대문자 기준 (쌍자음 등)
            key_queue.put(key)
            return False

        # 그 외 키 (Tab, F키, 방향키 등): 조합 확정 후 통과
        state.clear()
        key_queue.put('__clear__')
        return True

    # ── 마우스 훅 (채팅창 닫힘 감지) ──
    def on_mouse(event):
        """마우스 클릭 시 채팅창 닫힘 처리."""
        nonlocal chat_open
        if not isinstance(event, mouse.ButtonEvent):
            return
        if event.event_type != 'down':
            return
        if event.button not in ('left', 'right'):
            return
        if not is_allowed_focus():
            return
        if not chat_open:
            return

        chat_open = False
        log_chat(False)
        if state.mode:
            state.clear()
            key_queue.put('__clear__')
            state.mode = False
            log_mode(False, f"마우스({event.button})")

    keyboard.hook(on_key, suppress=True)
    mouse.hook(on_mouse)
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        pass


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
