"""
Highlight Grab v1.0
Keyboard-driven video review and segment extraction tool.
Uses VLC for playback and FFmpeg stream copy for lossless export.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import subprocess
import os
import sys
import time
import json
import uuid
from pathlib import Path

try:
    import vlc
except ImportError:
    vlc = None

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    TkinterDnD = None
    DND_FILES = None
    _DND_AVAILABLE = False

# ── Constants ──────────────────────────────────────────────────────────────────
APP_NAME = "Highlight Grab"
APP_VERSION = "v1"
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m4v"}
BG = "#1a1a1a"
PANEL_BG = "#242424"
ACCENT = "#f5a623"
TEXT = "#e8e8e8"
MUTED = "#888888"
DANGER = "#e74c3c"
TIMELINE_H = 48
THUMB_W, THUMB_H = 120, 68


# ── FFmpeg discovery ───────────────────────────────────────────────────────────
def find_ffmpeg():
    # Check PATH first
    for candidate in ["ffmpeg", "ffmpeg.exe"]:
        try:
            result = subprocess.run(
                [candidate, "-version"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    # Check script directory
    script_dir = Path(sys.argv[0]).parent
    for name in ["ffmpeg.exe", "ffmpeg"]:
        p = script_dir / name
        if p.exists():
            return str(p)
    return None


FFMPEG = find_ffmpeg()


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_time(seconds):
    if seconds is None or seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_video_duration(path):
    if not FFMPEG:
        return 0
    try:
        result = subprocess.run(
            [FFMPEG, "-i", path],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stderr.splitlines():
            if "Duration:" in line:
                dur_str = line.split("Duration:")[1].split(",")[0].strip()
                parts = dur_str.split(":")
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        pass
    return 0


def extract_thumbnail(video_path, at_second):
    if cv2 is None or Image is None:
        return None
    try:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        frame_no = int(at_second * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = frame.shape[:2]
        scale = min(THUMB_W / w, THUMB_H / h)
        nw, nh = int(w * scale), int(h * scale)
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
        img = Image.new("RGB", (THUMB_W, THUMB_H), (0, 0, 0))
        x_off = (THUMB_W - nw) // 2
        y_off = (THUMB_H - nh) // 2
        img.paste(Image.fromarray(resized), (x_off, y_off))
        return img
    except Exception:
        return None


# ── Main Application ───────────────────────────────────────────────────────────
_BaseWindow = TkinterDnD.Tk if _DND_AVAILABLE else tk.Tk

class HighlightGrab(_BaseWindow):
    def __init__(self):
        super().__init__()

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1280x768")
        self.minsize(900, 600)
        self.configure(bg=BG)

        # State
        self.files = []          # list of {"path": str, "duration": float}
        self.active_index = -1
        self.segments = []       # list of segment dicts
        self.thumb_cache = {}    # segment_id -> ImageTk.PhotoImage
        self.in_point = None
        self.out_point = None
        self.duration = 0.0
        self.is_playing = False
        self.speed = 1.0
        self._speed_options = [0.5, 1.0, 2.0, 4.0]
        self._drag_in = False
        self._drag_out = False
        self._toast_after = None

        # VLC
        self._vlc_instance = None
        self._player = None
        self._init_vlc()

        self._build_ui()
        self._bind_keys()
        self._register_drop_targets()

        self.after(100, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── VLC ──────────────────────────────────────────────────────────────────
    def _init_vlc(self):
        if vlc is None:
            return
        try:
            self._vlc_instance = vlc.Instance("--no-video-title-show", "--quiet")
            self._player = self._vlc_instance.media_player_new()
        except Exception as e:
            self._player = None
            print(f"VLC init error: {e}")

    # ── Drag-and-drop ────────────────────────────────────────────────────────
    def _register_drop_targets(self):
        if not _DND_AVAILABLE:
            return
        # Register ONLY the root window — never child widgets.
        # VLC steals video_frame's HWND so tkinter stops receiving events there.
        # The root HWND is always owned by tkinter and receives drops anywhere
        # on the application window without interfering with child button clicks.
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<DropEnter>>", self._on_drop_enter)
        self.dnd_bind("<<DropLeave>>", self._on_drop_leave)
        self.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop_enter(self, event):
        self._drop_hint = tk.Label(
            self.video_frame, text="⬇  Släpp videofiler här",
            font=("Segoe UI", 20, "bold"), fg="black", bg=ACCENT,
            padx=20, pady=10
        )
        self._drop_hint.place(relx=0.5, rely=0.5, anchor="center")
        return event.action

    def _on_drop_leave(self, event):
        if hasattr(self, "_drop_hint"):
            self._drop_hint.destroy()

    def _on_drop(self, event):
        if hasattr(self, "_drop_hint"):
            self._drop_hint.destroy()
        paths = self._parse_drop_paths(event.data)
        added = 0
        for p in paths:
            path = Path(p)
            if path.is_dir():
                for f in sorted(path.iterdir()):
                    if f.suffix.lower() in SUPPORTED_EXTENSIONS:
                        self._add_file(str(f))
                        added += 1
            elif path.suffix.lower() in SUPPORTED_EXTENSIONS:
                self._add_file(str(path))
                added += 1
        if added:
            self._show_toast(f"✓ {added} fil{'er' if added != 1 else ''} tillagd{'a' if added != 1 else ''}")
        else:
            self._show_toast("⚠ Inga videofiler hittades")

    @staticmethod
    def _parse_drop_paths(data: str) -> list[str]:
        """Parse the tkinterdnd2 drop string into a list of file paths.

        Windows Explorer wraps paths that contain spaces in curly braces,
        e.g.: {C:/My Videos/clip 1.mp4} C:/clean.mp4
        """
        paths = []
        data = data.strip()
        i = 0
        while i < len(data):
            if data[i] == "{":
                end = data.index("}", i)
                paths.append(data[i + 1:end])
                i = end + 1
            elif data[i] == " ":
                i += 1
            else:
                end = data.find(" ", i)
                if end == -1:
                    paths.append(data[i:])
                    break
                paths.append(data[i:end])
                i = end
        return paths

    # ── UI Build ─────────────────────────────────────────────────────────────
    def _build_ui(self):
        root_frame = tk.Frame(self, bg=BG)
        root_frame.pack(fill="both", expand=True)

        # Left panel
        self.left = tk.Frame(root_frame, bg=PANEL_BG, width=220)
        self.left.pack(side="left", fill="y")
        self.left.pack_propagate(False)
        self._build_left(self.left)

        # Center panel
        self.center = tk.Frame(root_frame, bg=BG)
        self.center.pack(side="left", fill="both", expand=True)
        self._build_center(self.center)

        # Right panel
        self.right = tk.Frame(root_frame, bg=PANEL_BG, width=260)
        self.right.pack(side="right", fill="y")
        self.right.pack_propagate(False)
        self._build_right(self.right)

    def _build_left(self, parent):
        # Header
        hdr = tk.Frame(parent, bg=PANEL_BG, pady=12)
        hdr.pack(fill="x", padx=12)
        tk.Label(hdr, text=APP_NAME.upper(), font=("Consolas", 13, "bold"),
                 fg=ACCENT, bg=PANEL_BG).pack(side="left")
        tk.Label(hdr, text=APP_VERSION, font=("Consolas", 9),
                 fg=MUTED, bg=PANEL_BG).pack(side="left", padx=(4, 0), pady=(4, 0))

        sep = tk.Frame(parent, bg="#333", height=1)
        sep.pack(fill="x")

        # Buttons
        btn_frame = tk.Frame(parent, bg=PANEL_BG, pady=8)
        btn_frame.pack(fill="x", padx=8)
        self._btn(btn_frame, "+ Lägg till filer", self._add_files).pack(fill="x", pady=2)
        self._btn(btn_frame, "+ Lägg till mapp", self._add_folder).pack(fill="x", pady=2)

        sep2 = tk.Frame(parent, bg="#333", height=1)
        sep2.pack(fill="x")

        # File list
        list_container = tk.Frame(parent, bg=PANEL_BG)
        list_container.pack(fill="both", expand=True)

        scrollbar = tk.Scrollbar(list_container, bg=PANEL_BG, troughcolor=BG,
                                  orient="vertical")
        scrollbar.pack(side="right", fill="y")

        self.file_canvas = tk.Canvas(list_container, bg=PANEL_BG,
                                      yscrollcommand=scrollbar.set,
                                      highlightthickness=0, bd=0)
        self.file_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.file_canvas.yview)

        self.file_list_frame = tk.Frame(self.file_canvas, bg=PANEL_BG)
        self.file_canvas_window = self.file_canvas.create_window(
            (0, 0), window=self.file_list_frame, anchor="nw"
        )
        self.file_list_frame.bind("<Configure>", lambda e: self.file_canvas.configure(
            scrollregion=self.file_canvas.bbox("all")
        ))
        self.file_canvas.bind("<Configure>", lambda e: self.file_canvas.itemconfig(
            self.file_canvas_window, width=e.width
        ))

    def _build_center(self, parent):
        # Video frame
        self.video_frame = tk.Frame(parent, bg="black")
        self.video_frame.pack(fill="both", expand=True)

        # Timecode overlay
        self.timecode_label = tk.Label(
            self.video_frame, text="00:00:00",
            font=("Consolas", 16, "bold"), fg=ACCENT, bg="black"
        )
        self.timecode_label.place(relx=1.0, rely=0.0, x=-10, y=8, anchor="ne")

        # No-video placeholder
        self.placeholder = tk.Label(
            self.video_frame,
            text="Välj en videofil för att börja",
            font=("Segoe UI", 13), fg=MUTED, bg="black"
        )
        self.placeholder.place(relx=0.5, rely=0.5, anchor="center")

        # Error label (vlc/ffmpeg missing)
        if vlc is None:
            tk.Label(
                self.video_frame,
                text="⚠  VLC inte installerat\npip install python-vlc\noch installera VLC från videolan.org",
                font=("Segoe UI", 11), fg=DANGER, bg="black", justify="center"
            ).place(relx=0.5, rely=0.5, anchor="center")

        # Timeline canvas
        tl_frame = tk.Frame(parent, bg=BG, height=TIMELINE_H)
        tl_frame.pack(fill="x", padx=0, pady=0)
        tl_frame.pack_propagate(False)

        self.timeline = tk.Canvas(tl_frame, bg="#111", height=TIMELINE_H,
                                   highlightthickness=0, cursor="crosshair")
        self.timeline.pack(fill="both", expand=True)
        self.timeline.bind("<Button-1>", self._timeline_click)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)

        # Transport bar
        transport = tk.Frame(parent, bg=PANEL_BG, height=44)
        transport.pack(fill="x")
        transport.pack_propagate(False)

        inner = tk.Frame(transport, bg=PANEL_BG)
        inner.pack(expand=True)

        self._btn(inner, "⏮", lambda: self._go_to_in(), width=3).pack(side="left", padx=2)
        self._btn(inner, "⏪", lambda: self._seek(-5), width=3).pack(side="left", padx=2)
        self.play_btn = self._btn(inner, "▶", self._toggle_play, width=3)
        self.play_btn.pack(side="left", padx=2)
        self._btn(inner, "⏩", lambda: self._seek(5), width=3).pack(side="left", padx=2)
        self._btn(inner, "⏭", lambda: self._go_to_out(), width=3).pack(side="left", padx=2)

        # Speed buttons
        tk.Frame(inner, bg=PANEL_BG, width=16).pack(side="left")
        self.speed_btns = {}
        for spd in self._speed_options:
            label = f"{spd:g}x"
            btn = tk.Button(
                inner, text=label, font=("Segoe UI", 9),
                bg=PANEL_BG, fg=TEXT, bd=0, padx=6, pady=4,
                activebackground="#333", activeforeground=ACCENT,
                relief="flat", cursor="hand2",
                command=lambda s=spd: self._set_speed(s)
            )
            btn.pack(side="left", padx=1)
            self.speed_btns[spd] = btn
        self._highlight_speed_btn()

        # Status bar
        dnd_hint = "  |  Dra filer till videon" if _DND_AVAILABLE else "  |  pip install tkinterdnd2 för drag-och-släpp"
        self.status_bar = tk.Label(
            parent,
            text=f"Space=Play/Pause  I=In  O=Out  M/Enter=Spara  E=Exportera  ?=Hjälp{dnd_hint}",
            font=("Consolas", 8), fg=MUTED, bg="#111", anchor="w", padx=8, pady=3
        )
        self.status_bar.pack(fill="x", side="bottom")

        # Toast
        self.toast = tk.Label(
            self.video_frame, text="", font=("Segoe UI", 11, "bold"),
            fg="black", bg=ACCENT, padx=12, pady=6
        )

    def _build_right(self, parent):
        hdr = tk.Frame(parent, bg=PANEL_BG, pady=12)
        hdr.pack(fill="x", padx=12)
        tk.Label(hdr, text="SEGMENTS", font=("Consolas", 11, "bold"),
                 fg=ACCENT, bg=PANEL_BG).pack(side="left")

        sep = tk.Frame(parent, bg="#333", height=1)
        sep.pack(fill="x")

        # Segment list
        seg_outer = tk.Frame(parent, bg=PANEL_BG)
        seg_outer.pack(fill="both", expand=True)

        seg_scroll = tk.Scrollbar(seg_outer, bg=PANEL_BG, troughcolor=BG)
        seg_scroll.pack(side="right", fill="y")

        self.seg_canvas = tk.Canvas(seg_outer, bg=PANEL_BG,
                                     yscrollcommand=seg_scroll.set,
                                     highlightthickness=0, bd=0)
        self.seg_canvas.pack(side="left", fill="both", expand=True)
        seg_scroll.config(command=self.seg_canvas.yview)

        self.seg_list_frame = tk.Frame(self.seg_canvas, bg=PANEL_BG)
        self.seg_canvas_window = self.seg_canvas.create_window(
            (0, 0), window=self.seg_list_frame, anchor="nw"
        )
        self.seg_list_frame.bind("<Configure>", lambda e: self.seg_canvas.configure(
            scrollregion=self.seg_canvas.bbox("all")
        ))
        self.seg_canvas.bind("<Configure>", lambda e: self.seg_canvas.itemconfig(
            self.seg_canvas_window, width=e.width
        ))

        sep2 = tk.Frame(parent, bg="#333", height=1)
        sep2.pack(fill="x")

        # Footer
        footer = tk.Frame(parent, bg=PANEL_BG, pady=8)
        footer.pack(fill="x", padx=8)

        self.total_dur_label = tk.Label(
            footer, text="Total: 0:00:00", font=("Consolas", 9),
            fg=MUTED, bg=PANEL_BG, anchor="w"
        )
        self.total_dur_label.pack(fill="x", pady=(0, 6))

        self.export_btn = self._btn(
            footer, "⬇  Exportera segment", self._export_all,
            accent=True
        )
        self.export_btn.pack(fill="x")

    # ── Widget helper ────────────────────────────────────────────────────────
    def _btn(self, parent, text, cmd, width=None, accent=False):
        kwargs = dict(
            text=text, command=cmd,
            font=("Segoe UI", 9),
            bd=0, relief="flat", cursor="hand2",
            padx=8, pady=5,
            activebackground=ACCENT if accent else "#333",
            activeforeground="black" if accent else ACCENT,
        )
        if accent:
            kwargs.update(bg=ACCENT, fg="black", font=("Segoe UI", 10, "bold"))
        else:
            kwargs.update(bg="#333", fg=TEXT)
        if width:
            kwargs["width"] = width
        return tk.Button(parent, **kwargs)

    # ── File management ──────────────────────────────────────────────────────
    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Välj videofiler",
            filetypes=[("Videofiler", "*.mp4 *.mov *.avi *.mkv *.mts *.m4v"), ("Alla", "*.*")]
        )
        for p in paths:
            self._add_file(p)

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Välj mapp")
        if not folder:
            return
        for f in sorted(Path(folder).iterdir()):
            if f.suffix.lower() in SUPPORTED_EXTENSIONS:
                self._add_file(str(f))

    def _add_file(self, path):
        if any(f["path"] == path for f in self.files):
            return
        dur = get_video_duration(path)
        self.files.append({"path": path, "duration": dur})
        self._refresh_file_list()
        if self.active_index == -1:
            self._load_file(len(self.files) - 1)

    def _refresh_file_list(self):
        for w in self.file_list_frame.winfo_children():
            w.destroy()
        for i, f in enumerate(self.files):
            self._make_file_row(i, f)

    def _make_file_row(self, index, f):
        name = Path(f["path"]).name
        dur_str = fmt_time(f["duration"])
        is_active = index == self.active_index

        row = tk.Frame(self.file_list_frame,
                       bg="#2e2e2e" if is_active else PANEL_BG,
                       cursor="hand2")
        row.pack(fill="x", pady=0)

        if is_active:
            bar = tk.Frame(row, bg=ACCENT, width=3)
            bar.pack(side="left", fill="y")

        content = tk.Frame(row, bg=row["bg"], padx=8, pady=6)
        content.pack(side="left", fill="x", expand=True)

        short_name = name[:28] + "…" if len(name) > 28 else name
        tk.Label(content, text=short_name, font=("Segoe UI", 9),
                 fg=ACCENT if is_active else TEXT,
                 bg=row["bg"], anchor="w").pack(fill="x")
        tk.Label(content, text=dur_str, font=("Consolas", 8),
                 fg=MUTED, bg=row["bg"], anchor="w").pack(fill="x")

        row.bind("<Button-1>", lambda e, i=index: self._load_file(i))
        content.bind("<Button-1>", lambda e, i=index: self._load_file(i))
        for child in content.winfo_children():
            child.bind("<Button-1>", lambda e, i=index: self._load_file(i))

        menu = tk.Menu(self, tearoff=0, bg=PANEL_BG, fg=TEXT,
                       activebackground="#333", activeforeground=ACCENT)
        menu.add_command(label="Ta bort från lista",
                         command=lambda i=index: self._remove_file(i))
        row.bind("<Button-3>", lambda e, m=menu: m.post(e.x_root, e.y_root))

    def _remove_file(self, index):
        self.files.pop(index)
        if self.active_index == index:
            self.active_index = -1
            if self._player:
                self._player.stop()
            self.duration = 0.0
            self.in_point = None
            self.out_point = None
            self._draw_timeline()
            if self.files:
                self._load_file(min(index, len(self.files) - 1))
        elif self.active_index > index:
            self.active_index -= 1
        self._refresh_file_list()

    def _load_file(self, index):
        if index < 0 or index >= len(self.files):
            return
        self.active_index = index
        f = self.files[index]
        self.in_point = None
        self.out_point = None
        self.duration = f["duration"]
        self._refresh_file_list()

        if self._player is None:
            return

        media = self._vlc_instance.media_new(f["path"])
        self._player.set_media(media)

        self.update_idletasks()
        hwnd = self.video_frame.winfo_id()
        self._player.set_hwnd(hwnd)

        self._player.play()
        time.sleep(0.15)
        self._player.pause()
        self.is_playing = False
        self.play_btn.config(text="▶")
        self.placeholder.place_forget()

    # ── Playback controls ────────────────────────────────────────────────────
    def _toggle_play(self):
        if self._player is None or self.active_index == -1:
            return
        if self.is_playing:
            self._player.pause()
            self.is_playing = False
            self.play_btn.config(text="▶")
        else:
            self._player.play()
            self.is_playing = True
            self.play_btn.config(text="⏸")

    def _seek(self, delta):
        if self._player is None or self.duration <= 0:
            return
        pos = self._get_position() + delta
        pos = max(0, min(self.duration, pos))
        self._player.set_time(int(pos * 1000))

    def _get_position(self):
        if self._player is None:
            return 0
        ms = self._player.get_time()
        return ms / 1000.0 if ms >= 0 else 0

    def _go_to_in(self):
        if self.in_point is not None:
            self._player.set_time(int(self.in_point * 1000))

    def _go_to_out(self):
        if self.out_point is not None:
            self._player.set_time(int(self.out_point * 1000))

    def _set_speed(self, speed):
        self.speed = speed
        if self._player:
            self._player.set_rate(speed)
        self._highlight_speed_btn()

    def _highlight_speed_btn(self):
        for spd, btn in self.speed_btns.items():
            if spd == self.speed:
                btn.config(fg=ACCENT, font=("Segoe UI", 9, "bold"))
            else:
                btn.config(fg=TEXT, font=("Segoe UI", 9))

    # ── In/Out markers ───────────────────────────────────────────────────────
    def _set_in(self):
        if self._player is None or self.duration <= 0:
            return
        self.in_point = self._get_position()
        if self.out_point is not None and self.out_point <= self.in_point:
            self.out_point = None
        self._draw_timeline()

    def _set_out(self):
        if self._player is None or self.duration <= 0:
            return
        self.out_point = self._get_position()
        if self.in_point is not None and self.in_point >= self.out_point:
            self.in_point = None
        self._draw_timeline()

    # ── Segment management ───────────────────────────────────────────────────
    def _save_segment(self):
        if self.in_point is None or self.out_point is None:
            return
        if self.out_point <= self.in_point + 0.5:
            self._show_toast("⚠ Segmentet är för kort")
            return
        if self.active_index == -1:
            return

        seg_id = str(uuid.uuid4())
        f = self.files[self.active_index]

        seg = {
            "id": seg_id,
            "source": f["path"],
            "in_point": self.in_point,
            "out_point": self.out_point,
        }
        self.segments.append(seg)

        # Thumbnail in background
        in_pt = self.in_point
        src = f["path"]
        def gen_thumb():
            img = extract_thumbnail(src, in_pt)
            if img and ImageTk:
                photo = ImageTk.PhotoImage(img)
                self.thumb_cache[seg_id] = photo
            self.after(0, self._refresh_segments)

        threading.Thread(target=gen_thumb, daemon=True).start()

        self.in_point = None
        self.out_point = None
        self._draw_timeline()
        self._refresh_segments()
        self._show_toast("✓ Segment sparat")

    def _remove_segment(self, seg_id):
        self.segments = [s for s in self.segments if s["id"] != seg_id]
        self.thumb_cache.pop(seg_id, None)
        self._refresh_segments()

    def _refresh_segments(self):
        for w in self.seg_list_frame.winfo_children():
            w.destroy()

        total = 0.0
        for seg in self.segments:
            dur = seg["out_point"] - seg["in_point"]
            total += dur
            self._make_segment_card(seg, dur)

        self.total_dur_label.config(text=f"Total: {fmt_time(total)}")

    def _make_segment_card(self, seg, dur):
        card = tk.Frame(self.seg_list_frame, bg="#2a2a2a")
        card.pack(fill="x", padx=6, pady=3)

        # ── Top row: thumbnail + info ──────────────────────────────────────
        top = tk.Frame(card, bg="#2a2a2a")
        top.pack(fill="x")

        # Thumbnail
        thumb_frame = tk.Frame(top, bg="black", width=THUMB_W, height=THUMB_H)
        thumb_frame.pack(side="left", padx=(6, 0), pady=(6, 2))
        thumb_frame.pack_propagate(False)

        if seg["id"] in self.thumb_cache:
            tk.Label(thumb_frame, image=self.thumb_cache[seg["id"]], bg="black").pack()
        else:
            tk.Label(thumb_frame, text="⏳", fg=MUTED, bg="black",
                     font=("Segoe UI", 16)).pack(expand=True)

        # Info to the right of thumbnail
        info = tk.Frame(top, bg="#2a2a2a", padx=6)
        info.pack(side="left", fill="both", expand=True, pady=(6, 2))

        src_name = Path(seg["source"]).name[:20]
        tk.Label(info, text=src_name, font=("Segoe UI", 8),
                 fg=MUTED, bg="#2a2a2a", anchor="w").pack(fill="x")
        tk.Label(info, text=f"{fmt_time(seg['in_point'])} →",
                 font=("Consolas", 9, "bold"), fg=TEXT, bg="#2a2a2a", anchor="w").pack(fill="x")
        tk.Label(info, text=fmt_time(seg['out_point']),
                 font=("Consolas", 9, "bold"), fg=TEXT, bg="#2a2a2a", anchor="w").pack(fill="x")
        tk.Label(info, text=fmt_time(dur), font=("Consolas", 8),
                 fg=ACCENT, bg="#2a2a2a", anchor="w").pack(fill="x")

        # ── Bottom row: full-width delete button ───────────────────────────
        tk.Button(
            card, text="✕  Ta bort segment",
            font=("Segoe UI", 9), bd=0, relief="flat", cursor="hand2",
            bg="#3a1a1a", fg=DANGER,
            activebackground=DANGER, activeforeground="white",
            pady=4,
            command=lambda sid=seg["id"]: self._remove_segment(sid)
        ).pack(fill="x", padx=6, pady=(0, 6))

    # ── Timeline drawing ──────────────────────────────────────────────────────
    def _draw_timeline(self):
        c = self.timeline
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 1:
            return
        c.delete("all")

        # Background
        c.create_rectangle(0, 0, w, h, fill="#111", outline="")

        if self.duration <= 0:
            return

        def t_to_x(t):
            return int(t / self.duration * w)

        # In/Out region
        if self.in_point is not None and self.out_point is not None:
            x1 = t_to_x(self.in_point)
            x2 = t_to_x(self.out_point)
            c.create_rectangle(x1, 0, x2, h, fill=ACCENT + "55", outline="")

        # In marker
        if self.in_point is not None:
            x = t_to_x(self.in_point)
            c.create_line(x, 0, x, h, fill=ACCENT, width=2)
            c.create_polygon(x - 6, h, x + 6, h, x, h - 10, fill=ACCENT)
            c.create_text(x + 4, 4, text=fmt_time(self.in_point),
                          font=("Consolas", 7), fill=ACCENT, anchor="nw")

        # Out marker
        if self.out_point is not None:
            x = t_to_x(self.out_point)
            c.create_line(x, 0, x, h, fill=ACCENT, width=2)
            c.create_polygon(x - 6, h, x + 6, h, x, h - 10, fill=ACCENT)
            c.create_text(x - 4, 4, text=fmt_time(self.out_point),
                          font=("Consolas", 7), fill=ACCENT, anchor="ne")

        # Playhead
        pos = self._get_position()
        px = t_to_x(pos)
        c.create_line(px, 0, px, h, fill="white", width=2)

        # Tick marks
        step = max(1, int(self.duration / 20))
        for tick in range(0, int(self.duration), step):
            tx = t_to_x(tick)
            c.create_line(tx, h - 6, tx, h, fill="#555", width=1)

    def _timeline_click(self, event):
        if self.duration <= 0:
            return
        w = self.timeline.winfo_width()
        pos = (event.x / w) * self.duration
        pos = max(0, min(self.duration, pos))

        # Check if near in/out markers for drag
        def near(pt, tolerance=8):
            if pt is None:
                return False
            x = (pt / self.duration) * w
            return abs(event.x - x) < tolerance

        if near(self.in_point):
            self._drag_in = True
        elif near(self.out_point):
            self._drag_out = True
        else:
            if self._player:
                self._player.set_time(int(pos * 1000))

    def _timeline_drag(self, event):
        if self.duration <= 0:
            return
        w = self.timeline.winfo_width()
        pos = max(0, min(self.duration, (event.x / w) * self.duration))
        if self._drag_in:
            self.in_point = pos
        elif self._drag_out:
            self.out_point = pos
        else:
            if self._player:
                self._player.set_time(int(pos * 1000))
        self._draw_timeline()

    def _timeline_release(self, event):
        self._drag_in = False
        self._drag_out = False

    # ── Tick loop ─────────────────────────────────────────────────────────────
    def _tick(self):
        if self._player and self.duration > 0:
            pos = self._get_position()
            self.timecode_label.config(text=fmt_time(pos))
            self._draw_timeline()

            state = self._player.get_state()
            if state == vlc.State.Ended if vlc else False:
                self.is_playing = False
                self.play_btn.config(text="▶")

        self.after(100, self._tick)

    # ── Toast ─────────────────────────────────────────────────────────────────
    def _show_toast(self, text):
        if self._toast_after:
            self.after_cancel(self._toast_after)
        self.toast.config(text=text)
        self.toast.place(relx=0.5, rely=0.9, anchor="center")
        self._toast_after = self.after(1500, self.toast.place_forget)

    # ── Export ────────────────────────────────────────────────────────────────
    def _export_all(self):
        if not self.segments:
            messagebox.showinfo("Export", "Inga segment att exportera.", parent=self)
            return
        if not FFMPEG:
            messagebox.showerror(
                "FFmpeg saknas",
                "FFmpeg hittades inte.\n\nLadda ned från: https://ffmpeg.org/download.html\n"
                "och lägg till i PATH eller i samma mapp som highlight_grab.py",
                parent=self
            )
            return

        win = tk.Toplevel(self)
        win.title("Exporterar…")
        win.geometry("420x180")
        win.configure(bg=PANEL_BG)
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text="Exporterar segment", font=("Segoe UI", 12, "bold"),
                 fg=TEXT, bg=PANEL_BG).pack(pady=(16, 4))

        status_lbl = tk.Label(win, text="", font=("Segoe UI", 9),
                               fg=MUTED, bg=PANEL_BG)
        status_lbl.pack(pady=2)

        file_lbl = tk.Label(win, text="", font=("Consolas", 8),
                             fg=MUTED, bg=PANEL_BG, wraplength=380)
        file_lbl.pack(pady=2)

        pb = ttk.Progressbar(win, length=380, mode="determinate")
        pb.pack(pady=8)

        cancel_flag = [False]
        tk.Button(win, text="Avbryt", font=("Segoe UI", 9),
                  bg="#333", fg=TEXT, bd=0, relief="flat", padx=10,
                  command=lambda: cancel_flag.__setitem__(0, True)).pack()

        total = len(self.segments)
        errors = []
        output_dir = None

        def do_export():
            nonlocal output_dir
            for i, seg in enumerate(self.segments):
                if cancel_flag[0]:
                    break
                src = seg["source"]
                if not Path(src).exists():
                    errors.append(f"Fil saknas: {Path(src).name}")
                    continue

                out_folder = Path(src).parent / "highlight_grab_export"
                out_folder.mkdir(exist_ok=True)
                output_dir = str(out_folder)

                base = Path(src).stem
                in_s = seg["in_point"]
                out_s = seg["out_point"]
                fname = f"{base}_{i+1}_{int(in_s)}s-{int(out_s)}s.mp4"
                out_path = out_folder / fname

                win.after(0, lambda i=i, fname=fname: (
                    status_lbl.config(text=f"Segment {i+1} av {total}"),
                    file_lbl.config(text=fname),
                    pb.config(value=int(i / total * 100))
                ))

                try:
                    subprocess.run([
                        FFMPEG, "-y",
                        "-i", src,
                        "-ss", str(in_s),
                        "-to", str(out_s),
                        "-c", "copy",
                        "-avoid_negative_ts", "1",
                        str(out_path)
                    ], check=True, capture_output=True, timeout=300)
                except subprocess.CalledProcessError as e:
                    errors.append(f"Fel vid export av segment {i+1}: {e.stderr.decode()[:200]}")
                except subprocess.TimeoutExpired:
                    errors.append(f"Timeout vid export av segment {i+1}")

            win.after(0, on_done)

        def on_done():
            pb.config(value=100)
            win.destroy()
            if errors:
                messagebox.showwarning(
                    "Export klar med fel",
                    "Export klar med följande fel:\n\n" + "\n".join(errors),
                    parent=self
                )
            else:
                ans = messagebox.askyesno(
                    "Export klar",
                    f"Alla {total} segment exporterades.\n\nÖppna exportmappen?",
                    parent=self
                )
                if ans and output_dir:
                    os.startfile(output_dir)

        threading.Thread(target=do_export, daemon=True).start()

    # ── Keyboard bindings ─────────────────────────────────────────────────────
    def _bind_keys(self):
        self.bind("<space>", lambda e: self._toggle_play())
        self.bind("i", lambda e: self._set_in())
        self.bind("I", lambda e: self._set_in())
        self.bind("o", lambda e: self._set_out())
        self.bind("O", lambda e: self._set_out())
        self.bind("m", lambda e: self._save_segment())
        self.bind("M", lambda e: self._save_segment())
        self.bind("<Return>", lambda e: self._save_segment())
        self.bind("<Right>", lambda e: self._seek(5))
        self.bind("<Left>", lambda e: self._seek(-5))
        self.bind("<Shift-Right>", lambda e: self._seek(1))
        self.bind("<Shift-Left>", lambda e: self._seek(-1))
        self.bind("n", lambda e: self._load_file(self.active_index + 1))
        self.bind("N", lambda e: self._load_file(self.active_index + 1))
        self.bind("p", lambda e: self._load_file(self.active_index - 1))
        self.bind("P", lambda e: self._load_file(self.active_index - 1))
        self.bind("<Delete>", lambda e: self._delete_last_segment())
        self.bind("e", lambda e: self._export_all())
        self.bind("E", lambda e: self._export_all())
        self.bind("?", lambda e: self._show_help())
        self.bind("j", lambda e: self._change_speed(-1))
        self.bind("J", lambda e: self._change_speed(-1))
        self.bind("l", lambda e: self._change_speed(1))
        self.bind("L", lambda e: self._change_speed(1))

    def _delete_last_segment(self):
        if self.segments:
            self._remove_segment(self.segments[-1]["id"])

    def _change_speed(self, direction):
        opts = self._speed_options
        try:
            idx = opts.index(self.speed)
        except ValueError:
            idx = 1
        new_idx = max(0, min(len(opts) - 1, idx + direction))
        self._set_speed(opts[new_idx])

    def _show_help(self):
        win = tk.Toplevel(self)
        win.title("Tangentbordsgenvägar")
        win.geometry("440x400")
        win.configure(bg=PANEL_BG)
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text="Tangentbordsgenvägar", font=("Segoe UI", 13, "bold"),
                 fg=ACCENT, bg=PANEL_BG).pack(pady=(16, 8))

        shortcuts = [
            ("Space", "Spela / Pausa"),
            ("I", "Sätt in-punkt"),
            ("O", "Sätt ut-punkt"),
            ("M / Enter", "Spara segment"),
            ("E", "Exportera alla segment"),
            ("→", "+5 sekunder"),
            ("←", "−5 sekunder"),
            ("Shift+→", "+1 sekund"),
            ("Shift+←", "−1 sekund"),
            ("N", "Nästa fil"),
            ("P", "Föregående fil"),
            ("Delete", "Ta bort senaste segment"),
            ("L", "Öka hastighet"),
            ("J", "Minska hastighet"),
            ("?", "Visa den här hjälpen"),
        ]

        frame = tk.Frame(win, bg=PANEL_BG)
        frame.pack(fill="both", expand=True, padx=24, pady=8)

        for key, desc in shortcuts:
            row = tk.Frame(frame, bg=PANEL_BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=key, font=("Consolas", 10, "bold"),
                     fg=ACCENT, bg=PANEL_BG, width=14, anchor="w").pack(side="left")
            tk.Label(row, text=desc, font=("Segoe UI", 10),
                     fg=TEXT, bg=PANEL_BG, anchor="w").pack(side="left")

        tk.Button(win, text="Stäng", font=("Segoe UI", 10),
                  bg=ACCENT, fg="black", bd=0, relief="flat", padx=16, pady=6,
                  cursor="hand2", command=win.destroy).pack(pady=12)

    def _on_close(self):
        if self._player:
            self._player.stop()
        self.destroy()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if vlc is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "VLC saknas",
            "python-vlc är inte installerat.\n\n"
            "Kör: pip install python-vlc\n"
            "Och installera VLC från https://www.videolan.org/vlc/\n\n"
            "Stäng och försök igen."
        )
        sys.exit(1)

    app = HighlightGrab()
    app.mainloop()
