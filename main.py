import sys
import math
import random
import time
import os
import re
import glob
import json
try:
    import winreg
    _HAS_WINREG = True
except ImportError:
    _HAS_WINREG = False

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QPushButton, QHBoxLayout, QVBoxLayout, QFrame,
    QStackedLayout, QDialog, QDoubleSpinBox, QComboBox,
    QStackedWidget, QSizePolicy, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont


SESSION_SECONDS      = 30
PIXELS_PER_INCH      = 2.54
FULL_ROTATION_DEG    = 360

# 기준값: Apex 1.5 at 800 DPI → scale = (DPI × sens × yaw) / BASELINE
BASELINE = 800 * 1.5 * 0.022   # ≈ 26.4

# 세션 기록 저장 파일 (스크립트와 같은 디렉터리)
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_history.json")

# ── 공통 스타일시트 ──────────────────────────────────────────
_SPINBOX_STYLE = """
    QDoubleSpinBox {{
        font-size: {size}px; font-weight: bold; color: #333;
        background: transparent; border: none;
        border-bottom: 2px solid #bbb; padding-bottom: 2px;
    }}
    QDoubleSpinBox:focus {{ border-bottom-color: #555; }}
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 18px; }}
"""

_DARK_BTN_STYLE = """
    QPushButton {{ background:{bg}; color:#fff; border:none;
                  border-radius:6px; font-size:12px; font-weight:bold; }}
    QPushButton:hover {{ background:{hov}; }}
"""


# ── 게임 설정 파일 자동 적용 헬퍼 ──────────────────────────
def _steam_path() -> str:
    """Steam 설치 경로를 레지스트리에서 탐색"""
    if _HAS_WINREG:
        for hive, sub in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Valve\Steam"),
        ]:
            try:
                k = winreg.OpenKey(hive, sub)
                p, _ = winreg.QueryValueEx(k, "InstallPath")
                return p
            except Exception:
                pass
    return r"C:\Program Files (x86)\Steam"


def _all_steam_libraries() -> list[str]:
    """
    Steam의 모든 라이브러리 폴더 목록 반환.
    libraryfolders.vdf 를 파싱해 C: 외의 드라이브에 설치된 게임도 탐색.
    VDF 파싱 실패 시 일반적인 설치 경로를 폴백으로 추가.
    """
    base = _steam_path()
    libraries = [base]

    # VDF 파싱
    vdf_path = os.path.join(base, r"steamapps\libraryfolders.vdf")
    if os.path.exists(vdf_path):
        try:
            with open(vdf_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            # "path"  "D:\\Games\\Steam" 형식의 줄 추출 (단일/이중 역슬래시 모두 허용)
            for match in re.finditer(r'"path"\s+"([^"]+)"', content):
                folder = match.group(1).replace("\\\\", "\\").replace("/", "\\")
                if folder not in libraries and os.path.isdir(folder):
                    libraries.append(folder)
        except Exception:
            pass

    # VDF 파싱이 실패하거나 누락된 경우를 대비해 흔한 Steam 설치 경로를 직접 시도
    _common_roots = [
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"D:\Steam",          r"D:\SteamLibrary",
        r"E:\Steam",          r"E:\SteamLibrary",
        r"F:\Steam",          r"F:\SteamLibrary",
        r"G:\Steam",          r"G:\SteamLibrary",
    ]
    for root in _common_roots:
        if root not in libraries and os.path.isdir(root):
            libraries.append(root)

    return libraries


def _find_in_steam(relative: str) -> str | None:
    """
    모든 Steam 라이브러리에서 relative 경로(steamapps\common\... 형식)를 탐색.
    존재하는 첫 번째 경로 반환, 없으면 None.
    """
    for lib in _all_steam_libraries():
        candidate = os.path.join(lib, relative)
        if os.path.exists(candidate):
            return candidate
    return None


def _find_cs2_config() -> str | None:
    """
    CS2 유저 설정 파일 탐색.
    실제 경로: Steam/userdata/{SteamUID}/730/local/cfg/cs2_user_convars_0_slot0.vcfg
    게임 설치 폴더(steamapps/common/...)가 아니라 userdata에 저장됨.
    """
    for lib in _all_steam_libraries():
        for filename in ["cs2_user_convars_0_slot0.vcfg", "cs2_user_convars_0.vcfg"]:
            pattern = os.path.join(lib, "userdata", "*", "730", "local", "cfg", filename)
            result = _first_glob(pattern)
            if result:
                return result
    return None


def _first_glob(pattern: str) -> str | None:
    matches = glob.glob(pattern)
    return matches[0] if matches else None


# 게임별 설정 파일 정보
#   path      : 고정 경로 (문자열)
#   path_fn    : 동적 경로 탐색 함수 → str | None
#   pattern    : 감도 줄을 찾는 정규식 (단일)
#   patterns   : [(pattern, repl), ...] 목록 — 순서대로 시도, 처음 매칭된 것 사용
#                (CS2처럼 파일 형식이 두 가지일 때 사용)
#   repl       : 치환 템플릿 ({val} 자리에 포맷된 값이 들어감)
#   fmt        : 감도 값 → 문자열 변환 함수
#   extra_pat  : (선택) 추가로 같이 바꿀 패턴 목록 [(pattern, repl), ...]
GAME_CONFIG = {
    "Apex Legends": {
        "path":      os.path.expandvars(
            r"%USERPROFILE%\Saved Games\Respawn\Apex\profile\profile.cfg"),
        "path_hint": r"%USERPROFILE%\Saved Games\Respawn\Apex\profile\profile.cfg",
        # profile.cfg 형식: mouse_sensitivity "1.500000"
        # ※ 이 키는 게임에서 직접 변경한 적 없으면 파일에 없음
        #   → append_tmpl 로 파일 끝에 추가
        "pattern":       r'(mouse_sensitivity\s+)"[^"]*"',
        "repl":          r'\g<1>"{val}"',
        "fmt":           lambda v: f"{v:.6f}",
        "append_tmpl":   'mouse_sensitivity "{val}"\n',
    },
    "CS2": {
        # 실제 경로: Steam/userdata/{UID}/730/local/cfg/cs2_user_convars_0_slot0.vcfg
        # (게임 설치 폴더가 아니라 Steam userdata 폴더에 저장됨)
        "path_fn": _find_cs2_config,
        "path_hint": r"[Steam]\userdata\{UID}\730\local\cfg\cs2_user_convars_0_slot0.vcfg",
        # vcfg 형식: "config" > "convars" > "sensitivity"		"2.500000"
        "patterns": [
            # vcfg (CS2): "sensitivity"<TAB><TAB>"2.500000"
            (r'"sensitivity"(\s+)"[0-9.]+"', r'"sensitivity"\g<1>"{val}"'),
            # cfg (구 CS:GO): sensitivity 2.5 또는 sensitivity "2.5"
            (r'(?<!["\w])(sensitivity)(\s+)"?[0-9.]+"?', r'\g<1>\g<2>"{val}"'),
        ],
        "fmt": lambda v: f"{v:.6f}",
    },
    "Valorant": {
        # UUID 하위 폴더 포함 여러 경로 패턴 시도
        "path_fn": lambda: (
            _first_glob(os.path.expandvars(
                r"%LOCALAPPDATA%\VALORANT\Saved\Config\Windows\*\GameUserSettings.ini"))
            or _first_glob(os.path.expandvars(
                r"%LOCALAPPDATA%\Riot Games\VALORANT\Saved\Config\Windows\*\GameUserSettings.ini"))
            or _first_glob(os.path.expandvars(
                r"%LOCALAPPDATA%\VALORANT\Saved\Config\*\GameUserSettings.ini"))
        ),
        "path_hint": r"%LOCALAPPDATA%\VALORANT\Saved\Config\Windows\{UUID}\GameUserSettings.ini",
        # INI 형식: MouseSensitivity=0.786000
        "pattern":   r'(MouseSensitivity=)[^\r\n]*',
        "repl":      r'\g<1>{val}',
        "fmt":       lambda v: f"{v:.6f}",
    },
    "Fortnite": {
        "path_fn": lambda: _first_glob(os.path.expandvars(
            r"%LOCALAPPDATA%\FortniteGame\Saved\Config\WindowsClient\GameUserSettings.ini")),
        "path_hint": r"%LOCALAPPDATA%\FortniteGame\Saved\Config\WindowsClient\GameUserSettings.ini",
        # INI 독립 줄: MouseSensitivity=0.099000
        # [^\r\n]* 대신 [0-9.]+ 로 숫자만 교체 (뒤에 오는 데이터 보존)
        "pattern":   r'(MouseSensitivity=)([0-9.]+)',
        "repl":      r'\g<1>{val}',
        "fmt":       lambda v: f"{v:.6f}",
        "extra_pat": [(r'(MouseTargetingMultiplier=)([0-9.]+)', r'\g<1>{val}')],
    },
    "PUBG": {
        # PUBG 설정은 Steam 설치 폴더가 아닌 %LOCALAPPDATA% 에 저장됨
        # ※ GameUserSettings.ini 내 MouseSensitivity= 는 독립 줄이 아닌 TslPersistantData
        #   구조체 안에 내장됨. [^\r\n]* 를 쓰면 구조체 전체를 지워 파일 손상 발생!
        #   → 숫자값([0-9.]+)만 정확히 교체
        "path_fn": lambda: _first_glob(os.path.expandvars(
            r"%LOCALAPPDATA%\TslGame\Saved\Config\WindowsNoEditor\GameUserSettings.ini")),
        "path_hint": r"%LOCALAPPDATA%\TslGame\Saved\Config\WindowsNoEditor\GameUserSettings.ini",
        "pattern":   r'(MouseSensitivity=)([0-9.]+)',
        "repl":      r'\g<1>{val}',
        "fmt":       lambda v: f"{v:.6f}",
    },
    "Rainbow Six Siege": {
        "path_fn": lambda: (
            _first_glob(os.path.expandvars(
                r"%USERPROFILE%\Documents\My Games\Rainbow Six - Siege\*\GameSettings.ini"))
            or _first_glob(os.path.expandvars(
                r"%USERPROFILE%\Documents\My Games\Rainbow Six Siege\*\GameSettings.ini"))
        ),
        "path_hint": r"%USERPROFILE%\Documents\My Games\Rainbow Six - Siege\{UUID}\GameSettings.ini",
        # INI 독립 줄: Mouse_H_Sensitivity=10  (정수)
        "pattern":   r'(Mouse_H_Sensitivity=)([0-9.]+)',
        "repl":      r'\g<1>{val}',
        "fmt":       lambda v: f"{int(round(v))}",
        "extra_pat": [(r'(Mouse_V_Sensitivity=)([0-9.]+)', r'\g<1>{val}')],
    },
    "Battlefield 2042": {
        "path_fn": lambda: (
            _first_glob(os.path.expandvars(
                r"%USERPROFILE%\Documents\Battlefield 2042\settings\PROFSAVE_profile"))
            or _first_glob(os.path.expandvars(
                r"%APPDATA%\Battlefield 2042\settings\PROFSAVE_profile"))
        ),
        "path_hint": r"%USERPROFILE%\Documents\Battlefield 2042\settings\PROFSAVE_profile",
        "pattern":   r'(GstInput\.MouseSensitivity\s+)\S+',
        "repl":      r'\g<1>{val}',
        "fmt":       lambda v: f"{v:.6f}",
    },
    "Overwatch 2": {
        # 설치 시점·버전에 따라 Documents 하위 폴더명이 다를 수 있음
        "path_fn": lambda: (
            _first_glob(os.path.expandvars(
                r"%USERPROFILE%\Documents\Overwatch 2\Settings\Settings_v0.ini"))
            or _first_glob(os.path.expandvars(
                r"%USERPROFILE%\Documents\Overwatch\Settings\Settings_v0.ini"))
            or _first_glob(os.path.expandvars(
                r"%PUBLIC%\Documents\Overwatch 2\Settings\Settings_v0.ini"))
        ),
        "path_hint": r"%USERPROFILE%\Documents\Overwatch 2\Settings\Settings_v0.ini",
        "pattern":   r'(MouseSensitivity\s*=\s*")[^"]*"',
        "repl":      r'\g<1>{val}"',
        "fmt":       lambda v: f"{v:.6f}",
    },
    "Call of Duty":  None,   # 버전(MW/Warzone/BO 등)마다 경로·포맷 상이 — 지원 불가
}


# 게임별 yaw (도/카운트) — 마우스 1카운트당 회전 각도
GAME_YAW = {
    "Apex Legends":       0.022,
    "Valorant":           0.07,
    "CS2":                0.022,
    "Overwatch 2":        0.0066,
    "Fortnite":           0.5555,
    "PUBG":               0.001054,
    "Rainbow Six Siege":  0.00573,
    "Call of Duty":       0.0066,
    "Battlefield 2042":   0.001813,
}

# 게임별 감도 설정 (이름, 기본값, 최소, 최대, 단계)
GAME_PRESETS = [
    ("Apex Legends",      2.50,  0.10,  3.00, 0.10),
    ("Valorant",          0.786, 0.001,10.00, 0.001),
    ("CS2",               2.50,  0.10, 10.00, 0.05),
    ("Overwatch 2",       8.33,  1.00,100.00, 0.01),
    ("Fortnite",          0.099, 0.001, 1.00, 0.001),
    ("PUBG",             52.00,  1.00,500.00, 1.00),
    ("Rainbow Six Siege",10.00,  1.00,100.00, 1.00),
    ("Call of Duty",      8.33,  1.00, 20.00, 0.01),
    ("Battlefield 2042", 30.30,  1.00,100.00, 0.10),
]


class _ClickOverlay(QWidget):
    """클릭하면 on_click 콜백을 호출하는 투명 오버레이"""
    def __init__(self, parent=None, on_click=None):
        super().__init__(parent)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if self._on_click:
            self._on_click()


# ────────────────────────────────────────────
#  트래킹 캔버스
# ────────────────────────────────────────────
class TrackingCanvas(QWidget):
    TARGET_R = 16
    SPEED    = 234

    # paintEvent에서 매 프레임 생성 방지를 위한 색상 캐시
    _COL_BG          = QColor("#f5f5f0")
    _COL_IDLE_TXT    = QColor("#999999")
    _COL_HIT_LINE    = QColor("#44bb44")
    _COL_MISS_LINE   = QColor("#ff4444")
    _COL_TARGET_HIT  = QColor(34, 180, 80, 200)
    _COL_TARGET_MISS = QColor(204, 34, 34, 180)
    _COL_BORDER_HIT  = QColor("#22aa44")
    _COL_BORDER_MISS = QColor("#cc2222")
    _COL_CROSSHAIR   = QColor("#111111")
    _COL_WHITE       = QColor("#ffffff")
    _COL_GRAY        = QColor("#888888")
    _COL_MODE_TXT    = QColor("#aaaaaa")
    _COL_PROG_BG     = QColor("#dddddd")
    _COL_CD_RED      = QColor("#ff4444")
    _COL_CD_ORANGE   = QColor("#ffaa22")
    _COL_CD_DARK     = QColor("#555555")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

        self.active        = False
        self._on_start     = None
        self.sensitivity   = 1.50
        self.game_yaw      = 0.022
        self.dpi           = 800
        self.tracking_mode = 1   # 1: 랜덤, 2: X축 왕복

        self.target_pos      = QPointF(0, 0)
        self._virtual_cursor = QPointF(0, 0)
        self._last_raw_pos   = None

        self.remaining = SESSION_SECONDS

        self._total_frames = 0
        self._hit_frames   = 0
        self._errors       = []
        self._errors_sum   = 0.0   # 누적합 (매번 sum() 방지)
        self._prev_err_x   = 0.0
        self._sign_changes = 0

        self._dt = 1 / 60
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_sensitivity(self, val: float, yaw: float = None, dpi: float = None):
        self.sensitivity = val
        if yaw is not None:
            self.game_yaw = yaw
        if dpi is not None:
            self.dpi = dpi

    def _cursor_scale(self) -> float:
        return (self.dpi * self.sensitivity * self.game_yaw) / BASELINE

    def start(self):
        self.active = True
        self._total_frames = 0
        self._hit_frames   = 0
        self._errors       = []
        self._errors_sum   = 0.0
        self._prev_err_x   = 0.0
        self._sign_changes = 0
        self.remaining     = SESSION_SECONDS
        self._last_raw_pos = None

        cx = self.width()  / 2
        cy = self.height() / 2
        self.target_pos      = QPointF(cx, cy)
        self._virtual_cursor = QPointF(cx, cy)

        if self.tracking_mode == 2:
            # X축 왕복: Y는 고정
            self.move_dir = QPointF(random.choice([-1, 1]), 0)
        else:
            self.move_dir = QPointF(
                random.choice([-1, 1]) * (0.5 + random.random() * 0.5),
                random.choice([-1, 1]) * (0.5 + random.random() * 0.5),
            )
        self._normalize_dir()
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._timer.start(int(self._dt * 1000))

    def stop(self):
        self.active = False
        self._timer.stop()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._last_raw_pos = None
        self.update()

    def start_resume(self):
        self.active = True
        self._last_raw_pos = None
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._timer.start(int(self._dt * 1000))

    def _normalize_dir(self):
        l = math.sqrt(self.move_dir.x() ** 2 + self.move_dir.y() ** 2)
        if l > 0:
            self.move_dir = QPointF(self.move_dir.x() / l, self.move_dir.y() / l)

    def _tick(self):
        if not self.active:
            return

        if self.tracking_mode == 2:
            # X축만 이동, Y는 canvas 중앙 고정
            dx = self.move_dir.x() * self.SPEED * self._dt
            dy = 0
            nx = self.target_pos.x() + dx
            ny = self.height() / 2

            pad = self.TARGET_R + 2
            if nx < pad or nx > self.width() - pad:
                self.move_dir = QPointF(-self.move_dir.x(), 0)
            self.target_pos = QPointF(
                max(pad, min(self.width() - pad, nx)),
                ny,
            )
        else:
            dx = self.move_dir.x() * self.SPEED * self._dt
            dy = self.move_dir.y() * self.SPEED * self._dt
            nx = self.target_pos.x() + dx
            ny = self.target_pos.y() + dy

            pad = self.TARGET_R + 2
            bounced = False
            if nx < pad or nx > self.width() - pad:
                self.move_dir = QPointF(-self.move_dir.x(), self.move_dir.y())
                bounced = True
            if ny < pad or ny > self.height() - pad:
                self.move_dir = QPointF(self.move_dir.x(), -self.move_dir.y())
                bounced = True
            if bounced:
                self._normalize_dir()

            self.target_pos = QPointF(
                max(pad, min(self.width()  - pad, nx)),
                max(pad, min(self.height() - pad, ny)),
            )

        err = self.error_distance()
        self._total_frames += 1
        if err <= self.TARGET_R:
            self._hit_frames += 1
        self._errors.append(err)
        self._errors_sum += err

        err_x = self.target_pos.x() - self._virtual_cursor.x()
        if self._prev_err_x != 0 and (err_x * self._prev_err_x < 0):
            self._sign_changes += 1
        self._prev_err_x = err_x

        self.update()

    def mousePressEvent(self, event):
        if not self.active and self._on_start:
            self._on_start()

    def mouseMoveEvent(self, event):
        raw = event.position()

        if self.active and self._last_raw_pos is None:
            self._virtual_cursor = QPointF(
                max(0, min(self.width(),  raw.x())),
                max(0, min(self.height(), raw.y())),
            )
        elif self.active and self._last_raw_pos is not None:
            delta_x = raw.x() - self._last_raw_pos.x()
            delta_y = raw.y() - self._last_raw_pos.y()
            scale   = self._cursor_scale()

            vx = self._virtual_cursor.x() + delta_x * scale
            vy = self._virtual_cursor.y() + delta_y * scale

            vx = max(0, min(self.width(),  vx))
            vy = max(0, min(self.height(), vy))
            self._virtual_cursor = QPointF(vx, vy)
        elif not self.active:
            self._virtual_cursor = raw

        self._last_raw_pos = raw
        self.update()

    def enterEvent(self, event):
        if self.active:
            self.setCursor(Qt.CursorShape.BlankCursor)

    def leaveEvent(self, event):
        if not self.active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self._last_raw_pos = None

    def error_distance(self) -> float:
        dx = self.target_pos.x() - self._virtual_cursor.x()
        dy = self.target_pos.y() - self._virtual_cursor.y()
        return math.sqrt(dx * dx + dy * dy)

    def session_result(self) -> dict:
        total    = self._total_frames or 1
        hit_rate = self._hit_frames / total * 100
        avg_err  = self._errors_sum / len(self._errors) if self._errors else 0.0
        max_err  = max(self._errors) if self._errors else 0.0
        osc_rate = self._sign_changes / total * 100

        if osc_rate > 12 and avg_err > 12:
            verdict = "빠름"
        elif avg_err > 22 and osc_rate < 20:
            verdict = "느림"
        else:
            verdict = "적정"

        return dict(hit_rate=hit_rate, verdict=verdict,
                    avg_err=avg_err, max_err=max_err, osc_rate=osc_rate)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._COL_BG)

        if not self.active:
            painter.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
            painter.setPen(self._COL_IDLE_TXT)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "클릭하여 시작")
            return

        cx = int(self._virtual_cursor.x())
        cy = int(self._virtual_cursor.y())

        err = self.error_distance()
        on_target  = err <= self.TARGET_R
        line_color = self._COL_HIT_LINE if on_target else self._COL_MISS_LINE
        painter.setPen(QPen(line_color, 1, Qt.PenStyle.DotLine))
        painter.drawLine(cx, cy,
                         int(self.target_pos.x()), int(self.target_pos.y()))

        r        = self.TARGET_R
        t_color  = self._COL_TARGET_HIT  if on_target else self._COL_TARGET_MISS
        t_border = self._COL_BORDER_HIT  if on_target else self._COL_BORDER_MISS
        painter.setPen(QPen(t_border, 2))
        painter.setBrush(QBrush(t_color))
        painter.drawEllipse(
            QRectF(self.target_pos.x() - r, self.target_pos.y() - r, r * 2, r * 2)
        )
        painter.setPen(QPen(self._COL_WHITE, 2))
        tx, ty = int(self.target_pos.x()), int(self.target_pos.y())
        painter.drawLine(tx - 7, ty, tx + 7, ty)
        painter.drawLine(tx, ty - 7, tx, ty + 7)

        painter.setPen(QPen(self._COL_CROSSHAIR, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))
        painter.drawLine(cx - 6, cy, cx - 4, cy)
        painter.drawLine(cx + 4, cy, cx + 6, cy)
        painter.drawLine(cx, cy - 6, cx, cy - 4)
        painter.drawLine(cx, cy + 4, cx, cy + 6)

        secs = max(0, self.remaining)
        cd_color = (self._COL_CD_RED    if secs <= 5  else
                    self._COL_CD_ORANGE if secs <= 10 else
                    self._COL_CD_DARK)
        painter.setPen(cd_color)
        painter.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
        painter.drawText(
            QRectF(self.width() - 100, 12, 88, 48),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{secs}s"
        )

        bar_h    = 4
        progress = 1.0 - (secs / SESSION_SECONDS)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._COL_PROG_BG)
        painter.drawRect(0, self.height() - bar_h, self.width(), bar_h)
        painter.setBrush(cd_color)
        painter.drawRect(0, self.height() - bar_h, int(self.width() * progress), bar_h)

        painter.setPen(self._COL_GRAY)
        painter.setFont(QFont("Segoe UI", 10))
        cm360 = FULL_ROTATION_DEG / (self.dpi * self.sensitivity * self.game_yaw) * PIXELS_PER_INCH
        painter.drawText(10, self.height() - 10,
                         f"감도 {self.sensitivity:.2f}  |  "
                         f"DPI {int(self.dpi)}  |  "
                         f"cm/360°  {cm360:.1f}")

        # mode 표시
        mode_txt = "모드: X축 왕복" if self.tracking_mode == 2 else "모드: 랜덤"
        painter.setPen(self._COL_MODE_TXT)
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(10, 20, mode_txt)


