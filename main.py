import sys
import os
import re
import glob
import json
import base64
try:
    import winreg
    _HAS_WINREG = True
except ImportError:
    _HAS_WINREG = False

try:
    import cv2 as _cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

try:
    import anthropic as _anthropic_mod
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QPushButton, QHBoxLayout, QVBoxLayout, QFrame,
    QDialog, QDoubleSpinBox, QComboBox,
    QFileDialog, QLineEdit, QProgressBar,
    QMessageBox, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont


# ── 테마 팔레트 ──────────────────────────────────────────────
_THEMES = {
    "light": {
        "bg":     "#f0ede8",
        "bg2":    "#e8e4de",
        "bg3":    "#dedad4",
        "bg4":    "#eae6e0",
        "border": "#d0ccc6",
        "sep":    "#c0bcb6",
        "txt":    "#222222",
        "txt2":   "#555555",
        "txt3":   "#666666",
        "txt4":   "#888888",
        "txt5":   "#999999",
        "canvas": "#f5f5f0",
        "cb_bg":  "#f0ede8",
        "cb_sel": "#dedad4",
        "sp_c":   "#333333",
        "sp_b":   "#bbbbbb",
        "sp_f":   "#555555",
    },
    "dark": {
        "bg":     "#1a1a1a",
        "bg2":    "#242424",
        "bg3":    "#2e2e2e",
        "bg4":    "#202020",
        "border": "#383838",
        "sep":    "#444444",
        "txt":    "#e0e0e0",
        "txt2":   "#bbbbbb",
        "txt3":   "#999999",
        "txt4":   "#777777",
        "txt5":   "#555555",
        "canvas": "#1e1e1e",
        "cb_bg":  "#2a2a2a",
        "cb_sel": "#3a3a3a",
        "sp_c":   "#dddddd",
        "sp_b":   "#555555",
        "sp_f":   "#aaaaaa",
    },
}
_cur_theme = "light"

def _p() -> dict:
    return _THEMES[_cur_theme]

def _spinbox_style(size: int) -> str:
    p = _p()
    return (
        f"QDoubleSpinBox {{ font-size:{size}px; font-weight:bold; color:{p['sp_c']};"
        f" background:transparent; border:none;"
        f" border-bottom:2px solid {p['sp_b']}; padding-bottom:2px; }}"
        f"QDoubleSpinBox:focus {{ border-bottom-color:{p['sp_f']}; }}"
        f"QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width:18px; }}"
    )

def _combo_style(size: int) -> str:
    p = _p()
    return (
        f"QComboBox {{ font-size:{size}px; font-weight:bold; color:{p['sp_c']};"
        f" background:transparent; border:none;"
        f" border-bottom:2px solid {p['sp_b']}; padding-bottom:2px; }}"
        f"QComboBox:focus {{ border-bottom-color:{p['sp_f']}; }}"
        f"QComboBox::drop-down {{ border:none; width:20px; }}"
        f"QComboBox QAbstractItemView {{ background:{p['cb_bg']}; border:1px solid {p['border']};"
        f" color:{p['txt']}; selection-background-color:{p['cb_sel']};"
        f" selection-color:{p['txt']}; font-size:{size}px; }}"
    )


