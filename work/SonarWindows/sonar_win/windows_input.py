from __future__ import annotations

import sys

MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_OEM_PLUS = 0xBB
VK_OEM_MINUS = 0xBD
VK_0 = 0x30
VK_CONTROL = 0x11
VK_SPACE = 0x20
VK_D = 0x44
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_WIN = 0x0008

BROWSER_HINTS = ("chrome", "msedge", "firefox", "iexplore", "brave", "opera", "vivaldi")


def _user32():
    if sys.platform != "win32":
        return None
    import ctypes

    return ctypes.windll.user32


def is_windows() -> bool:
    return sys.platform == "win32"


def post_scroll(pixels: int) -> bool:
    if pixels == 0:
        return False
    user32 = _user32()
    if user32 is None:
        return False
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(-pixels), 0)
    return True


def _key(vk: int, down: bool) -> None:
    user32 = _user32()
    if user32 is None:
        return
    flags = 0 if down else KEYEVENTF_KEYUP
    user32.keybd_event(vk, 0, flags, 0)


def send_arrow(next_image: bool) -> bool:
    if _user32() is None:
        return False
    vk = VK_RIGHT if next_image else VK_LEFT
    _key(vk, True)
    _key(vk, False)
    return True


def send_zoom_keys(keys: list[str]) -> bool:
    if _user32() is None:
        return False
    mapping = {"equal": VK_OEM_PLUS, "minus": VK_OEM_MINUS, "0": VK_0}
    for name in keys:
        vk = mapping[name]
        _key(VK_CONTROL, True)
        _key(vk, True)
        _key(vk, False)
        _key(VK_CONTROL, False)
    return True


def foreground_is_browser() -> bool:
    user32 = _user32()
    if user32 is None:
        return False
    import ctypes

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value.lower()
    return any(hint in title for hint in BROWSER_HINTS)


def register_hotkeys(stop_id: int = 1, flip_id: int = 2) -> bool:
    user32 = _user32()
    if user32 is None:
        return False
    mods = MOD_CONTROL | MOD_ALT | MOD_WIN
    ok_stop = user32.RegisterHotKey(None, stop_id, mods, VK_SPACE)
    ok_flip = user32.RegisterHotKey(None, flip_id, mods, VK_D)
    return bool(ok_stop and ok_flip)


def unregister_hotkeys(stop_id: int = 1, flip_id: int = 2) -> None:
    user32 = _user32()
    if user32 is None:
        return
    user32.UnregisterHotKey(None, stop_id)
    user32.UnregisterHotKey(None, flip_id)


def combo_pressed(vks: tuple[int, ...]) -> bool:
    user32 = _user32()
    if user32 is None:
        return False
    return all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)


def stop_shortcut_pressed() -> bool:
    return combo_pressed((VK_CONTROL, VK_MENU, VK_SPACE)) and (
        combo_pressed((VK_LWIN,)) or combo_pressed((VK_RWIN,))
    )


def flip_shortcut_pressed() -> bool:
    return combo_pressed((VK_CONTROL, VK_MENU, VK_D)) and (
        combo_pressed((VK_LWIN,)) or combo_pressed((VK_RWIN,))
    )