# ────────────────────────────────────────────
#  그리드샷 캔버스
# ────────────────────────────────────────────
class GridshotCanvas(QWidget):
    TARGET_R  = 24
    GRID_COLS = 5
    GRID_ROWS = 4

    # paintEvent 색상 캐시
    _COL_BG          = QColor("#f5f5f0")
    _COL_IDLE_TXT    = QColor("#999999")
    _COL_TARGET_FILL = QColor(204, 34, 34, 180)
    _COL_TARGET_BDR  = QColor("#cc2222")
    _COL_WHITE       = QColor("#ffffff")
    _COL_CROSSHAIR   = QColor("#111111")
    _COL_PROG_BG     = QColor("#dddddd")
    _COL_CD_RED      = QColor("#ff4444")
    _COL_CD_ORANGE   = QColor("#ffaa22")
    _COL_CD_DARK     = QColor("#555555")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

        self.active        = False
        self._on_start     = None
        self.on_result     = None  # 세션 종료 시 결과 콜백
        self.sensitivity   = 1.50
        self.game_yaw      = 0.022
        self.dpi           = 800

        self._virtual_cursor = QPointF(0, 0)
        self._last_raw_pos   = None

        self.remaining     = SESSION_SECONDS
        self._active_idx   = -1   # 현재 활성 원 인덱스
        self._hit_count    = 0
        self._miss_count   = 0
        self._react_times  = []
        self._last_hit_time = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._grid_positions = []  # 격자 중심 위치들 (비율 기준)

    def set_sensitivity(self, val: float, yaw: float = None, dpi: float = None):
        self.sensitivity = val
        if yaw is not None:
            self.game_yaw = yaw
        if dpi is not None:
            self.dpi = dpi

    def _cursor_scale(self) -> float:
        return (self.dpi * self.sensitivity * self.game_yaw) / BASELINE

    def _build_grid(self):
        """캔버스 크기 기반으로 격자 위치 계산"""
        self._grid_positions = []
        w = self.width()
        h = self.height()
        pad_x = w * 0.10
        pad_y = h * 0.15
        cell_w = (w - pad_x * 2) / self.GRID_COLS
        cell_h = (h - pad_y * 2) / self.GRID_ROWS
        for r in range(self.GRID_ROWS):
            for c in range(self.GRID_COLS):
                cx = pad_x + cell_w * c + cell_w / 2
                cy = pad_y + cell_h * r + cell_h / 2
                self._grid_positions.append(QPointF(cx, cy))

    def _pick_next(self):
        """현재와 다른 랜덤 인덱스 선택"""
        n = len(self._grid_positions)
        if n == 0:
            return
        candidates = [i for i in range(n) if i != self._active_idx]
        self._active_idx = random.choice(candidates)
        self._last_hit_time = time.perf_counter()

    def start(self):
        self._build_grid()
        self.active        = True
        self._hit_count    = 0
        self._miss_count   = 0
        self._react_times  = []
        self.remaining     = SESSION_SECONDS
        self._last_raw_pos = None
        self._active_idx   = -1

        cx = self.width()  / 2
        cy = self.height() / 2
        self._virtual_cursor = QPointF(cx, cy)

        self._pick_next()
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._timer.start(1000)
        self.update()

    def stop(self):
        self.active = False
        self._timer.stop()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._last_raw_pos = None
        self.update()

    def start_resume(self):
        """일시정지 후 재개 — 타이머도 함께 재시작"""
        self.active = True
        self._last_raw_pos = None
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._timer.start(1000)   # 카운트다운 타이머 재개 (pause 시 stop() 했으므로 반드시 재시작)
        self.update()

    def session_result(self) -> dict:
        avg_react = (sum(self._react_times) / len(self._react_times) * 1000
                     if self._react_times else 0)
        return dict(
            hits=self._hit_count,
            misses=self._miss_count,
            avg_react_ms=avg_react,
        )

    def _tick(self):
        """1초마다 카운트다운"""
        if not self.active:
            return
        self.remaining -= 1
        if self.remaining <= 0:
            self.remaining = 0
            self.stop()
            if self.on_result:
                self.on_result(self.session_result())
        self.update()

    def mousePressEvent(self, event):
        if not self.active:
            if self._on_start:
                self._on_start()
            return

        # 가상 커서 위치로 히트 판정
        vx = self._virtual_cursor.x()
        vy = self._virtual_cursor.y()

        if self._active_idx >= 0 and self._active_idx < len(self._grid_positions):
            target = self._grid_positions[self._active_idx]
            dx = vx - target.x()
            dy = vy - target.y()
            dist = math.sqrt(dx * dx + dy * dy)

            if dist <= self.TARGET_R:
                # 히트
                react = time.perf_counter() - self._last_hit_time
                self._react_times.append(react)
                self._hit_count += 1
                self._pick_next()
            else:
                # 미스: 다른 원들도 확인
                hit_other = False
                for i, pos in enumerate(self._grid_positions):
                    if i == self._active_idx:
                        continue
                    dx2 = vx - pos.x()
                    dy2 = vy - pos.y()
                    if math.sqrt(dx2 * dx2 + dy2 * dy2) <= self.TARGET_R:
                        hit_other = True
                        break
                if not hit_other:
                    self._miss_count += 1
        self.update()

    def mouseMoveEvent(self, event):
        raw = event.position()

        if self.active and self._last_raw_pos is None:
            self._virtual_cursor = QPointF(
                max(0, min(self.width(),  raw.x())),
                max(0, min(self.height(), raw.y())),
            )
        elif self.active and self._last_raw_pos is not None:
            delta_x = raw.x() - self._last_raw_pos.x()
            delta_y = raw.y() - self._last_raw_pos.y()
            scale   = self._cursor_scale()

            vx = self._virtual_cursor.x() + delta_x * scale
            vy = self._virtual_cursor.y() + delta_y * scale
            vx = max(0, min(self.width(),  vx))
            vy = max(0, min(self.height(), vy))
            self._virtual_cursor = QPointF(vx, vy)
        elif not self.active:
            self._virtual_cursor = raw

        self._last_raw_pos = raw
        self.update()

    def enterEvent(self, event):
        if self.active:
            self.setCursor(Qt.CursorShape.BlankCursor)

    def leaveEvent(self, event):
        if not self.active:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self._last_raw_pos = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 트래킹과 동일한 밝은 배경
        painter.fillRect(self.rect(), self._COL_BG)

        if not self.active:
            painter.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
            painter.setPen(self._COL_IDLE_TXT)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "클릭하여 시작")
            return

        # 활성 타겟만 표시 (비활성 원 없음)
        if self._active_idx >= 0 and self._active_idx < len(self._grid_positions):
            pos = self._grid_positions[self._active_idx]
            r   = self.TARGET_R
            painter.setPen(QPen(self._COL_TARGET_BDR, 2))
            painter.setBrush(QBrush(self._COL_TARGET_FILL))
            painter.drawEllipse(QRectF(pos.x() - r, pos.y() - r, r * 2, r * 2))
            # 중심 십자
            painter.setPen(QPen(self._COL_WHITE, 2))
            tx, ty = int(pos.x()), int(pos.y())
            painter.drawLine(tx - 7, ty, tx + 7, ty)
            painter.drawLine(tx, ty - 7, tx, ty + 7)

        # 가상 커서 (트래킹과 동일한 크로스헤어)
        cx = int(self._virtual_cursor.x())
        cy = int(self._virtual_cursor.y())
        painter.setPen(QPen(self._COL_CROSSHAIR, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))
        painter.drawLine(cx - 6, cy, cx - 4, cy)
        painter.drawLine(cx + 4, cy, cx + 6, cy)
        painter.drawLine(cx, cy - 6, cx, cy - 4)
        painter.drawLine(cx, cy + 4, cx, cy + 6)

        # 카운트다운 (우상단) — 트래킹과 동일 색상 기준
        secs = max(0, self.remaining)
        cd_color = (self._COL_CD_RED    if secs <= 5  else
                    self._COL_CD_ORANGE if secs <= 10 else
                    self._COL_CD_DARK)
        painter.setPen(cd_color)
        painter.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
        painter.drawText(
            QRectF(self.width() - 110, 12, 98, 48),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{secs}s"
        )

        # 진행 바 (하단)
        bar_h    = 4
        progress = 1.0 - (secs / SESSION_SECONDS)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._COL_PROG_BG)
        painter.drawRect(0, self.height() - bar_h, self.width(), bar_h)
        painter.setBrush(cd_color)
        painter.drawRect(0, self.height() - bar_h, int(self.width() * progress), bar_h)