# ── Steam / 게임 설정 파일 경로 탐색 헬퍼 ────────────────────
def _steam_path() -> str:
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
    base = _steam_path()
    libraries = [base]
    vdf_path = os.path.join(base, r"steamapps\libraryfolders.vdf")
    if os.path.exists(vdf_path):
        try:
            with open(vdf_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            for match in re.finditer(r'"path"\s+"([^"]+)"', content):
                folder = match.group(1).replace("\\\\", "\\").replace("/", "\\")
                if folder not in libraries and os.path.isdir(folder):
                    libraries.append(folder)
        except Exception:
            pass
    for root in [
        r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
        r"D:\Steam", r"D:\SteamLibrary", r"E:\Steam", r"E:\SteamLibrary",
        r"F:\Steam", r"F:\SteamLibrary", r"G:\Steam", r"G:\SteamLibrary",
    ]:
        if root not in libraries and os.path.isdir(root):
            libraries.append(root)
    return libraries


def _find_cs2_config() -> str | None:
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


# ── 게임별 설정 파일 정보 ─────────────────────────────────────
GAME_CONFIG = {
    "Apex Legends": {
        "path":        os.path.expandvars(
            r"%USERPROFILE%\Saved Games\Respawn\Apex\profile\profile.cfg"),
        "path_hint":   r"%USERPROFILE%\Saved Games\Respawn\Apex\profile\profile.cfg",
        "pattern":     r'(mouse_sensitivity\s+)"[^"]*"',
        "repl":        r'\g<1>"{val}"',
        "fmt":         lambda v: f"{v:.6f}",
        "append_tmpl": 'mouse_sensitivity "{val}"\n',
    },
    "CS2": {
        "path_fn":   _find_cs2_config,
        "path_hint": r"[Steam]\userdata\{UID}\730\local\cfg\cs2_user_convars_0_slot0.vcfg",
        "patterns": [
            (r'"sensitivity"(\s+)"[0-9.]+"', r'"sensitivity"\g<1>"{val}"'),
            (r'(?<!["\w])(sensitivity)(\s+)"?[0-9.]+"?', r'\g<1>\g<2>"{val}"'),
        ],
        "fmt": lambda v: f"{v:.6f}",
    },
    "Valorant": {
        "path_fn": lambda: (
            _first_glob(os.path.expandvars(
                r"%LOCALAPPDATA%\VALORANT\Saved\Config\Windows\*\GameUserSettings.ini"))
            or _first_glob(os.path.expandvars(
                r"%LOCALAPPDATA%\Riot Games\VALORANT\Saved\Config\Windows\*\GameUserSettings.ini"))
            or _first_glob(os.path.expandvars(
                r"%LOCALAPPDATA%\VALORANT\Saved\Config\*\GameUserSettings.ini"))
        ),
        "path_hint": r"%LOCALAPPDATA%\VALORANT\Saved\Config\Windows\{UUID}\GameUserSettings.ini",
        "pattern":   r'(MouseSensitivity=)[^\r\n]*',
        "repl":      r'\g<1>{val}',
        "fmt":       lambda v: f"{v:.6f}",
    },
    "Fortnite": {
        "path_fn": lambda: _first_glob(os.path.expandvars(
            r"%LOCALAPPDATA%\FortniteGame\Saved\Config\WindowsClient\GameUserSettings.ini")),
        "path_hint": r"%LOCALAPPDATA%\FortniteGame\Saved\Config\WindowsClient\GameUserSettings.ini",
        "pattern":   r'(MouseSensitivity=)([0-9.]+)',
        "repl":      r'\g<1>{val}',
        "fmt":       lambda v: f"{v:.6f}",
        "extra_pat": [(r'(MouseTargetingMultiplier=)([0-9.]+)', r'\g<1>{val}')],
    },
    "PUBG": {
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
    "Call of Duty": None,
}

# 게임별 감도 설정 (이름, 기본값, 최소, 최대, 단계)
GAME_PRESETS = [
    ("Apex Legends",      2.50,  0.10,  3.00, 0.10),
    ("Valorant",          0.786, 0.001, 10.00, 0.001),
    ("CS2",               2.50,  0.10,  10.00, 0.05),
    ("Overwatch 2",       8.33,  1.00,  100.00, 0.01),
    ("Fortnite",          0.099, 0.001, 1.00,  0.001),
    ("PUBG",              50.00, 0.00,  100.00, 1.00),
    ("Rainbow Six Siege", 10.00, 1.00,  100.00, 1.00),
    ("Call of Duty",      8.33,  1.00,  20.00,  0.01),
    ("Battlefield 2042",  30.30, 1.00,  100.00, 0.10),
]


# ────────────────────────────────────────────
#  영상 분석 백그라운드 워커
# ────────────────────────────────────────────
class VideoAnalysisWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)

    def __init__(self, video_path: str, api_key: str,
                 game_name: str, current_sens: float, dpi: float):
        super().__init__()
        self.video_path   = video_path
        self.api_key      = api_key
        self.game_name    = game_name
        self.current_sens = current_sens
        self.dpi          = dpi

    def run(self):
        try:
            self._analyze()
        except Exception as exc:
            self.error.emit(str(exc))

    def _analyze(self):
        self.progress.emit(5, "영상 로딩 중…")

        cap = _cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            self.error.emit("영상 파일을 열 수 없습니다.")
            return

        total_frames = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
        fps          = cap.get(_cv2.CAP_PROP_FPS) or 30.0
        duration     = total_frames / fps

        N       = 12
        indices = [int(total_frames * i / N) for i in range(N)]
        frames_b64 = []
        for idx in indices:
            cap.set(_cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            h, w = frame.shape[:2]
            if w > 1280:
                frame = _cv2.resize(frame, (1280, int(h * 1280 / w)))
            _, buf = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 75])
            frames_b64.append(base64.standard_b64encode(buf.tobytes()).decode())
        cap.release()

        if not frames_b64:
            self.error.emit("영상에서 프레임을 추출할 수 없습니다.")
            return

        self.progress.emit(30, f"프레임 {len(frames_b64)}개 추출 완료 ({duration:.1f}초 영상)")

        client = _anthropic_mod.Anthropic(api_key=self.api_key)

        content = [
            {
                "type": "text",
                "text": (
                    f"당신은 FPS/TPS 게임 마우스 감도 분석 전문가입니다.\n"
                    f"아래에 '{self.game_name}' 게임플레이 영상에서 균등 간격으로 추출한 "
                    f"{len(frames_b64)}개의 프레임이 있습니다.\n"
                    f"현재 설정: 감도 {self.current_sens}, DPI {int(self.dpi)}\n\n"
                    "각 프레임과 프레임 간 변화를 보고 플레이어의 에임(조준) 패턴을 분석해주세요:\n"
                    "• 크로스헤어가 적을 지나치고 되돌아오는 오버슈팅이 잦으면 → '빠름'\n"
                    "• 크로스헤어가 적에 느리게 접근하거나 따라가지 못하면 → '느림'\n"
                    "• 조준이 자연스럽고 적을 잘 추적하면 → '적정'\n\n"
                    "적이 없는 구간이 많더라도 크로스헤어의 이동 속도·진폭으로도 판단해주세요.\n\n"
                    "반드시 아래 JSON 형식만 출력하세요 (다른 텍스트 없이):\n"
                    '{"verdict":"빠름"|"느림"|"적정",'
                    '"confidence":0~100,'
                    '"overshoot_count":오버슈팅_횟수,'
                    '"undershoot_count":언더슈팅_횟수,'
                    '"reasoning":"한국어 2~3문장",'
                    '"suggested_change":퍼센트_정수_(-20~+20)}'
                )
            }
        ]
        for b64 in frames_b64:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
            })

        self.progress.emit(50, "Claude AI 분석 중…")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": content}]
        )

        self.progress.emit(90, "결과 처리 중…")

        raw = response.content[0].text.strip()
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            result = json.loads(m.group())
        else:
            result = {
                "verdict": "알 수 없음",
                "confidence": 0,
                "overshoot_count": 0,
                "undershoot_count": 0,
                "reasoning": raw,
                "suggested_change": 0,
            }

        result["frames_analyzed"] = len(frames_b64)
        result["video_duration"]  = round(duration, 1)
        self.progress.emit(100, "완료")
        self.finished.emit(result)


