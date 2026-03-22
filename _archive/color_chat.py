"""
컬러 채팅 — engkor_converter에서 발췌
헬다2 채팅에서 #색상명 텍스트 → <c=FFRRGGBB>텍스트 변환

사용 예:
    #red 안녕  → <c=FFFF0000>안녕
    #blue 하이 → <c=FF0000FF>하이
    일반텍스트 #green 강조 → 일반텍스트 <c=FF008000>강조

TODO: hangul_bypass.py State에 통합할 때
  - State.record()에서 '#' 입력 시 영문 모드 전환
  - space 입력 시 한글 모드 복귀
  - flush() 시 _convert_script() 적용
"""

from PIL import ImageColor


def _tohex(name):
    r, g, b = ImageColor.getrgb(name)
    return f"{r:02X}{g:02X}{b:02X}"


def convert_script(text: str) -> str:
    """#색상명 뒤의 텍스트에 헬다2 컬러 태그를 씌운다."""
    parts = text.split("#")
    if len(parts) == 1:
        return text

    result = []
    if parts[0]:
        result.append(parts[0])

    for part in parts[1:]:
        tokens = part.split(maxsplit=1)
        color_name = tokens[0]
        content = tokens[1] if len(tokens) > 1 else ""
        try:
            hex6 = _tohex(color_name)
            result.append(f"<c=FF{hex6}>{content}")
        except ValueError:
            result.append(part)

    return "".join(result)