# ────────────────────────────────────────────
#  감도 계산기 위젯
# ────────────────────────────────────────────
class SensCalcWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:#f0ede8;")
        # 메인 윈도우가 연결할 콜백: on_apply(game_name, sens, dpi)
        self.on_apply = None

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(24)

        # ── 좌측: 입력 패널 ──────────────────────
        left_panel = QFrame()
        left_panel.setFixedWidth(300)
        left_panel.setStyleSheet(
            "background:#e8e4de; border-radius:8px;"
        )
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(20, 20, 20, 20)
        left_lay.setSpacing(16)

        title_l = QLabel("입력")
        title_l.setStyleSheet("font-size:12px; color:#888; letter-spacing:2px;")
        left_lay.addWidget(title_l)

        # 게임 선택
        game_lbl = QLabel("게임 선택")
        game_lbl.setStyleSheet("font-size:10px; color:#888;")
        left_lay.addWidget(game_lbl)

        self._sc_game_combo = QComboBox()
        for name, *_ in GAME_PRESETS:
            self._sc_game_combo.addItem(name)
        self._sc_game_combo.setStyleSheet("""
            QComboBox {
                font-size:15px; font-weight:bold; color:#333;
                background:transparent; border:none;
                border-bottom:2px solid #bbb; padding-bottom:2px;
            }
            QComboBox:focus { border-bottom-color:#555; }
            QComboBox::drop-down { border:none; width:20px; }
            QComboBox QAbstractItemView {
                background:#f0ede8; border:1px solid #ccc;
                color:#222; selection-background-color:#dedad4;
                selection-color:#111; font-size:14px;
            }
        """)
        self._sc_game_combo.currentIndexChanged.connect(self._on_sc_game_changed)
        left_lay.addWidget(self._sc_game_combo)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("color:#d0ccc6;")
        left_lay.addWidget(sep1)

        # 감도 입력
        sens_lbl = QLabel("감도")
        sens_lbl.setStyleSheet("font-size:10px; color:#888;")
        left_lay.addWidget(sens_lbl)

        # 초기 게임(Apex Legends) 기본값으로 설정
        _init_name, _init_def, _init_lo, _init_hi, _init_step = GAME_PRESETS[0]
        self._sc_sens_spin = QDoubleSpinBox()
        self._sc_sens_spin.setRange(_init_lo, _init_hi)
        self._sc_sens_spin.setSingleStep(_init_step)
        self._sc_sens_spin.setValue(_init_def)
        self._sc_sens_spin.setDecimals(3 if _init_step < 0.01 else 2 if _init_step < 1 else 0)
        self._sc_sens_spin.setStyleSheet(_SPINBOX_STYLE.format(size=20))
        self._sc_sens_spin.valueChanged.connect(self._recalc)
        left_lay.addWidget(self._sc_sens_spin)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color:#d0ccc6;")
        left_lay.addWidget(sep2)

        # DPI 입력
        dpi_lbl = QLabel("DPI")
        dpi_lbl.setStyleSheet("font-size:10px; color:#888;")
        left_lay.addWidget(dpi_lbl)

        self._sc_dpi_spin = QDoubleSpinBox()
        self._sc_dpi_spin.setRange(100, 32000)
        self._sc_dpi_spin.setSingleStep(100)
        self._sc_dpi_spin.setValue(800)
        self._sc_dpi_spin.setDecimals(0)
        self._sc_dpi_spin.setStyleSheet(_SPINBOX_STYLE.format(size=20))
        self._sc_dpi_spin.valueChanged.connect(self._recalc)
        left_lay.addWidget(self._sc_dpi_spin)

        left_lay.addStretch()
        root.addWidget(left_panel)

        # ── 우측: 결과 패널 ──────────────────────
        right_panel = QFrame()
        right_panel.setStyleSheet("background:#e8e4de; border-radius:8px;")
        right_lay = QVBoxLayout(right_panel)
        right_lay.setContentsMargins(24, 20, 24, 20)
        right_lay.setSpacing(12)

        self._cm360_lbl = QLabel()  # 숨김용 더미

        # 다른 게임 등가 감도 표
        equiv_lbl = QLabel("다른 게임 감도")
        equiv_lbl.setStyleSheet("font-size:14px; color:#888; letter-spacing:1px;")
        right_lay.addWidget(equiv_lbl)

        self._equiv_table = QTableWidget(len(GAME_PRESETS), 2)
        self._equiv_table.setHorizontalHeaderLabels(["다른 게임 감도", "감도"])
        self._equiv_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._equiv_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._equiv_table.horizontalHeader().resizeSection(1, 130)
        self._equiv_table.verticalHeader().setVisible(False)
        self._equiv_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._equiv_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._equiv_table.setShowGrid(False)
        self._equiv_table.setStyleSheet("""
            QTableWidget {
                background:transparent; border:none; font-size:16px; color:#333;
            }
            QHeaderView::section {
                background:#dedad4; color:#555; font-size:13px;
                border:none; padding:6px;
            }
            QTableWidget::item { padding:6px 10px; }
        """)
        bold_font = QFont(); bold_font.setBold(True); bold_font.setPointSize(12)
        for r, (name, *_) in enumerate(GAME_PRESETS):
            item_name = QTableWidgetItem(name)
            item_name.setFont(bold_font)
            item_val  = QTableWidgetItem("—")
            item_val.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._equiv_table.setItem(r, 0, item_name)
            self._equiv_table.setItem(r, 1, item_val)

        right_lay.addWidget(self._equiv_table, stretch=1)

        # 적용 버튼 — 현재 선택 행의 게임+감도를 메인 스핀박스에 반영
        apply_hint = QLabel("표에서 행을 클릭하면 해당 게임 감도를 메인에 적용합니다.")
        apply_hint.setStyleSheet("font-size:10px; color:#999;")
        apply_hint.setWordWrap(True)
        right_lay.addWidget(apply_hint)

        self._apply_btn = QPushButton("▶  선택한 게임 감도를 메인에 적용")
        self._apply_btn.setFixedHeight(40)
        self._apply_btn.setStyleSheet("""
            QPushButton {
                background:#2a4a2a; color:#7dff9a;
                border:1px solid #3a7a3a; border-radius:6px;
                font-size:13px; font-weight:bold;
            }
            QPushButton:hover  { background:#3a6a3a; }
            QPushButton:pressed { background:#1a3a1a; }
        """)
        self._apply_btn.clicked.connect(self._do_apply)
        right_lay.addWidget(self._apply_btn)

        root.addWidget(right_panel, stretch=1)

        # 초기 계산
        self._recalc()

        # 테이블 행 클릭 시 해당 게임 행 하이라이트
        self._equiv_table.cellClicked.connect(self._on_table_row_clicked)
        self._selected_row = 0  # 기본 선택: 첫 번째 행

    def sync_dpi(self, dpi: float):
        """메인 DPI 스핀박스 값을 계산기에 동기화 (감도 계산기 탭 진입 시 호출)"""
        self._sc_dpi_spin.blockSignals(True)
        self._sc_dpi_spin.setValue(dpi)
        self._sc_dpi_spin.blockSignals(False)
        self._recalc()

    def _on_sc_game_changed(self, idx: int):
        """게임 변경 시 해당 게임의 기본 감도를 자동 입력"""
        _, default, lo, hi, step = GAME_PRESETS[idx]
        self._sc_sens_spin.blockSignals(True)
        self._sc_sens_spin.setRange(lo, hi)
        self._sc_sens_spin.setSingleStep(step)
        self._sc_sens_spin.setDecimals(3 if step < 0.01 else 2 if step < 1 else 0)
        self._sc_sens_spin.setValue(default)
        self._sc_sens_spin.blockSignals(False)
        self._recalc()

    def _recalc(self):
        idx  = self._sc_game_combo.currentIndex()
        name = GAME_PRESETS[idx][0]
        sens = self._sc_sens_spin.value()
        dpi  = self._sc_dpi_spin.value()
        yaw  = GAME_YAW.get(name, 0.022)

        # cm/360 계산
        if sens > 0 and dpi > 0 and yaw > 0:
            cm360 = FULL_ROTATION_DEG / (dpi * sens * yaw) * PIXELS_PER_INCH
        else:
            cm360 = 0

        self._cm360_lbl.setText(f"{cm360:.2f}")

        # 등가 감도 표 갱신
        for r, (g_name, *_) in enumerate(GAME_PRESETS):
            g_yaw = GAME_YAW.get(g_name, 0.022)
            if g_yaw > 0 and dpi > 0:
                equiv_sens = FULL_ROTATION_DEG / (dpi * g_yaw * cm360 / PIXELS_PER_INCH)
                self._equiv_table.item(r, 1).setText(f"{equiv_sens:.4f}")
            else:
                self._equiv_table.item(r, 1).setText("—")

        # 선택 행 버튼 텍스트 갱신
        self._refresh_apply_btn()

    def _on_table_row_clicked(self, row: int, _col: int):
        self._selected_row = row
        self._refresh_apply_btn()

    def _refresh_apply_btn(self):
        if not hasattr(self, "_apply_btn"):
            return
        row  = getattr(self, "_selected_row", 0)
        name = GAME_PRESETS[row][0]
        val  = self._equiv_table.item(row, 1).text() if self._equiv_table.item(row, 1) else "—"
        self._apply_btn.setText(f"▶  {name}  {val}  → 메인에 적용")

    def _do_apply(self):
        if not self.on_apply:
            return
        row  = getattr(self, "_selected_row", 0)
        name = GAME_PRESETS[row][0]
        val_txt = self._equiv_table.item(row, 1).text() if self._equiv_table.item(row, 1) else ""
        try:
            sens = float(val_txt)
        except ValueError:
            return
        dpi = self._sc_dpi_spin.value()
        self.on_apply(name, sens, dpi)