# ────────────────────────────────────────────
#  영상 감도 분석 위젯
# ────────────────────────────────────────────
class VideoAnalysisWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.on_apply    = None   # callback(sens, game_idx, dpi)
        self._worker     = None
        self._video_path = ""
        self._result     = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        root.addWidget(self._build_left(), stretch=0)
        root.addWidget(self._build_right(), stretch=1)

    # ── 좌측 설정 패널 ───────────────────────
    def _build_left(self):
        self._va_left = QFrame()
        self._va_left.setFixedWidth(290)
        lay = QVBoxLayout(self._va_left)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        self._va_title = QLabel("영상 감도 분석")
        self._va_title.setStyleSheet("font-size:15px; font-weight:bold;")
        lay.addWidget(self._va_title)

        _sep0 = QFrame(); _sep0.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(_sep0)

        self._va_api_lbl = QLabel("Anthropic API 키")
        lay.addWidget(self._va_api_lbl)

        api_row = QHBoxLayout()
        api_row.setSpacing(4)
        self._va_api_edit = QLineEdit()
        self._va_api_edit.setPlaceholderText("sk-ant-...")
        self._va_api_edit.setEchoMode(QLineEdit.EchoMode.Password)
        api_row.addWidget(self._va_api_edit)

        self._va_show_btn = QPushButton("표시")
        self._va_show_btn.setFixedSize(44, 28)
        self._va_show_btn.setCheckable(True)
        self._va_show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._va_show_btn.toggled.connect(
            lambda on: self._va_api_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        api_row.addWidget(self._va_show_btn)
        lay.addLayout(api_row)

        _sep1 = QFrame(); _sep1.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(_sep1)

        self._va_file_lbl = QLabel("게임플레이 영상")
        lay.addWidget(self._va_file_lbl)

        self._va_path_lbl = QLabel("파일을 선택해주세요")
        self._va_path_lbl.setWordWrap(True)
        self._va_path_lbl.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(self._va_path_lbl)

        self._va_pick_btn = QPushButton("파일 선택…")
        self._va_pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._va_pick_btn.clicked.connect(self._pick_video)
        lay.addWidget(self._va_pick_btn)

        _sep2 = QFrame(); _sep2.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(_sep2)

        self._va_game_lbl = QLabel("게임")
        lay.addWidget(self._va_game_lbl)
        self._va_game_combo = QComboBox()
        for name, *_ in GAME_PRESETS:
            self._va_game_combo.addItem(name)
        self._va_game_combo.currentIndexChanged.connect(self._sync_sens_range)
        lay.addWidget(self._va_game_combo)

        self._va_sens_lbl = QLabel("현재 감도")
        lay.addWidget(self._va_sens_lbl)
        _, def0, lo0, hi0, step0 = GAME_PRESETS[0]
        self._va_sens_spin = QDoubleSpinBox()
        self._va_sens_spin.setRange(lo0, hi0)
        self._va_sens_spin.setSingleStep(step0)
        self._va_sens_spin.setValue(def0)
        lay.addWidget(self._va_sens_spin)

        self._va_dpi_lbl = QLabel("DPI")
        lay.addWidget(self._va_dpi_lbl)
        self._va_dpi_spin = QDoubleSpinBox()
        self._va_dpi_spin.setRange(100, 32000)
        self._va_dpi_spin.setSingleStep(100)
        self._va_dpi_spin.setValue(800)
        self._va_dpi_spin.setDecimals(0)
        lay.addWidget(self._va_dpi_spin)

        lay.addStretch()

        self._va_progress = QProgressBar()
        self._va_progress.setRange(0, 100)
        self._va_progress.setValue(0)
        self._va_progress.hide()
        lay.addWidget(self._va_progress)

        self._va_status_lbl = QLabel("")
        self._va_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._va_status_lbl.setStyleSheet("font-size:11px; color:#888;")
        self._va_status_lbl.hide()
        lay.addWidget(self._va_status_lbl)

        self._va_start_btn = QPushButton("분석 시작")
        self._va_start_btn.setFixedHeight(44)
        self._va_start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._va_start_btn.setStyleSheet(
            "QPushButton { background:#2b7a3a; color:#fff;"
            " border-radius:6px; font-size:14px; font-weight:bold; }"
            "QPushButton:hover { background:#37a34a; }"
            "QPushButton:disabled { background:#555; color:#aaa; }"
        )
        self._va_start_btn.clicked.connect(self._start_analysis)
        lay.addWidget(self._va_start_btn)

        return self._va_left

    # ── 우측 결과 패널 ───────────────────────
    def _build_right(self):
        self._va_right = QFrame()
        lay = QVBoxLayout(self._va_right)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        self._va_res_title = QLabel("분석 결과")
        self._va_res_title.setStyleSheet("font-size:14px; font-weight:bold;")
        lay.addWidget(self._va_res_title)

        _sep3 = QFrame(); _sep3.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(_sep3)

        self._va_verdict_lbl = QLabel("—")
        self._va_verdict_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._va_verdict_lbl.setStyleSheet(
            "font-size:48px; font-weight:bold; color:#555;")
        lay.addWidget(self._va_verdict_lbl)

        self._va_conf_lbl = QLabel("")
        self._va_conf_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._va_conf_lbl.setStyleSheet("font-size:13px; color:#888;")
        lay.addWidget(self._va_conf_lbl)

        self._va_info_lbl = QLabel("")
        self._va_info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._va_info_lbl.setStyleSheet("font-size:11px; color:#aaa;")
        lay.addWidget(self._va_info_lbl)

        _sep4 = QFrame(); _sep4.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(_sep4)

        self._va_reason_lbl = QLabel("판단 근거")
        self._va_reason_lbl.setStyleSheet("font-size:12px; font-weight:bold;")
        lay.addWidget(self._va_reason_lbl)

        self._va_reason_text = QLabel("영상을 선택하고 분석을 시작하세요.")
        self._va_reason_text.setWordWrap(True)
        self._va_reason_text.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._va_reason_text.setStyleSheet(
            "font-size:13px; color:#555; padding:8px 0;")
        lay.addWidget(self._va_reason_text, stretch=1)

        _sep5 = QFrame(); _sep5.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(_sep5)

        self._va_sugg_lbl = QLabel("권장 감도")
        self._va_sugg_lbl.setStyleSheet("font-size:12px; font-weight:bold;")
        lay.addWidget(self._va_sugg_lbl)

        sugg_row = QHBoxLayout()
        sugg_row.setSpacing(12)
        self._va_change_lbl = QLabel("—")
        self._va_change_lbl.setStyleSheet(
            "font-size:22px; font-weight:bold; color:#888;")
        sugg_row.addWidget(self._va_change_lbl)
        self._va_arrow_lbl = QLabel("→")
        sugg_row.addWidget(self._va_arrow_lbl)
        self._va_new_sens_lbl = QLabel("—")
        self._va_new_sens_lbl.setStyleSheet(
            "font-size:22px; font-weight:bold; color:#2b7a3a;")
        sugg_row.addWidget(self._va_new_sens_lbl)
        sugg_row.addStretch()
        lay.addLayout(sugg_row)

        self._va_apply_btn = QPushButton("게임 설정에 적용하기")
        self._va_apply_btn.setFixedHeight(40)
        self._va_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._va_apply_btn.setEnabled(False)
        self._va_apply_btn.setStyleSheet(
            "QPushButton { background:#1a5276; color:#fff;"
            " border-radius:6px; font-size:13px; font-weight:bold; }"
            "QPushButton:hover { background:#2471a3; }"
            "QPushButton:disabled { background:#555; color:#888; }"
        )
        self._va_apply_btn.clicked.connect(self._apply_suggested)
        lay.addWidget(self._va_apply_btn)

        return self._va_right

    # ── 슬롯 ────────────────────────────────
    def _pick_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "게임플레이 영상 선택", "",
            "영상 파일 (*.mp4 *.avi *.mov *.mkv *.wmv *.flv);;모든 파일 (*)"
        )
        if path:
            self._video_path = path
            self._va_path_lbl.setText(os.path.basename(path))
            self._va_path_lbl.setStyleSheet("font-size:11px; color:#444;")

    def _sync_sens_range(self, idx: int):
        _, def_v, lo, hi, step = GAME_PRESETS[idx]
        self._va_sens_spin.blockSignals(True)
        self._va_sens_spin.setRange(lo, hi)
        self._va_sens_spin.setSingleStep(step)
        self._va_sens_spin.setDecimals(3 if step < 0.01 else 2 if step < 1 else 0)
        self._va_sens_spin.setValue(def_v)
        self._va_sens_spin.blockSignals(False)

    def _start_analysis(self):
        if not _HAS_CV2:
            self._va_reason_text.setText(
                "OpenCV가 설치되지 않았습니다.\n"
                "터미널에서 다음 명령을 실행해주세요:\n\n  pip install opencv-python-headless")
            return
        if not _HAS_ANTHROPIC:
            self._va_reason_text.setText(
                "anthropic 패키지가 설치되지 않았습니다.\n"
                "터미널에서 다음 명령을 실행해주세요:\n\n  pip install anthropic")
            return
        api_key = self._va_api_edit.text().strip()
        if not api_key:
            self._va_reason_text.setText("API 키를 입력해주세요.")
            return
        if not self._video_path or not os.path.exists(self._video_path):
            self._va_reason_text.setText("유효한 영상 파일을 선택해주세요.")
            return

        self._va_start_btn.setEnabled(False)
        self._va_progress.setValue(0)
        self._va_progress.show()
        self._va_status_lbl.show()
        self._va_verdict_lbl.setText("분석 중…")
        self._va_verdict_lbl.setStyleSheet(
            "font-size:48px; font-weight:bold; color:#888;")
        self._va_reason_text.setText("영상을 분석하고 있습니다. 잠시만 기다려 주세요…")
        self._va_apply_btn.setEnabled(False)
        self._va_change_lbl.setText("—")
        self._va_new_sens_lbl.setText("—")

        self._worker = VideoAnalysisWorker(
            self._video_path,
            api_key,
            self._va_game_combo.currentText(),
            self._va_sens_spin.value(),
            self._va_dpi_spin.value(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, pct: int, msg: str):
        self._va_progress.setValue(pct)
        self._va_status_lbl.setText(msg)

    def _on_result(self, result: dict):
        self._result = result
        self._va_start_btn.setEnabled(True)
        self._va_progress.hide()
        self._va_status_lbl.hide()

        verdict = result.get("verdict", "알 수 없음")
        conf    = result.get("confidence", 0)
        reason  = result.get("reasoning", "")
        change  = result.get("suggested_change", 0)
        frames  = result.get("frames_analyzed", 0)
        dur     = result.get("video_duration", 0)
        over    = result.get("overshoot_count", 0)
        under   = result.get("undershoot_count", 0)

        color_map = {"빠름": "#c0392b", "느림": "#2980b9", "적정": "#27ae60"}
        color = color_map.get(verdict, "#888888")
        self._va_verdict_lbl.setText(verdict)
        self._va_verdict_lbl.setStyleSheet(
            f"font-size:48px; font-weight:bold; color:{color};")
        self._va_conf_lbl.setText(
            f"신뢰도: {conf}%  |  오버슈팅: {over}회  /  언더슈팅: {under}회")
        self._va_info_lbl.setText(
            f"분석 프레임: {frames}개  |  영상 길이: {dur}초")
        self._va_reason_text.setText(reason or "(이유 없음)")

        current_sens = self._va_sens_spin.value()
        _, _, lo, hi, step = GAME_PRESETS[self._va_game_combo.currentIndex()]
        new_sens = current_sens * (1 + change / 100.0)
        new_sens = max(lo, min(hi, round(new_sens / step) * step))
        self._result["new_sens"] = new_sens

        sign = "+" if change >= 0 else ""
        self._va_change_lbl.setText(f"{sign}{change}%")
        dec = 3 if step < 0.01 else 2 if step < 1 else 0
        self._va_new_sens_lbl.setText(f"{new_sens:.{dec}f}")

        if abs(change) > 0 and verdict not in ("알 수 없음",):
            self._va_apply_btn.setEnabled(True)

    def _on_error(self, msg: str):
        self._va_start_btn.setEnabled(True)
        self._va_progress.hide()
        self._va_status_lbl.hide()
        self._va_verdict_lbl.setText("오류")
        self._va_verdict_lbl.setStyleSheet(
            "font-size:48px; font-weight:bold; color:#c0392b;")
        self._va_reason_text.setText(f"분석 중 오류가 발생했습니다:\n{msg}")

    def _apply_suggested(self):
        new_sens = self._result.get("new_sens")
        if new_sens is not None and self.on_apply:
            self.on_apply(new_sens, self._va_game_combo.currentIndex(),
                          self._va_dpi_spin.value())

    # ── 테마 적용 ────────────────────────────
    def apply_theme(self):
        p = _p()
        self._va_left.setStyleSheet(
            f"background:{p['bg2']}; border-radius:6px;")
        self._va_right.setStyleSheet(
            f"background:{p['bg2']}; border-radius:6px;")
        self._va_title.setStyleSheet(
            f"font-size:15px; font-weight:bold; color:{p['txt']};")
        self._va_res_title.setStyleSheet(
            f"font-size:14px; font-weight:bold; color:{p['txt']};")
        self._va_reason_lbl.setStyleSheet(
            f"font-size:12px; font-weight:bold; color:{p['txt']};")
        self._va_sugg_lbl.setStyleSheet(
            f"font-size:12px; font-weight:bold; color:{p['txt']};")
        for lbl in (self._va_api_lbl, self._va_file_lbl,
                    self._va_game_lbl, self._va_sens_lbl, self._va_dpi_lbl):
            lbl.setStyleSheet(f"font-size:11px; color:{p['txt4']};")
        self._va_conf_lbl.setStyleSheet(f"font-size:13px; color:{p['txt3']};")
        self._va_info_lbl.setStyleSheet(f"font-size:11px; color:{p['txt4']};")
        self._va_arrow_lbl.setStyleSheet(f"font-size:18px; color:{p['txt3']};")
        self._va_reason_text.setStyleSheet(
            f"font-size:13px; color:{p['txt2']}; padding:8px 0;")
        self._va_status_lbl.setStyleSheet(f"font-size:11px; color:{p['txt4']};")
        self._va_game_combo.setStyleSheet(_combo_style(14))
        self._va_sens_spin.setStyleSheet(_spinbox_style(14))
        self._va_dpi_spin.setStyleSheet(_spinbox_style(14))
        self._va_api_edit.setStyleSheet(
            f"border:1px solid {p['border']}; border-radius:4px;"
            f" padding:4px 6px; font-size:12px;"
            f" background:{p['bg3']}; color:{p['txt']};")


# ────────────────────────────────────────────
#  게임 설정 파일 경로 관리 다이얼로그
# ────────────────────────────────────────────
class GamePathSettingsDialog(QDialog):
    def __init__(self, parent, custom_paths: dict):
        super().__init__(parent)
        self.setWindowTitle("게임 설정 파일 경로")
        self.resize(660, 420)
        self._custom_paths = custom_paths

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        title = QLabel("게임별 설정 파일 경로")
        title.setStyleSheet("font-size:14px; font-weight:bold;")
        lay.addWidget(title)

        desc = QLabel("직접 경로를 지정하면 자동 탐색보다 우선 적용됩니다. 비워두면 자동 탐색합니다.")
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(desc)

        # 스크롤 영역에 게임별 행 나열
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 4, 0, 4)
        inner_lay.setSpacing(8)

        self._path_edits: dict[str, QLineEdit] = {}
        for name, *_ in GAME_PRESETS:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            name_lbl = QLabel(name)
            name_lbl.setFixedWidth(140)
            name_lbl.setStyleSheet("font-size:12px; font-weight:bold;")
            row.addWidget(name_lbl)

            edit = QLineEdit()
            edit.setPlaceholderText("자동 탐색")
            edit.setText(custom_paths.get(name, ""))
            self._path_edits[name] = edit
            row.addWidget(edit, stretch=1)

            browse_btn = QPushButton("…")
            browse_btn.setFixedSize(32, 28)
            browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            browse_btn.clicked.connect(lambda _, n=name: self._browse(n))
            row.addWidget(browse_btn)

            inner_lay.addWidget(row_widget)

        inner_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        lay.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        clear_btn = QPushButton("모두 초기화")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(clear_btn)

        ok_btn = QPushButton("확인")
        ok_btn.setFixedWidth(90)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self._save_and_close)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

    def _browse(self, game_name: str):
        path, _ = QFileDialog.getOpenFileName(
            self, f"{game_name} 설정 파일 선택", "",
            "설정 파일 (*.cfg *.ini *.vcfg *.txt);;모든 파일 (*)"
        )
        if path:
            self._path_edits[game_name].setText(path)

    def _clear_all(self):
        for edit in self._path_edits.values():
            edit.clear()

    def _save_and_close(self):
        for name, edit in self._path_edits.items():
            path = edit.text().strip()
            if path:
                self._custom_paths[name] = path
            elif name in self._custom_paths:
                del self._custom_paths[name]
        self.accept()