# ────────────────────────────────────────────
#  오차 그래프 위젯
# ────────────────────────────────────────────
class ErrorGraphWidget(QWidget):
    """최근 5회 세션의 평균 오차를 꺾은선 그래프로 표시"""

    _VERDICT_COLOR = {"빠름": "#ff4444", "느림": "#4488ff", "적정": "#44cc77"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[tuple[float, str]] = []  # (avg_err, verdict)
        self.setFixedHeight(130)
        self.setMinimumWidth(80)

    def push(self, avg_err: float, verdict: str):
        self._data.append((avg_err, verdict))
        if len(self._data) > 5:
            self._data.pop(0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = self.width(), self.height()
        painter.fillRect(self.rect(), QColor("#e8e4de"))

        # 제목
        title_font = QFont("Arial", 8)
        painter.setFont(title_font)
        painter.setPen(QColor("#333333"))
        painter.drawText(8, 0, W - 16, 18,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "명중률")

        if not self._data:
            painter.setPen(QColor("#aaaaaa"))
            painter.drawText(0, 18, W, H - 18,
                             Qt.AlignmentFlag.AlignCenter, "기록 없음")
            return

        PAD_L, PAD_R, PAD_T, PAD_B = 32, 10, 22, 22
        plot_x0 = PAD_L
        plot_y0 = PAD_T
        plot_w  = W - PAD_L - PAD_R
        plot_h  = H - PAD_T - PAD_B

        values = [d[0] for d in self._data]
        max_v  = 100.0   # 명중률은 0~100%
        n      = len(values)

        # 격자선 (수평 3줄)
        for i in range(4):
            gy = plot_y0 + plot_h * i / 3
            painter.setPen(QPen(QColor("#ccc8c2"), 1))
            painter.drawLine(int(plot_x0), int(gy), int(plot_x0 + plot_w), int(gy))

        # Y 축 레이블
        small_font = QFont("Arial", 7)
        painter.setFont(small_font)
        painter.setPen(QColor("#333333"))
        painter.drawText(0, plot_y0 - 6, PAD_L - 4, 12,
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         "100%")
        painter.drawText(0, plot_y0 + plot_h - 6, PAD_L - 4, 12,
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         "0%")

        def to_pt(idx, val):
            if n == 1:
                x = plot_x0 + plot_w / 2
            else:
                x = plot_x0 + plot_w * idx / (n - 1)
            y = plot_y0 + plot_h * (1.0 - val / max_v)
            return int(x), int(y)

        pts = [to_pt(i, v) for i, v in enumerate(values)]

        # 선
        painter.setPen(QPen(QColor("#2266cc"), 1, Qt.PenStyle.SolidLine))
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])

        # 점 + 값 레이블
        for i, (x, y) in enumerate(pts):
            verdict = self._data[i][1]
            dot_color = QColor(self._VERDICT_COLOR.get(verdict, "#4488ff"))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(dot_color))
            painter.drawEllipse(x - 4, y - 4, 8, 8)

            # 값
            painter.setPen(QColor("#111111"))
            painter.setFont(small_font)
            label = f"{values[i]:.0f}%"
            lx = x - 14 if i == n - 1 else x - 10
            painter.drawText(lx, y - 14, 28, 12,
                             Qt.AlignmentFlag.AlignCenter, label)

        # X 축 레이블 (1회~5회)
        painter.setFont(small_font)
        painter.setPen(QColor("#333333"))
        offset = 5 - n  # 5회 미만이면 번호를 맞춤
        for i, (x, _) in enumerate(pts):
            painter.drawText(x - 10, H - PAD_B + 4, 20, 14,
                             Qt.AlignmentFlag.AlignCenter,
                             f"{offset + i + 1}회")


# ────────────────────────────────────────────
#  설정 다이얼로그 (게임 경로 관리)
# ────────────────────────────────────────────
class GamePathSettingsDialog(QDialog):
    """모든 게임의 설정 파일 경로를 한 곳에서 관리하는 다이얼로그"""

    _BTN_STYLE = """
        QPushButton {{ background:{bg}; color:{fg};
                      border:1px solid {bd}; border-radius:5px;
                      font-size:13px; padding:0 4px; }}
        QPushButton:hover {{ background:{hov}; }}
        QPushButton:disabled {{ color:#444; background:#1a1a1a; border-color:#2a2a2a; }}
    """

    def __init__(self, parent, custom_paths: dict):
        super().__init__(parent)
        self._custom_paths = custom_paths   # MainWindow의 dict 참조
        self._row_ui: dict[str, dict] = {}

        self.setWindowTitle("게임 설정 파일 경로 관리")
        self.setFixedSize(740, 600)
        self.setStyleSheet("background:#1e1e1e; color:#fff;")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(0)

        # 타이틀
        title = QLabel("게임 설정 파일 경로 관리")
        title.setStyleSheet("font-size:15px; font-weight:bold; color:#ddd; letter-spacing:1px;")
        root.addWidget(title)
        sub = QLabel("경로를 직접 지정하거나, 자동으로 다시 탐색할 수 있습니다.")
        sub.setStyleSheet("font-size:10px; color:#666; margin-bottom:4px;")
        root.addWidget(sub)
        root.addSpacing(14)

        # 헤더
        hdr = QFrame()
        hdr.setStyleSheet("background:#2a2a2a; border-radius:4px;")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(16, 6, 16, 6)
        for text, width in [("게임", 170), ("상태", 30), ("경로", 0), ("액션", 112)]:
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size:10px; color:#888; letter-spacing:1px;")
            if width:
                lbl.setFixedWidth(width)
            else:
                lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            hdr_lay.addWidget(lbl)
        root.addWidget(hdr)
        root.addSpacing(4)

        # 게임 행 목록
        rows_frame = QFrame()
        rows_frame.setStyleSheet("background:#151515; border-radius:6px;")
        rows_lay = QVBoxLayout(rows_frame)
        rows_lay.setContentsMargins(0, 4, 0, 4)
        rows_lay.setSpacing(0)

        for idx, (game_name, cfg) in enumerate(GAME_CONFIG.items()):
            rows_lay.addWidget(self._make_row(game_name, cfg, idx))

        root.addWidget(rows_frame, stretch=1)
        root.addSpacing(16)

        # 하단 버튼
        foot = QHBoxLayout()
        foot.addStretch()
        all_detect_btn = QPushButton("🔍  전체 자동 탐색")
        all_detect_btn.setFixedHeight(34)
        all_detect_btn.setStyleSheet("""
            QPushButton { background:#223344; color:#88aaff;
                          border:1px solid #445566; border-radius:6px;
                          font-size:12px; font-weight:bold; padding:0 14px; }
            QPushButton:hover { background:#334466; }
        """)
        all_detect_btn.clicked.connect(self._detect_all)
        foot.addWidget(all_detect_btn)
        foot.addSpacing(8)

        close_btn = QPushButton("닫기")
        close_btn.setFixedHeight(34)
        close_btn.setFixedWidth(90)
        close_btn.setStyleSheet("""
            QPushButton { background:#cc2222; color:#fff; border:none;
                          border-radius:6px; font-size:13px; font-weight:bold; }
            QPushButton:hover { background:#ee4444; }
        """)
        close_btn.clicked.connect(self.accept)
        foot.addWidget(close_btn)
        root.addLayout(foot)

    def _make_row(self, game_name: str, cfg, idx: int) -> QWidget:
        bg = "#1e1e1e" if idx % 2 == 0 else "#222222"
        row = QFrame()
        row.setStyleSheet(f"background:{bg};")
        row.setFixedHeight(58)

        lay = QHBoxLayout(row)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)

        # 게임 이름
        name_lbl = QLabel(game_name)
        name_lbl.setFixedWidth(170)
        name_lbl.setStyleSheet("font-size:12px; font-weight:bold; color:#ccc;")
        lay.addWidget(name_lbl)

        # 상태 아이콘
        status_lbl = QLabel("—")
        status_lbl.setFixedWidth(30)
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_lbl.setStyleSheet("font-size:14px; color:#555;")
        lay.addWidget(status_lbl)

        # 경로 텍스트
        path_lbl = QLabel("—")
        path_lbl.setStyleSheet("font-size:9px; color:#666;")
        path_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(path_lbl, stretch=1)

        # 액션 버튼 3개
        detect_btn = QPushButton("🔍")
        detect_btn.setFixedSize(32, 28)
        detect_btn.setToolTip("자동 경로 탐색")
        detect_btn.setStyleSheet(self._BTN_STYLE.format(
            bg="#223344", fg="#88aaff", bd="#445566", hov="#334466"))

        pick_btn = QPushButton("📂")
        pick_btn.setFixedSize(32, 28)
        pick_btn.setToolTip("파일 직접 선택")
        pick_btn.setStyleSheet(self._BTN_STYLE.format(
            bg="#2a2a3a", fg="#aaaaff", bd="#444466", hov="#3a3a5a"))

        clear_btn = QPushButton("↺")
        clear_btn.setFixedSize(32, 28)
        clear_btn.setToolTip("직접 지정 경로 초기화")
        clear_btn.setEnabled(False)
        clear_btn.setStyleSheet(self._BTN_STYLE.format(
            bg="#1a1a1a", fg="#555", bd="#2a2a2a", hov="#3a2a1a"))

        if cfg is None:
            for b in (detect_btn, pick_btn, clear_btn):
                b.setEnabled(False)
        else:
            detect_btn.clicked.connect(lambda _, g=game_name, c=cfg: self._auto_detect(g, c))
            pick_btn.clicked.connect(  lambda _, g=game_name, c=cfg: self._pick_file(g, c))
            clear_btn.clicked.connect( lambda _, g=game_name, c=cfg: self._clear_path(g, c))

        btn_box = QHBoxLayout()
        btn_box.setSpacing(4)
        btn_box.setContentsMargins(0, 0, 0, 0)
        btn_box.addWidget(detect_btn)
        btn_box.addWidget(pick_btn)
        btn_box.addWidget(clear_btn)

        lay.addLayout(btn_box)

        self._row_ui[game_name] = {
            "status_lbl": status_lbl,
            "path_lbl":   path_lbl,
            "clear_btn":  clear_btn,
        }
        self._refresh_row(game_name, cfg)
        return row

    # ── 경로 헬퍼 ────────────────────────────
    def _auto_path(self, cfg) -> tuple[str | None, str | None]:
        """
        자동 탐색 실행 → (존재하는 경로 or None, 시도한/힌트 경로 or None).
        파일이 없을 때도 '어디를 찾았는지' 알 수 있도록 tried_path를 반환.
        path_fn이 None을 반환하면 cfg["path_hint"]를 tried_path로 사용.
        """
        if cfg is None:
            return None, None
        try:
            if "path" in cfg:
                tried = cfg["path"]
                return (tried if os.path.exists(tried) else None), tried
            if "path_fn" in cfg:
                found = cfg["path_fn"]()
                # path_fn이 경로를 찾은 경우 → 파일 존재 여부 확인 후 반환
                if found:
                    return (found if os.path.exists(found) else None), found
                # path_fn이 None 반환 → 게임 미설치 or 경로 불일치
                # path_hint가 있으면 "이 경로를 찾았지만 없었다"는 힌트 제공
                hint = cfg.get("path_hint")
                return None, hint  # hint가 없으면 None (기존 동작과 동일)
        except Exception:
            pass
        return None, None

    def _resolved_path(self, game_name: str, cfg) -> str | None:
        """커스텀 경로 우선, 없으면 자동 탐색 (존재하는 경로만 반환)"""
        if game_name in self._custom_paths:
            return self._custom_paths[game_name]
        found, _ = self._auto_path(cfg)
        return found

    def _refresh_row(self, game_name: str, cfg):
        ui = self._row_ui.get(game_name)
        if not ui:
            return

        if cfg is None:
            ui["status_lbl"].setText("—")
            ui["status_lbl"].setStyleSheet("font-size:14px; color:#444;")
            ui["path_lbl"].setText("자동 적용 미지원")
            ui["path_lbl"].setStyleSheet("font-size:9px; color:#444;")
            ui["clear_btn"].setEnabled(False)
            return

        is_custom = game_name in self._custom_paths

        if is_custom:
            path   = self._custom_paths[game_name]
            exists = os.path.exists(path)
            tried  = path
        else:
            path, tried = self._auto_path(cfg)
            exists = path is not None

        def _trim(p): return p if len(p) <= 65 else "…" + p[-62:]

        if exists:
            ui["status_lbl"].setText("✓")
            ui["status_lbl"].setStyleSheet("font-size:14px; font-weight:bold; color:#44cc77;")
            tag = "  [직접 지정]" if is_custom else ""
            ui["path_lbl"].setText(_trim(path) + tag)
            ui["path_lbl"].setStyleSheet(
                "font-size:9px; color:#5dcc77;" if is_custom else "font-size:9px; color:#888;")
        else:
            ui["status_lbl"].setText("✗")
            ui["status_lbl"].setStyleSheet("font-size:14px; font-weight:bold; color:#ff6644;")
            if tried:
                if is_custom or (not is_custom and "path" in (cfg or {})):
                    # 고정 경로 또는 직접 지정 경로 → 파일 없음
                    ui["path_lbl"].setText(f"파일 없음: {_trim(tried)}")
                else:
                    # path_fn이 None 반환 + hint만 있는 경우 → 게임 미설치 or 경로 불일치
                    ui["path_lbl"].setText(f"게임 미설치 또는 경로 불일치\n탐색 위치: {_trim(tried)}")
            else:
                ui["path_lbl"].setText("경로 탐색 실패 — 📂로 직접 지정하세요")
            ui["path_lbl"].setStyleSheet("font-size:9px; color:#ff8855;")

        ui["clear_btn"].setEnabled(is_custom)
        ui["clear_btn"].setStyleSheet(self._BTN_STYLE.format(
            bg="#2a1a0a", fg="#ffaa66", bd="#664422", hov="#3a2a1a"
        ) if is_custom else self._BTN_STYLE.format(
            bg="#1a1a1a", fg="#555", bd="#2a2a2a", hov="#1a1a1a"
        ))

    # ── 액션 ─────────────────────────────────
    def _auto_detect(self, game_name: str, cfg):
        """🔍: 커스텀 경로를 지우고 자동 탐색 재실행"""
        self._custom_paths.pop(game_name, None)
        # "탐색 중…" 표시
        ui = self._row_ui.get(game_name)
        if ui:
            ui["status_lbl"].setText("⟳")
            ui["status_lbl"].setStyleSheet("font-size:14px; color:#888;")
            ui["path_lbl"].setText("탐색 중…")
            ui["path_lbl"].setStyleSheet("font-size:9px; color:#888;")
        QApplication.processEvents()   # UI 즉시 갱신
        self._refresh_row(game_name, cfg)

    def _pick_file(self, game_name: str, cfg):
        """📂: 파일 탐색기로 직접 지정"""
        found, tried = self._auto_path(cfg)
        current = self._custom_paths.get(game_name) or found or tried
        start   = os.path.dirname(current) if current else os.path.expanduser("~")
        chosen, _ = QFileDialog.getOpenFileName(
            self, f"{game_name} 설정 파일 선택", start,
            "설정 파일 (*.cfg *.vcfg *.ini *.txt *.profile PROFSAVE_profile);;모든 파일 (*.*)",
        )
        if chosen:
            self._custom_paths[game_name] = chosen
            self._refresh_row(game_name, cfg)

    def _clear_path(self, game_name: str, cfg):
        """↺: 직접 지정 경로 초기화 → 자동 탐색으로 복귀"""
        self._custom_paths.pop(game_name, None)
        self._refresh_row(game_name, cfg)

    def _detect_all(self):
        """🔍 전체: 모든 게임 커스텀 경로 초기화 후 자동 탐색 재실행"""
        # 1단계: 모든 커스텀 경로 초기화 + 전체 행에 "탐색 중…" 표시
        for game_name, cfg in GAME_CONFIG.items():
            self._custom_paths.pop(game_name, None)
            if cfg is not None:
                ui = self._row_ui.get(game_name)
                if ui:
                    ui["status_lbl"].setText("⟳")
                    ui["status_lbl"].setStyleSheet("font-size:14px; color:#888;")
                    ui["path_lbl"].setText("탐색 중…")
                    ui["path_lbl"].setStyleSheet("font-size:9px; color:#888;")
        QApplication.processEvents()   # 화면에 즉시 반영

        # 2단계: 게임마다 탐색 실행 후 즉시 UI 갱신
        for game_name, cfg in GAME_CONFIG.items():
            self._refresh_row(game_name, cfg)
            QApplication.processEvents()