# ────────────────────────────────────────────
#  메인 윈도우
# ────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("에임 트레이너")
        self.resize(1200, 720)

        self._custom_config_paths: dict[str, str] = {}
        self._config_backups: dict[str, tuple[str, str]] = {}

        self._build_ui()
        self._apply_theme()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._make_topbar())

        self._video_analysis = VideoAnalysisWidget()
        self._video_analysis.on_apply = self._apply_from_video_analysis
        root.addWidget(self._video_analysis, stretch=1)

    def _make_topbar(self):
        self._topbar = QFrame()
        self._topbar.setFixedHeight(44)
        lay = QHBoxLayout(self._topbar)
        lay.setContentsMargins(16, 0, 16, 0)

        self._title_lbl = QLabel("에임 트레이너")
        self._title_lbl.setStyleSheet("font-size:15px; font-weight:bold;")
        lay.addWidget(self._title_lbl)
        lay.addStretch()

        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setFixedSize(28, 28)
        self._settings_btn.setToolTip("게임 설정 파일 경로 관리")
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.clicked.connect(self._open_path_settings)
        lay.addWidget(self._settings_btn)
        lay.addSpacing(4)

        self._theme_btn = QPushButton("☽")
        self._theme_btn.setFixedSize(28, 28)
        self._theme_btn.setToolTip("다크/라이트 테마 전환")
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self._theme_btn)

        return self._topbar

    def _toggle_theme(self):
        global _cur_theme
        _cur_theme = "dark" if _cur_theme == "light" else "light"
        self._apply_theme()

    def _apply_theme(self):
        p = _p()
        dark = (_cur_theme == "dark")
        self.centralWidget().setStyleSheet(f"background:{p['bg']};")
        self._topbar.setStyleSheet(
            f"background:{p['bg2']}; border-bottom:1px solid {p['border']};")
        self._title_lbl.setStyleSheet(
            f"font-size:15px; font-weight:bold; color:{p['txt']};")
        icon_style = (
            f"QPushButton {{ background:transparent; color:{p['txt4']};"
            f" border:1px solid transparent; border-radius:4px; font-size:15px; }}"
            f"QPushButton:hover {{ background:{p['bg3']}; color:{p['txt']};"
            f" border-color:{p['border']}; }}"
        )
        self._settings_btn.setStyleSheet(icon_style)
        self._theme_btn.setStyleSheet(icon_style)
        self._theme_btn.setText("☼" if dark else "☽")
        self._video_analysis.apply_theme()

    def _open_path_settings(self):
        dlg = GamePathSettingsDialog(self, self._custom_config_paths)
        dlg.exec()

    def _apply_from_video_analysis(self, sens: float, game_idx: int, dpi: float):
        ok, msg = self._write_game_config(sens, game_idx)
        if ok:
            QMessageBox.information(self, "감도 적용 완료", msg)
        else:
            QMessageBox.warning(self, "적용 실패", msg)

    def _write_game_config(self, sens: float, game_idx: int) -> tuple[bool, str]:
        game_name = GAME_PRESETS[game_idx][0]
        cfg = GAME_CONFIG.get(game_name)

        if cfg is None:
            return False, f"'{game_name}'은 자동 적용을 지원하지 않습니다."

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
                f"⚙ 버튼에서 경로를 직접 지정해주세요.\n"
                f"예상 위치: {hint}"
            )
        if not os.path.exists(path):
            return False, (
                f"설정 파일이 존재하지 않습니다.\n"
                f"게임을 한 번 이상 실행해 설정 파일을 생성하세요.\n"
                f"경로: {path}"
            )

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            return False, f"파일 읽기 실패:\n{path}\n오류: {e}"

        if game_name not in self._config_backups:
            self._config_backups[game_name] = (path, content)

        val_str = cfg["fmt"](sens)

        def do_replace(text, pattern, repl_tmpl):
            repl = repl_tmpl.replace("{val}", val_str)
            new_text, n = re.subn(pattern, repl, text)
            return new_text, n

        pattern_list = cfg.get("patterns")
        if pattern_list:
            new_content, count = content, 0
            for pat, rep in pattern_list:
                new_content, count = do_replace(content, pat, rep)
                if count > 0:
                    break
        else:
            new_content, count = do_replace(content, cfg["pattern"], cfg["repl"])

        if count == 0:
            append_tmpl = cfg.get("append_tmpl")
            if append_tmpl:
                append_line = append_tmpl.replace("{val}", val_str)
                new_content = content + ("" if content.endswith("\n") else "\n") + append_line
            else:
                preview = content[:200].replace("\n", "↵").replace("\t", "→")
                return False, (
                    f"설정 파일에서 감도 항목을 찾지 못했습니다.\n"
                    f"게임을 한 번 이상 실행한 뒤 다시 시도하세요.\n\n"
                    f"파일 미리보기:\n{preview}"
                )

        for pat, rep in cfg.get("extra_pat", []):
            new_content, _ = do_replace(new_content, pat, rep)

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

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                verify = f.read()
            if val_str not in verify:
                return False, f"파일 쓰기는 성공했지만 값 확인 실패.\n({val_str}이 파일에서 발견되지 않음)"
        except Exception:
            pass

        return True, f"감도 {val_str} 적용 완료\n({os.path.basename(path)})"


# ────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