# ────────────────────────────────────────────
#  메인 윈도우
# ────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("에임 트레이너")
        self.resize(1200, 720)
        self.setStyleSheet("background-color: #f0ede8;")

        self._running   = False
        self._paused    = False
        self._countdown = SESSION_SECONDS

        # 그리드샷 상태
        self._gs_running  = False
        self._gs_paused   = False

        # 감도 변경 로그
        self._sens_log = []   # [(verdict, before, after), ...]

        # 게임 설정 파일 백업 {game_name: (path, original_content)}
        self._config_backups: dict[str, tuple[str, str]] = {}
        # 유저가 직접 지정한 설정 파일 경로 {game_name: path}
        self._custom_config_paths: dict[str, str] = {}

        self._current_mode = "트래킹 1"

        self._build_ui()

        self._info_timer = QTimer(self)
        self._info_timer.timeout.connect(self._update_info)
        self._info_timer.start(50)

        self._cd_timer = QTimer(self)
        self._cd_timer.timeout.connect(self._tick_countdown)

    # ── UI 구성 ──────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._make_topbar())
        root.addWidget(self._make_info_bar())

        # QStackedWidget으로 body 구성
        self._body_stack = QStackedWidget()

        # 페이지 0,1: 트래킹 (공유 레이아웃)
        tracking_page = QWidget()
        tracking_page.setStyleSheet("background:#f0ede8;")
        tp_lay = QHBoxLayout(tracking_page)
        tp_lay.setContentsMargins(12, 8, 12, 8)
        tp_lay.setSpacing(10)
        tp_lay.addWidget(self._make_canvas_area(), stretch=3)
        tp_lay.addWidget(self._make_right_panel(), stretch=1)
        self._body_stack.addWidget(tracking_page)   # index 0 (트래킹1)

        # 페이지 1: 트래킹2 (같은 위젯 재사용 — 별도 페이지 없이 mode만 변경)
        # 실제로는 index 0 페이지에서 canvas.tracking_mode를 변경

        # 페이지 1: 그리드샷
        gridshot_page = QWidget()
        gridshot_page.setStyleSheet("background:#f0ede8;")
        gs_lay = QHBoxLayout(gridshot_page)
        gs_lay.setContentsMargins(12, 8, 12, 8)
        gs_lay.setSpacing(10)

        # 그리드샷 캔버스 + 일시정지 오버레이를 QStackedLayout으로 묶음
        gs_canvas_frame = QFrame()
        gs_canvas_frame.setStyleSheet("background:#dedad4; border-radius:6px;")
        gs_stack = QStackedLayout(gs_canvas_frame)
        gs_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        gs_stack.setContentsMargins(0, 0, 0, 0)

        self._gs_canvas = GridshotCanvas()
        self._gs_canvas._on_start = self._gs_start
        self._gs_canvas.on_result = self._gs_on_result
        # 초기 감도 동기화
        self._gs_canvas.set_sensitivity(2.50, yaw=0.022, dpi=800)
        gs_stack.addWidget(self._gs_canvas)

        # 그리드샷 일시정지 오버레이 (트래킹과 동일 스타일)
        self._gs_pause_overlay = QWidget(gs_canvas_frame)
        self._gs_pause_overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._gs_pause_overlay.hide()
        gp_lay = QVBoxLayout(self._gs_pause_overlay)
        gp_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gp_lay.setSpacing(14)

        gp_title = QLabel("일시정지")
        gp_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gp_title.setStyleSheet(
            "font-size:22px; font-weight:bold; color:#ffffff; letter-spacing:4px;"
        )
        gp_lay.addWidget(gp_title)
        gp_lay.addSpacing(8)

        gs_resume_btn = QPushButton("▶  계속하기")
        gs_resume_btn.setFixedSize(200, 52)
        gs_resume_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gs_resume_btn.setStyleSheet("""
            QPushButton {
                background: rgba(30,30,30,220); color:#fff;
                border: 2px solid #555; border-radius:26px;
                font-size:15px; font-weight:bold;
            }
            QPushButton:hover  { background:rgba(60,60,60,235); border-color:#999; }
            QPushButton:pressed { background:rgba(0,0,0,255); }
        """)
        gs_resume_btn.clicked.connect(self._gs_resume_session)
        gp_lay.addWidget(gs_resume_btn)

        gs_exit_btn = QPushButton("✕  프로그램 종료")
        gs_exit_btn.setFixedSize(200, 52)
        gs_exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gs_exit_btn.setStyleSheet("""
            QPushButton {
                background: rgba(160,20,20,210); color:#fff;
                border: 2px solid #992222; border-radius:26px;
                font-size:15px; font-weight:bold;
            }
            QPushButton:hover  { background:rgba(200,40,40,230); border-color:#cc4444; }
            QPushButton:pressed { background:rgba(100,0,0,255); }
        """)
        gs_exit_btn.clicked.connect(QApplication.quit)
        gp_lay.addWidget(gs_exit_btn)

        gs_stack.addWidget(self._gs_pause_overlay)

        gs_lay.addWidget(gs_canvas_frame, stretch=3)
        gs_lay.addWidget(self._make_gs_right_panel(), stretch=1)
        self._body_stack.addWidget(gridshot_page)   # index 1

        # 페이지 2: 감도 계산기
        self._sens_calc = SensCalcWidget()
        self._sens_calc.on_apply = self._apply_from_calc
        self._body_stack.addWidget(self._sens_calc)  # index 2


        root.addWidget(self._body_stack, stretch=1)
        root.addWidget(self._make_bottom_bar())

        # 시작 시 스핀박스 범위·감도·트래킹 모드를 첫 번째 게임에 맞게 초기화
        self._on_game_changed(0)
        self.canvas.tracking_mode = 2   # "트래킹 1" 버튼 선택 상태와 일치 (X축 왕복)
        # 이전 세션 기록 복원
        self._load_history()

    def _make_topbar(self):
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet("background:#e8e4de; border-bottom:1px solid #d0ccc6;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)

        title = QLabel("에임 트레이너")
        title.setStyleSheet("font-size:15px; font-weight:bold; color:#222;")
        lay.addWidget(title)
        lay.addStretch()

        self._mode_btns = {}
        mode_bar = QFrame()
        mode_bar.setStyleSheet("background:transparent;")
        mode_lay = QHBoxLayout(mode_bar)
        mode_lay.setContentsMargins(0, 0, 0, 0)
        mode_lay.setSpacing(4)

        modes = ["트래킹 1", "트래킹 2", "그리드샷", "감도 계산기"]
        for i, name in enumerate(modes):
            btn = QPushButton(name)
            btn.setFixedHeight(28)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda checked, n=name: self._on_mode_changed(n))
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #666;
                    border: 1px solid transparent;
                    border-radius: 4px;
                    font-size: 12px;
                    padding: 0 12px;
                }
                QPushButton:checked {
                    background: #333;
                    color: #fff;
                    border-color: #333;
                }
                QPushButton:hover:!checked {
                    background: #dedad4;
                    border-color: #ccc;
                }
            """)
            mode_lay.addWidget(btn)
            self._mode_btns[name] = btn

        lay.addWidget(mode_bar)
        lay.addSpacing(8)

        # ⚙ 설정 버튼 (게임 경로 관리)
        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(28, 28)
        settings_btn.setToolTip("게임 설정 파일 경로 관리")
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #888;
                border: 1px solid transparent; border-radius: 4px;
                font-size: 15px;
            }
            QPushButton:hover { background: #dedad4; color: #333; border-color: #ccc; }
        """)
        settings_btn.clicked.connect(self._open_path_settings)
        lay.addWidget(settings_btn)
        lay.addSpacing(4)
        return bar

    def _on_mode_changed(self, selected: str):
        # 버튼 상태 동기화
        for name, btn in self._mode_btns.items():
            btn.setChecked(name == selected)

        # 현재 세션 중지
        self._stop_all_sessions()

        self._current_mode = selected

        if selected == "트래킹 1":
            self.canvas.tracking_mode = 2
            self._body_stack.setCurrentIndex(0)
            self._info_bar.show()
        elif selected == "트래킹 2":
            self.canvas.tracking_mode = 1
            self._body_stack.setCurrentIndex(0)
            self._info_bar.show()
        elif selected == "그리드샷":
            self._body_stack.setCurrentIndex(1)
            self._info_bar.show()
        elif selected == "감도 계산기":
            self._body_stack.setCurrentIndex(2)
            self._info_bar.hide()
            # 메인 DPI를 계산기에 동기화
            self._sens_calc.sync_dpi(self._dpi_spinbox.value())

    def _stop_all_sessions(self):
        """모든 세션 중지"""
        if self._running or self._paused:
            self._stop_session(show_result=False)
        if self._gs_running or self._gs_paused:
            self._gs_canvas.stop()
            self._gs_running = False
            self._gs_paused  = False
            self._gs_pause_overlay.hide()

    def _make_info_bar(self):
        self._info_bar = QFrame()
        self._info_bar.setFixedHeight(80)
        self._info_bar.setStyleSheet("background:#eae6e0; border-bottom:1px solid #d0ccc6;")
        lay = QHBoxLayout(self._info_bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(0)

        # 게임 선택
        col0 = QVBoxLayout()
        col0.setContentsMargins(24, 6, 24, 6)
        col0.setSpacing(2)

        lbl0 = QLabel("게임 선택")
        lbl0.setStyleSheet("font-size:10px; color:#888;")

        self._game_combo = QComboBox()
        for name, *_ in GAME_PRESETS:
            self._game_combo.addItem(name)
        self._game_combo.setFixedWidth(180)
        self._game_combo.setStyleSheet("""
            QComboBox {
                font-size: 16px; font-weight: bold; color: #333;
                background: transparent; border: none;
                border-bottom: 2px solid #bbb; padding-bottom: 2px;
            }
            QComboBox:focus { border-bottom-color: #555; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background: #f0ede8; border: 1px solid #ccc;
                color: #222; selection-background-color: #dedad4;
                selection-color: #111; font-size: 16px;
            }
        """)
        self._game_combo.currentIndexChanged.connect(self._on_game_changed)

        col0.addWidget(lbl0)
        col0.addWidget(self._game_combo)
        lay.addLayout(col0)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.Shape.VLine)
        sep0.setFixedHeight(62)
        sep0.setStyleSheet("color:#c0bcb6;")
        lay.addWidget(sep0, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 감도 스핀박스
        col1 = QVBoxLayout()
        col1.setContentsMargins(24, 6, 24, 6)
        col1.setSpacing(2)

        self._sens_label = QLabel("감도")
        self._sens_label.setStyleSheet("font-size:10px; color:#888;")

        self._spinbox = QDoubleSpinBox()
        self._spinbox.setRange(0.10, 10.00)
        self._spinbox.setSingleStep(0.10)
        self._spinbox.setValue(2.50)
        self._spinbox.setDecimals(2)
        self._spinbox.setFixedWidth(120)
        self._spinbox.setStyleSheet(_SPINBOX_STYLE.format(size=16))
        self._spinbox.valueChanged.connect(self._on_sensitivity_changed)

        col1.addWidget(self._sens_label)
        col1.addWidget(self._spinbox)
        lay.addLayout(col1)

        self._edpi_lbl = None

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedHeight(40)
        sep.setStyleSheet("color:#c0bcb6;")
        lay.addWidget(sep, alignment=Qt.AlignmentFlag.AlignVCenter)

        col2 = QVBoxLayout()
        col2.setContentsMargins(24, 6, 24, 6)
        col2.setSpacing(2)

        lbl2 = QLabel("DPI")
        lbl2.setStyleSheet("font-size:10px; color:#888;")

        self._dpi_spinbox = QDoubleSpinBox()
        self._dpi_spinbox.setRange(100, 32000)
        self._dpi_spinbox.setSingleStep(100)
        self._dpi_spinbox.setValue(800)
        self._dpi_spinbox.setDecimals(0)
        self._dpi_spinbox.setFixedWidth(120)
        self._dpi_spinbox.setStyleSheet(_SPINBOX_STYLE.format(size=16))
        self._dpi_spinbox.valueChanged.connect(self._on_dpi_changed)

        col2.addWidget(lbl2)
        col2.addWidget(self._dpi_spinbox)
        lay.addLayout(col2)

        lay.addStretch()
        return self._info_bar

    # ── 게임 설정 파일 적용 ──────────────────────────
    def _apply_sens_to_config(self, sens: float) -> tuple[bool, str]:
        """현재 선택된 게임의 설정 파일에 감도를 직접 기록. (success, message)"""
        game_name = GAME_PRESETS[self._game_combo.currentIndex()][0]
        cfg = GAME_CONFIG.get(game_name)

        if cfg is None:
            return False, f"'{game_name}'은 자동 적용을 지원하지 않습니다."

        # ── 경로 탐색: 유저 지정 경로 → 자동 탐색 순으로 시도 ──
        path = self._custom_config_paths.get(game_name)
        if not path:
            try:
                path = cfg.get("path") or (cfg["path_fn"]() if "path_fn" in cfg else None)
            except Exception as e:
                return False, f"경로 탐색 중 오류: {e}"

        if not path:
            hint = cfg.get("path_hint", "경로 미확인")
            return False, (
                f"설정 파일 경로를 찾지 못했습니다.\n"
                f"게임이 설치되어 있는지 확인하세요.\n"
                f"예상 위치: {hint}"
            )
        if not os.path.exists(path):
            return False, (
                f"설정 파일이 존재하지 않습니다.\n"
                f"게임을 한 번 이상 실행해 설정 파일을 생성하세요.\n"
                f"경로: {path}"
            )

        # ── 파일 읽기 ──
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return False, f"파일 읽기 실패:\n{path}\n오류: {e}"

        # 최초 적용 시에만 원본 백업
        if game_name not in self._config_backups:
            self._config_backups[game_name] = (path, content)

        val_str = cfg["fmt"](sens)

        def do_replace(text, pattern, repl_tmpl):
            repl = repl_tmpl.replace("{val}", val_str)
            new_text, n = re.subn(pattern, repl, text)
            return new_text, n

        # ── 패턴 매칭 ──
        # cfg에 "patterns" 리스트가 있으면 순서대로 시도 (CS2 vcfg/cfg 양식 대응)
        # 없으면 단일 "pattern"/"repl" 사용
        pattern_list = cfg.get("patterns")
        if pattern_list:
            new_content, count = content, 0
            matched_pat = None
            for pat, rep in pattern_list:
                new_content, count = do_replace(content, pat, rep)
                if count > 0:
                    matched_pat = pat
                    break
        else:
            new_content, count = do_replace(content, cfg["pattern"], cfg["repl"])
            matched_pat = cfg.get("pattern")

        if count == 0:
            append_tmpl = cfg.get("append_tmpl")
            if append_tmpl:
                # 키가 없는 경우 파일 끝에 추가 (Apex Legends 등)
                append_line = append_tmpl.replace("{val}", val_str)
                new_content = content + ("" if content.endswith("\n") else "\n") + append_line
                count = 1
            else:
                # 파일 앞 200자를 메시지에 포함해 디버깅에 활용
                preview = content[:200].replace("\n", "↵").replace("\t", "→")
                return False, (
                    f"설정 파일에서 감도 항목을 찾지 못했습니다.\n"
                    f"게임을 한 번 이상 실행한 뒤 다시 시도하세요.\n\n"
                    f"파일 미리보기:\n{preview}"
                )

        # 추가 패턴 (R6S 수직 감도 등)
        for pat, rep in cfg.get("extra_pat", []):
            new_content, _ = do_replace(new_content, pat, rep)

        # ── 파일 쓰기 ──
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except PermissionError:
            return False, (
                f"파일 쓰기 권한 없음:\n{path}\n"
                f"게임 실행 중이면 종료 후 다시 시도하세요."
            )
        except Exception as e:
            return False, f"파일 쓰기 실패:\n{path}\n오류: {e}"

        # ── 쓰기 후 검증: 실제로 값이 바뀌었는지 재확인 ──
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                verify = f.read()
            if val_str not in verify:
                return False, (
                    f"파일 쓰기는 성공했지만 값 확인 실패.\n"
                    f"({val_str}이 파일에서 발견되지 않음)\n"
                    f"원본이 복구됩니다."
                )
        except Exception:
            pass  # 검증 실패는 무시 (쓰기 자체는 성공)

        return True, f"감도 {val_str} 적용 완료\n({os.path.basename(path)})"

    def _restore_config(self) -> tuple[bool, str]:
        """현재 게임의 설정 파일을 적용 전 원본으로 복구"""
        game_name = GAME_PRESETS[self._game_combo.currentIndex()][0]

        if game_name not in self._config_backups:
            return False, "복구할 백업이 없습니다.\n아직 이번 세션에서 적용한 적이 없습니다."

        path, original = self._config_backups[game_name]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)
            # 복구 후 백업 삭제 (다음 적용 시 새로 백업받기 위해)
            del self._config_backups[game_name]
            return True, f"원본 설정으로 복구 완료\n({os.path.basename(path)})"
        except Exception as e:
            return False, f"복구 실패: {e}"

    def _on_game_changed(self, index: int):
        name, default, lo, hi, step = GAME_PRESETS[index]
        yaw = GAME_YAW.get(name, 0.022)
        self._sens_label.setText(f"{name} 감도")
        self._spinbox.blockSignals(True)
        self._spinbox.setRange(lo, hi)
        self._spinbox.setSingleStep(step)
        self._spinbox.setDecimals(3 if step < 0.01 else 2 if step < 1 else 0)
        self._spinbox.setValue(default)
        self._spinbox.blockSignals(False)
        self.canvas.set_sensitivity(default, yaw=yaw, dpi=self._dpi_spinbox.value())
        self._gs_canvas.set_sensitivity(default, yaw=yaw, dpi=self._dpi_spinbox.value())

    def _on_sensitivity_changed(self, val: float):
        name = GAME_PRESETS[self._game_combo.currentIndex()][0]
        yaw  = GAME_YAW.get(name, 0.022)
        self.canvas.set_sensitivity(val, yaw=yaw, dpi=self._dpi_spinbox.value())
        self._gs_canvas.set_sensitivity(val, yaw=yaw, dpi=self._dpi_spinbox.value())

    def _on_dpi_changed(self, val: float):
        name = GAME_PRESETS[self._game_combo.currentIndex()][0]
        yaw  = GAME_YAW.get(name, 0.022)
        self.canvas.set_sensitivity(self._spinbox.value(), yaw=yaw, dpi=val)
        self._gs_canvas.set_sensitivity(self._spinbox.value(), yaw=yaw, dpi=val)
        # 감도 계산기 탭이 열려 있으면 DPI 실시간 동기화
        if self._current_mode == "감도 계산기":
            self._sens_calc.sync_dpi(val)

    def _make_canvas_area(self):
        frame = QFrame()
        frame.setStyleSheet("background:#dedad4; border-radius:6px;")

        stack = QStackedLayout(frame)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.setContentsMargins(0, 0, 0, 0)

        self.canvas = TrackingCanvas()
        self.canvas._on_start = self._toggle
        self.canvas.set_sensitivity(2.50, yaw=0.022, dpi=800)
        stack.addWidget(self.canvas)

        self._overlay = _ClickOverlay(frame, on_click=self._toggle)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        ov_lay = QVBoxLayout(self._overlay)
        ov_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._center_btn = None
        stack.addWidget(self._overlay)

        # 일시정지 오버레이
        self._pause_overlay = QWidget(frame)
        self._pause_overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._pause_overlay.hide()
        p_lay = QVBoxLayout(self._pause_overlay)
        p_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p_lay.setSpacing(14)

        pause_title = QLabel("일시정지")
        pause_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pause_title.setStyleSheet(
            "font-size:22px; font-weight:bold; color:#ffffff; letter-spacing:4px;"
        )
        p_lay.addWidget(pause_title)
        p_lay.addSpacing(8)

        resume_btn = QPushButton("▶  계속하기")
        resume_btn.setFixedSize(200, 52)
        resume_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        resume_btn.setStyleSheet("""
            QPushButton {
                background: rgba(30,30,30,220); color:#fff;
                border: 2px solid #555; border-radius:26px;
                font-size:15px; font-weight:bold;
            }
            QPushButton:hover  { background:rgba(60,60,60,235); border-color:#999; }
            QPushButton:pressed { background:rgba(0,0,0,255); }
        """)
        resume_btn.clicked.connect(self._resume_session)
        p_lay.addWidget(resume_btn)

        exit_btn = QPushButton("✕  프로그램 종료")
        exit_btn.setFixedSize(200, 52)
        exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_btn.setStyleSheet("""
            QPushButton {
                background: rgba(160,20,20,210); color:#fff;
                border: 2px solid #992222; border-radius:26px;
                font-size:15px; font-weight:bold;
            }
            QPushButton:hover  { background:rgba(200,40,40,230); border-color:#cc4444; }
            QPushButton:pressed { background:rgba(100,0,0,255); }
        """)
        exit_btn.clicked.connect(QApplication.quit)
        p_lay.addWidget(exit_btn)

        stack.addWidget(self._pause_overlay)
        return frame

    def _make_right_panel(self):
        panel = QFrame()
        panel.setFixedWidth(260)
        panel.setStyleSheet("background:#e8e4de; border-radius:6px;")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        sec_lbl = QLabel("측정 데이터")
        sec_lbl.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(sec_lbl)

        self.error_lbl = QLabel("—")
        self.error_lbl.setStyleSheet("font-size:36px; font-weight:bold; color:#333;")
        lay.addWidget(self.error_lbl)

        desc = QLabel("추적 세션 동안 실시간으로 픽셀 오차가 기록됩니다.")
        desc.setStyleSheet("font-size:10px; color:#999;")
        desc.setWordWrap(True)
        lay.addWidget(desc)
        lay.addSpacing(8)

        perf_lbl = QLabel("성과 지표")
        perf_lbl.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(perf_lbl)

        self._metrics = {}
        for key, default in [
            ("명중률",    "—"),
            ("평균 오차", "—"),
            ("감도 판정", "—"),
            ("남은 시간", f"{SESSION_SECONDS}s"),
        ]:
            row = QHBoxLayout()
            k_lbl = QLabel(key)
            k_lbl.setStyleSheet("font-size:11px; color:#666;")
            v_lbl = QLabel(default)
            v_lbl.setStyleSheet("font-size:11px; font-weight:bold; color:#333;")
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(k_lbl)
            row.addStretch()
            row.addWidget(v_lbl)
            lay.addLayout(row)
            self._metrics[key] = v_lbl

            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color:#d0ccc6;")
            lay.addWidget(sep)

        lay.addStretch()

        log_title = QLabel("감도 변경 로그")
        log_title.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(log_title)

        self._sens_log_lbl = QLabel("아직 변경 없음")
        self._sens_log_lbl.setStyleSheet("font-size:10px; color:#999; line-height:150%;")
        self._sens_log_lbl.setWordWrap(True)
        lay.addWidget(self._sens_log_lbl)

        lay.addSpacing(10)

        self._err_graph = ErrorGraphWidget()
        lay.addWidget(self._err_graph)

        return panel

    def _make_gs_right_panel(self):
        """그리드샷 전용 우측 패널 — 트래킹과 동일 디자인"""
        panel = QFrame()
        panel.setFixedWidth(260)
        panel.setStyleSheet("background:#e8e4de; border-radius:6px;")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        # 측정 데이터 (대형 수치)
        sec_lbl = QLabel("측정 데이터")
        sec_lbl.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(sec_lbl)

        self._gs_hit_lbl = QLabel("—")
        self._gs_hit_lbl.setStyleSheet("font-size:36px; font-weight:bold; color:#333;")
        lay.addWidget(self._gs_hit_lbl)

        desc = QLabel("30초 동안 활성 타겟을 클릭하세요.")
        desc.setStyleSheet("font-size:10px; color:#999;")
        desc.setWordWrap(True)
        lay.addWidget(desc)
        lay.addSpacing(8)

        # 성과 지표
        perf_lbl = QLabel("성과 지표")
        perf_lbl.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(perf_lbl)

        self._gs_metrics = {}
        for key, default in [
            ("히트",         "—"),
            ("미스",         "—"),
            ("평균 반응시간", "—"),
            ("남은 시간",    f"{SESSION_SECONDS}s"),
        ]:
            row_lay = QHBoxLayout()
            k_lbl = QLabel(key)
            k_lbl.setStyleSheet("font-size:11px; color:#666;")
            v_lbl = QLabel(default)
            v_lbl.setStyleSheet("font-size:11px; font-weight:bold; color:#333;")
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row_lay.addWidget(k_lbl)
            row_lay.addStretch()
            row_lay.addWidget(v_lbl)
            lay.addLayout(row_lay)
            self._gs_metrics[key] = v_lbl

            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color:#d0ccc6;")
            lay.addWidget(sep)

        lay.addStretch()

        note_title = QLabel("기술 노트")
        note_title.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(note_title)
        note = QLabel(
            "정확한 측정을 위해 Windows의\n"
            "\"포인터 정밀도 향상\" 기능이\n"
            "비활성화되어 있는지 확인하세요."
        )
        note.setStyleSheet("font-size:10px; color:#999;")
        note.setWordWrap(True)
        lay.addWidget(note)
        return panel

    def _make_bottom_bar(self):
        bar = QFrame()
        bar.setFixedHeight(52)
        bar.setStyleSheet("background:#e8e4de; border-top:1px solid #d0ccc6;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)

        session_lbl = QLabel("세션 01 / 04")
        session_lbl.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(session_lbl)
        lay.addStretch()

        reset_btn = QPushButton("세션 재설정")
        reset_btn.setFixedSize(100, 34)
        reset_btn.setStyleSheet("""
            QPushButton { background:transparent; color:#555;
                          border:1px solid #bbb; border-radius:4px; font-size:12px; }
            QPushButton:hover { background:#ddd; }
        """)
        reset_btn.clicked.connect(self._reset)
        lay.addWidget(reset_btn)
        lay.addStretch()

        ts_lbl = QLabel("클릭하여 시작  |  ESC 일시정지  |  30초 측정")
        ts_lbl.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(ts_lbl)
        return bar

    # ── 키 입력 ──────────────────────────────
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            mode = self._current_mode
            if mode in ("트래킹 1", "트래킹 2"):
                if self._running:
                    self._pause_session()
                elif self._paused:
                    self._resume_session()
            elif mode == "그리드샷":
                if self._gs_running:
                    self._gs_pause_session()
                elif self._gs_paused:
                    self._gs_resume_session()

    # ── 트래킹 세션 동작 ─────────────────────
    def _toggle(self):
        if not self._running and not self._paused:
            self._paused    = False
            self._running   = True
            self._countdown = SESSION_SECONDS
            self.canvas.start()
            self._overlay.hide()
            self._cd_timer.start(1000)

    def _pause_session(self):
        self._paused  = True
        self._running = False
        self._cd_timer.stop()
        self.canvas.stop()
        self._pause_overlay.show()
        self._pause_overlay.raise_()

    def _resume_session(self):
        self._paused  = False
        self._running = True
        self._pause_overlay.hide()
        self.canvas.start_resume()
        self._cd_timer.start(1000)

    def _tick_countdown(self):
        self._countdown -= 1
        self.canvas.remaining = self._countdown
        self._metrics["남은 시간"].setText(f"{self._countdown}s")
        if self._countdown <= 0:
            self._stop_session(show_result=True)

    def _stop_session(self, show_result: bool):
        self._running = False
        self._paused  = False
        self._cd_timer.stop()
        self.canvas.stop()
        self._pause_overlay.hide()
        self._overlay.show()
        self._metrics["남은 시간"].setText(f"{SESSION_SECONDS}s")

        if show_result and self.canvas._total_frames > 0:
            r = self.canvas.session_result()
            self._err_graph.push(r["hit_rate"], r["verdict"])
            self._save_session(r)
            self._show_combined_result_dialog(r)

    def _reset(self):
        mode = self._current_mode
        if mode in ("트래킹 1", "트래킹 2"):
            self._stop_session(show_result=False)
            self.error_lbl.setText("—")
            for k, v in self._metrics.items():
                v.setText(f"{SESSION_SECONDS}s" if k == "남은 시간" else "—")
        elif mode == "그리드샷":
            if self._gs_running or self._gs_paused:
                self._gs_canvas.stop()
                self._gs_running = False
                self._gs_paused  = False
                self._gs_pause_overlay.hide()
            for k, v in self._gs_metrics.items():
                v.setText(f"{SESSION_SECONDS}s" if k == "남은 시간" else "—")
            self._gs_canvas.update()

    def _update_info(self):
        """50ms마다 패널 정보 갱신"""
        mode = self._current_mode
        if mode in ("트래킹 1", "트래킹 2"):
            if not self._running:
                return
            err = self.canvas.error_distance()
            self.error_lbl.setText(f"{err:.1f}")

            total   = self.canvas._total_frames or 1
            hit_pct = self.canvas._hit_frames / total * 100
            self._metrics["명중률"].setText(f"{hit_pct:.1f} %")
            self._metrics["평균 오차"].setText(
                f"{self.canvas._errors_sum/len(self.canvas._errors):.1f} px"
                if self.canvas._errors else "—"
            )
            osc     = self.canvas._sign_changes / total * 100
            avg_err = self.canvas._errors_sum / len(self.canvas._errors) if self.canvas._errors else 0
            if osc > 12 and avg_err > 12:
                verdict, color = "빠름", "#cc2222"
            elif avg_err > 22 and osc < 20:
                verdict, color = "느림",  "#2266cc"
            else:
                verdict, color = "적정",  "#22aa44"
            self._metrics["감도 판정"].setText(verdict)
            self._metrics["감도 판정"].setStyleSheet(
                f"font-size:11px; font-weight:bold; color:{color};"
            )

        elif mode == "그리드샷":
            if not self._gs_running:
                return
            gs = self._gs_canvas
            self._gs_hit_lbl.setText(str(gs._hit_count))
            self._gs_metrics["히트"].setText(str(gs._hit_count))
            self._gs_metrics["미스"].setText(str(gs._miss_count))
            avg_rt = (sum(gs._react_times) / len(gs._react_times) * 1000
                      if gs._react_times else 0)
            self._gs_metrics["평균 반응시간"].setText(
                f"{avg_rt:.0f} ms" if gs._react_times else "—"
            )
            self._gs_metrics["남은 시간"].setText(f"{gs.remaining}s")

    # ── 그리드샷 세션 동작 ──────────────────
    def _gs_start(self):
        if not self._gs_running and not self._gs_paused:
            self._gs_running = True
            self._gs_paused  = False
            self._gs_canvas.start()

    def _gs_pause_session(self):
        self._gs_running = False
        self._gs_paused  = True
        self._gs_canvas.stop()
        self._gs_pause_overlay.show()
        self._gs_pause_overlay.raise_()

    def _gs_resume_session(self):
        self._gs_paused  = False
        self._gs_running = True
        self._gs_pause_overlay.hide()
        self._gs_canvas.start_resume()

    def _gs_on_result(self, result: dict):
        """그리드샷 세션 종료 콜백"""
        self._gs_running = False
        # 결과 패널 갱신
        self._gs_hit_lbl.setText(str(result["hits"]))
        self._gs_metrics["히트"].setText(str(result["hits"]))
        self._gs_metrics["미스"].setText(str(result["misses"]))
        avg_rt = result["avg_react_ms"]
        self._gs_metrics["평균 반응시간"].setText(
            f"{avg_rt:.0f} ms" if avg_rt > 0 else "—"
        )
        self._gs_metrics["남은 시간"].setText("0s")

        # 결과 다이얼로그
        dlg = QDialog(self)
        dlg.setWindowTitle("그리드샷 결과")
        dlg.setFixedSize(380, 320)
        dlg.setStyleSheet("background:#1e1e1e; color:#fff;")
        dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(32, 32, 32, 32)
        lay.setSpacing(8)

        t = QLabel("그리드샷 완료")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet("font-size:13px; color:#888; letter-spacing:3px;")
        lay.addWidget(t)
        lay.addSpacing(10)

        for label, value in [
            ("히트",         str(result["hits"])),
            ("미스",         str(result["misses"])),
            ("평균 반응시간", f"{avg_rt:.0f} ms" if avg_rt > 0 else "—"),
        ]:
            row = QHBoxLayout()
            k = QLabel(label)
            k.setStyleSheet("font-size:14px; color:#aaa;")
            v = QLabel(value)
            v.setStyleSheet("font-size:20px; font-weight:bold; color:#ccccff;")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(k)
            row.addStretch()
            row.addWidget(v)
            lay.addLayout(row)
            sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color:#333;")
            lay.addWidget(sep)

        lay.addStretch()

        btn_row = QHBoxLayout()
        retry_btn = QPushButton("다시 시작")
        retry_btn.setFixedHeight(40)
        retry_btn.setStyleSheet("""
            QPushButton { background:#333; color:#fff; border:1px solid #555;
                          border-radius:6px; font-size:13px; }
            QPushButton:hover { background:#444; }
        """)
        retry_btn.clicked.connect(dlg.reject)

        close_btn = QPushButton("닫기")
        close_btn.setFixedHeight(40)
        close_btn.setStyleSheet("""
            QPushButton { background:#3333aa; color:#fff; border:none;
                          border-radius:6px; font-size:13px; font-weight:bold; }
            QPushButton:hover { background:#5555cc; }
        """)
        close_btn.clicked.connect(dlg.accept)

        btn_row.addWidget(retry_btn)
        btn_row.addSpacing(10)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        dlg.exec()


    # ── 통합 결과 + 감도 조정 다이얼로그 ──────────────────────
    def _show_combined_result_dialog(self, r: dict):
        verdict   = r["verdict"]
        hit_rate  = r["hit_rate"]
        avg_err   = r["avg_err"]
        max_err   = r["max_err"]
        osc_rate  = r["osc_rate"]
        cur_sens  = self._spinbox.value()

        colors = {"느림": "#4488ff", "빠름": "#ff4444", "적정": "#44cc77"}
        v_color = colors.get(verdict, "#fff")
        descs   = {
            "느림": "커서가 타겟을 자주 놓쳤습니다.\n감도를 조금 높여보세요.",
            "빠름": "커서가 타겟을 자주 지나쳤습니다.\n감도를 조금 낮춰보세요.",
            "적정": "커서가 타겟을 안정적으로 추적했습니다.\n현재 감도가 잘 맞습니다.",
        }

        dlg = QDialog(self)
        dlg.setFixedSize(460, 800)
        dlg.setStyleSheet("background:#1e1e1e; color:#fff;")
        dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(36, 32, 36, 32)
        root.setSpacing(0)

        # 타이틀
        title = QLabel("측정 완료")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:13px; color:#888; letter-spacing:3px;")
        root.addWidget(title)
        root.addSpacing(4)

        sens_lbl = QLabel(f"감도  {cur_sens:.2f}  기준")
        sens_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sens_lbl.setStyleSheet("font-size:11px; color:#555;")
        root.addWidget(sens_lbl)
        root.addSpacing(10)

        # 판정
        vl = QLabel(verdict)
        vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.setStyleSheet(f"font-size:64px; font-weight:bold; color:{v_color}; letter-spacing:4px;")
        root.addWidget(vl)

        desc = QLabel(descs.get(verdict, ""))
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setStyleSheet("font-size:11px; color:#aaa;")
        desc.setWordWrap(True)
        root.addWidget(desc)
        root.addSpacing(16)

        # 구분선
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#333;"); root.addWidget(sep)
        root.addSpacing(12)

        # 통계
        def hit_color(rate):
            if rate >= 60: return "#44cc77"
            if rate >= 35: return "#ffaa22"
            return "#ff4444"

        for label, value, color in [
            ("명중률",    f"{hit_rate:.1f} %",  hit_color(hit_rate)),
            ("평균 오차", f"{avg_err:.1f} px",  "#ccc"),
            ("최대 오차", f"{max_err:.1f} px",  "#ccc"),
            ("진동 비율", f"{osc_rate:.1f} %",  "#ccc"),
        ]:
            row = QHBoxLayout()
            k = QLabel(label); k.setStyleSheet("font-size:13px; color:#888;")
            v = QLabel(value); v.setStyleSheet(f"font-size:13px; font-weight:bold; color:{color};")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(k); row.addStretch(); row.addWidget(v)
            root.addLayout(row)
            root.addSpacing(8)

        root.addSpacing(8)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color:#333;"); root.addWidget(sep2)
        root.addSpacing(12)

        # 추천 감도 계산
        rec_delta = {"빠름": -0.1, "느림": +0.1, "적정": 0.0}.get(verdict, 0.0)
        rec_sens  = round(max(self._spinbox.minimum(),
                              min(self._spinbox.maximum(),
                                  cur_sens + rec_delta)), 6)

        rec_row = QHBoxLayout()
        rec_k = QLabel("추천 감도")
        rec_k.setStyleSheet("font-size:13px; color:#888;")
        if verdict == "적정":
            rec_val_text = f"{rec_sens:.4f}  (현재 적정)"
            rec_val_color = "#44cc77"
        elif verdict == "빠름":
            rec_val_text = f"{rec_sens:.4f}  (−0.1 권장)"
            rec_val_color = "#ff8888"
        else:
            rec_val_text = f"{rec_sens:.4f}  (+0.1 권장)"
            rec_val_color = "#88aaff"
        rec_v = QLabel(rec_val_text)
        rec_v.setStyleSheet(f"font-size:13px; font-weight:bold; color:{rec_val_color};")
        rec_v.setAlignment(Qt.AlignmentFlag.AlignRight)
        rec_row.addWidget(rec_k); rec_row.addStretch(); rec_row.addWidget(rec_v)
        root.addLayout(rec_row)
        root.addSpacing(12)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet("color:#333;"); root.addWidget(sep3)
        root.addSpacing(12)

        # 현재 감도 표시
        self._combined_sens_lbl = QLabel(f"현재 감도:  {cur_sens:.4f}")
        self._combined_sens_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._combined_sens_lbl.setStyleSheet("font-size:14px; color:#ccc; font-weight:bold;")
        root.addWidget(self._combined_sens_lbl)
        root.addSpacing(10)

        # 미세 조정 버튼
        fine_lbl = QLabel("감도 조정")
        fine_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fine_lbl.setStyleSheet("font-size:10px; color:#666;")
        root.addWidget(fine_lbl)
        root.addSpacing(6)

        # 조정 버튼 행
        _applied_sens = [cur_sens]

        def apply_delta(delta):
            new_s = round(max(self._spinbox.minimum(),
                              min(self._spinbox.maximum(),
                                  _applied_sens[0] + delta)), 6)
            _applied_sens[0] = new_s
            self._spinbox.setValue(new_s)
            self._on_sensitivity_changed(new_s)
            self._combined_sens_lbl.setText(f"현재 감도:  {new_s:.4f}")
            arrow = "↑" if delta > 0 else "↓"
            self._add_sens_log(verdict, cur_sens, new_s, arrow)

        def _btn(label, delta, bg, hov):
            b = QPushButton(label)
            b.setFixedHeight(40)
            b.setStyleSheet(f"""
                QPushButton {{ background:{bg}; color:#fff; border:none;
                              border-radius:6px; font-size:12px; font-weight:bold; }}
                QPushButton:hover {{ background:{hov}; }}
            """)
            b.clicked.connect(lambda: apply_delta(delta))
            return b

        btn_row = QHBoxLayout(); btn_row.setSpacing(6)
        btn_row.addWidget(_btn("−0.1",  -0.1,  "#882222", "#aa3333"))
        btn_row.addWidget(_btn("−0.01", -0.01, "#663333", "#884444"))
        btn_row.addWidget(_btn("+0.01", +0.01, "#334466", "#445588"))
        btn_row.addWidget(_btn("+0.1",  +0.1,  "#224488", "#336699"))
        root.addLayout(btn_row)
        root.addSpacing(12)

        # 게임 설정 파일 적용 버튼
        game_name = GAME_PRESETS[self._game_combo.currentIndex()][0]
        cfg       = GAME_CONFIG.get(game_name)
        supported = cfg is not None

        # ── 경로 미리보기 (적용 전 실제 경로 표시) ──
        # 유저 지정 경로 우선, 없으면 자동 탐색
        _cur_path = [self._custom_config_paths.get(game_name)]
        if not _cur_path[0] and supported:
            try:
                _cur_path[0] = cfg.get("path") or (cfg["path_fn"]() if "path_fn" in cfg else None)
            except Exception:
                _cur_path[0] = None

        def _path_exists():
            return bool(_cur_path[0] and os.path.exists(_cur_path[0]))

        path_preview = QLabel()
        path_preview.setWordWrap(True)
        path_preview.setAlignment(Qt.AlignmentFlag.AlignLeft)

        def _refresh_path_ui():
            """경로 라벨·적용 버튼 상태를 현재 _cur_path 기준으로 갱신"""
            ok = _path_exists()
            if not supported:
                path_preview.setText(f"⚠  {game_name}은 자동 적용 미지원")
                path_preview.setStyleSheet("font-size:9px; color:#555;")
            elif ok:
                is_custom = game_name in self._custom_config_paths
                tag = " [직접 지정]" if is_custom else ""
                path_preview.setText(f"📁  {_cur_path[0]}{tag}")
                path_preview.setStyleSheet("font-size:9px; color:#5dcc77;")
            else:
                path_preview.setText(
                    f"⚠  설정 파일을 찾을 수 없음\n"
                    f"({'경로 탐색 실패' if not _cur_path[0] else _cur_path[0]})"
                )
                path_preview.setStyleSheet("font-size:9px; color:#ff8855;")
            apply_btn.setEnabled(supported and ok)
            _style = f"""
                QPushButton {{
                    background:{'#1a5c2a' if ok else '#2a2a2a'};
                    color:{'#7dff9a' if ok else '#555'};
                    border:1px solid {'#2d8c45' if ok else '#333'};
                    border-radius:6px; font-size:12px; font-weight:bold;
                }}
                QPushButton:hover {{ background:#2a7a3a; }}
                QPushButton:disabled {{ color:#444; }}
            """
            apply_btn.setStyleSheet(_style)

        def _pick_file():
            """파일 열기 다이얼로그로 설정 파일을 직접 지정"""
            start_dir = os.path.dirname(_cur_path[0]) if _cur_path[0] else os.path.expanduser("~")
            chosen, _ = QFileDialog.getOpenFileName(
                dlg,
                f"{game_name} 설정 파일 선택",
                start_dir,
                "설정 파일 (*.cfg *.ini *.txt *.profile PROFSAVE_profile);;모든 파일 (*.*)",
            )
            if chosen:
                _cur_path[0] = chosen
                self._custom_config_paths[game_name] = chosen
                _refresh_path_ui()

        # 경로 라벨 + "직접 선택" 버튼 가로 배치
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_row.addWidget(path_preview, stretch=1)

        pick_btn = QPushButton("📂")
        pick_btn.setFixedSize(32, 28)
        pick_btn.setToolTip("설정 파일을 직접 선택")
        pick_btn.setStyleSheet("""
            QPushButton {
                background:#2a2a3a; color:#aaaaff;
                border:1px solid #444; border-radius:5px; font-size:14px;
            }
            QPushButton:hover { background:#3a3a5a; border-color:#7777cc; }
        """)
        pick_btn.clicked.connect(_pick_file)
        if supported:
            path_row.addWidget(pick_btn, alignment=Qt.AlignmentFlag.AlignTop)

        root.addLayout(path_row)
        root.addSpacing(6)

        apply_btn = QPushButton(f"▶  {game_name} 설정 파일에 적용")
        apply_btn.setFixedHeight(38)
        apply_btn.setEnabled(False)   # _refresh_path_ui 에서 최종 결정

        apply_status = QLabel()
        apply_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        apply_status.setStyleSheet("font-size:10px; color:#666;")
        apply_status.setWordWrap(True)

        def _set_status(ok, msg):
            apply_status.setStyleSheet(
                f"font-size:10px; color:{'#5dcc77' if ok else '#ff6666'};")
            apply_status.setText(("✓  " if ok else "✗  ") + msg)
            # 적용 성공 시 복구 버튼 활성화
            has_backup = game_name in self._config_backups
            restore_btn.setEnabled(has_backup)
            restore_btn.setStyleSheet(_restore_style(has_backup))

        def _do_apply():
            ok, msg = self._apply_sens_to_config(_applied_sens[0])
            _set_status(ok, msg)

        def _do_restore():
            ok, msg = self._restore_config()
            _set_status(ok, msg)

        def _restore_style(enabled: bool) -> str:
            if enabled:
                return """QPushButton {
                    background:#2a2010; color:#ffcc66;
                    border:1px solid #886622; border-radius:6px;
                    font-size:12px; font-weight:bold;
                }
                QPushButton:hover { background:#3a3010; }"""
            return """QPushButton {
                background:#222; color:#444;
                border:1px solid #333; border-radius:6px;
                font-size:12px;
            }"""

        has_backup = game_name in self._config_backups
        restore_btn = QPushButton("↩  원래 설정으로 복구")
        restore_btn.setFixedHeight(38)
        restore_btn.setEnabled(has_backup)
        restore_btn.setStyleSheet(_restore_style(has_backup))
        restore_btn.clicked.connect(_do_restore)

        apply_btn.clicked.connect(_do_apply)
        # 경로 라벨·버튼 초기 상태 반영 (apply_btn 생성 후 호출)
        _refresh_path_ui()

        apply_row = QHBoxLayout(); apply_row.setSpacing(6)
        apply_row.addWidget(apply_btn, stretch=3)
        apply_row.addWidget(restore_btn, stretch=2)

        root.addLayout(apply_row)
        root.addSpacing(4)
        root.addWidget(apply_status)
        root.addSpacing(10)

        # 하단 버튼
        bottom = QHBoxLayout(); bottom.setSpacing(12)

        retry_btn = QPushButton("다시 측정")
        retry_btn.setFixedHeight(42)
        retry_btn.setStyleSheet("""
            QPushButton { background:#333; color:#fff; border:1px solid #555;
                          border-radius:6px; font-size:13px; }
            QPushButton:hover { background:#444; }
        """)
        retry_btn.clicked.connect(dlg.reject)

        close_btn = QPushButton("닫기")
        close_btn.setFixedHeight(42)
        close_btn.setStyleSheet("""
            QPushButton { background:#cc2222; color:#fff; border:none;
                          border-radius:6px; font-size:13px; font-weight:bold; }
            QPushButton:hover { background:#ee4444; }
        """)
        close_btn.clicked.connect(dlg.accept)

        bottom.addWidget(retry_btn)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        dlg.exec()

    # ── 세션 기록 저장/불러오기 ──────────────────
    def _save_session(self, r: dict):
        """세션 결과를 JSON 파일에 누적 저장"""
        entry = {
            "timestamp":   time.strftime("%Y-%m-%d %H:%M:%S"),
            "game":        GAME_PRESETS[self._game_combo.currentIndex()][0],
            "mode":        self._current_mode,
            "sensitivity": round(self._spinbox.value(), 6),
            "dpi":         int(self._dpi_spinbox.value()),
            "hit_rate":    round(r["hit_rate"],  2),
            "avg_err":     round(r["avg_err"],   2),
            "max_err":     round(r["max_err"],   2),
            "osc_rate":    round(r["osc_rate"],  2),
            "verdict":     r["verdict"],
        }
        history = self._load_history_raw()
        history.append(entry)
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_history_raw(self) -> list:
        """파일에서 전체 기록 목록 반환 (실패 시 빈 리스트)"""
        if not os.path.exists(HISTORY_FILE):
            return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _load_history(self):
        """앱 시작 시 최근 5회 세션을 그래프에 복원"""
        history = self._load_history_raw()
        # 트래킹 세션만 필터링
        tracking = [e for e in history if e.get("mode") in ("트래킹 1", "트래킹 2")]
        for entry in tracking[-5:]:
            self._err_graph.push(entry.get("hit_rate", 0), entry.get("verdict", "적정"))

    def _open_path_settings(self):
        """⚙ 버튼 → 게임 경로 관리 다이얼로그 열기"""
        dlg = GamePathSettingsDialog(self, self._custom_config_paths)
        dlg.exec()

    def _apply_from_calc(self, game_name: str, sens: float, dpi: float):
        """감도 계산기 '적용' 버튼 → 메인 게임 콤보·감도·DPI 스핀박스 동기화"""
        # 게임 콤보 변경
        for i, (name, *_) in enumerate(GAME_PRESETS):
            if name == game_name:
                self._game_combo.blockSignals(True)
                self._game_combo.setCurrentIndex(i)
                self._game_combo.blockSignals(False)
                break

        # _on_game_changed 로 범위·step 재설정
        idx = self._game_combo.currentIndex()
        self._on_game_changed(idx)

        # 감도 클램프 후 적용
        clamped = max(self._spinbox.minimum(), min(self._spinbox.maximum(), sens))
        self._spinbox.setValue(clamped)

        # DPI 동기화
        self._dpi_spinbox.setValue(dpi)

        # 감도 계산기 → 트래킹 화면으로 전환
        self._on_mode_changed("트래킹 1")

    def _add_sens_log(self, verdict: str, before: float, after: float, arrow: str):
        entry = f"{verdict}  {before:.2f} {arrow} {after:.2f}"
        self._sens_log.append(entry)
        # 최근 6개만 표시
        self._sens_log_lbl.setText("\n".join(self._sens_log[-6:]))


# ────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
