"""Kivy UI shared by the desktop build and the Android APK."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from ..config import ClipConfig, DetectConfig, LiveConfig
from ..live import LiveClipper

BG = (0.031, 0.043, 0.071, 1)
CARD = (0.063, 0.082, 0.125, 1)
FIELD = (0.098, 0.118, 0.169, 1)
ACCENT = (0.427, 0.361, 1.0, 1)
HOT = (0.133, 0.827, 0.933, 1)
TEXT = (0.91, 0.93, 0.96, 1)
MUTED = (0.45, 0.51, 0.60, 1)
DANGER = (1.0, 0.35, 0.45, 1)


def _open_folder(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif not sys.platform.startswith("android"):
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def label(text: str, color=TEXT, size: float = 13, bold: bool = False, **kwargs) -> Label:
    widget = Label(text=text, color=color, font_size=dp(size), bold=bold,
                   halign="left", valign="middle", **kwargs)
    widget.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, w.height)))
    return widget


class Card(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*CARD)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(16)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size


class Pill(Label):
    def __init__(self, **kwargs):
        super().__init__(bold=True, font_size=dp(12), color=MUTED, **kwargs)
        with self.canvas.before:
            self._color = Color(*CARD)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size

    def set_state(self, live: bool) -> None:
        self.text = "● LIVE" if live else "○ IDLE"
        self.color = HOT if live else MUTED
        self._color.rgba = (0.04, 0.15, 0.19, 1) if live else FIELD


class NeonSlider(Slider):
    """Slider with a drawn knob instead of the default image cursor."""

    def __init__(self, **kwargs):
        super().__init__(cursor_size=(0, 0), **kwargs)
        with self.canvas.after:
            Color(*HOT)
            self._knob = Ellipse(size=(dp(15), dp(15)))
        self.bind(pos=self._sync, size=self._sync, value=self._sync)

    def _sync(self, *_):
        span = max(1e-6, self.max - self.min)
        ratio = (self.value - self.min) / span
        x = self.x + dp(8) + (self.width - dp(16)) * ratio
        self._knob.pos = (x - dp(7.5), self.center_y - dp(7.5))


class Waveform(Widget):
    """Rolling excitement trace fed by detection events."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.values: deque[float] = deque([0.0] * 120, maxlen=120)
        self.peak = False
        self.bind(pos=self._redraw, size=self._redraw)

    def push(self, value: float) -> None:
        self.values.append(max(0.0, min(1.0, value)))
        self._redraw()

    def mark_peak(self) -> None:
        self.peak = True
        self._redraw()

    def _redraw(self, *_):
        self.canvas.clear()
        with self.canvas:
            Color(*CARD)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(16)])
            Color(0.13, 0.16, 0.22, 1)
            Line(points=[self.x + dp(12), self.y + dp(12),
                         self.right - dp(12), self.y + dp(12)], width=1)
            if self.width < dp(40) or self.height < dp(40):
                return
            step = (self.width - dp(24)) / max(1, len(self.values) - 1)
            points: list[float] = []
            for i, value in enumerate(self.values):
                points += [
                    self.x + dp(12) + i * step,
                    self.y + dp(12) + value * (self.height - dp(34)),
                ]
            Color(*HOT)
            Line(points=points, width=dp(2), joint="round")
            if self.peak:
                Color(*ACCENT)
                Ellipse(pos=(self.right - dp(26), self.top - dp(26)), size=(dp(9), dp(9)))


class ClipRow(Card):
    def __init__(self, name: str, detail: str, path: Path, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(68),
                         padding=(dp(14), dp(8)), spacing=dp(10), **kwargs)
        text = BoxLayout(orientation="vertical", spacing=dp(2))
        text.add_widget(label(name, TEXT, 13, True, size_hint_y=0.55))
        text.add_widget(label(detail, MUTED, 11, size_hint_y=0.45))
        self.add_widget(text)
        open_btn = Button(text="Open", size_hint=(None, 1), width=dp(64),
                          background_normal="", background_color=ACCENT,
                          color=TEXT, font_size=dp(12), bold=True)
        open_btn.bind(on_release=lambda *_: _open_folder(path))
        self.add_widget(open_btn)


class LiveClipApp(App):
    title = "LiveClip"

    def build(self):
        Window.clearcolor = BG
        if sys.platform not in ("android", "ios"):
            Window.size = (440, 900)
        self.events: queue.Queue = queue.Queue()
        self.clipper: LiveClipper | None = None
        self._sensitivity = 1.6
        self._length = 12.0

        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

        header = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        wordmark = Label(text="[b]LiveClip[/b]", markup=True, color=TEXT,
                         font_size=dp(25), halign="left", valign="middle")
        wordmark.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, w.height)))
        header.add_widget(wordmark)
        self.pill = Pill(text="○ IDLE", size_hint=(None, 1), width=dp(100))
        header.add_widget(self.pill)
        root.add_widget(header)

        self.wave = Waveform(size_hint_y=None, height=dp(124))
        root.add_widget(self.wave)

        controls = Card(orientation="vertical", padding=dp(14), spacing=dp(10),
                        size_hint_y=None, height=dp(282))
        row = BoxLayout(spacing=dp(10), size_hint_y=None, height=dp(44))
        self.source = Spinner(
            text="Screen",
            values=("Screen", "Camera", "Stream URL", "Video file"),
            size_hint=(0.52, 1), background_normal="", background_color=FIELD,
            color=TEXT, font_size=dp(13),
        )
        self.source.bind(text=self._on_source)
        row.add_widget(self.source)
        self.start_btn = Button(text="Start clipping", size_hint=(0.48, 1),
                                background_normal="", background_color=ACCENT,
                                color=TEXT, bold=True, font_size=dp(13))
        self.start_btn.bind(on_release=self.toggle)
        row.add_widget(self.start_btn)
        controls.add_widget(row)

        self.path_input = TextInput(
            hint_text="rtmp://… or /path/to/video.mp4", multiline=False,
            size_hint_y=None, height=dp(42), background_color=FIELD,
            foreground_color=TEXT, hint_text_color=MUTED, cursor_color=HOT,
            padding=(dp(12), dp(12)), disabled=True,
        )
        controls.add_widget(self.path_input)

        controls.add_widget(self._slider_row("Sensitivity", 0.6, 3.0, self._sensitivity,
                                             self._set_sensitivity))
        controls.add_widget(self._slider_row("Clip length", 4.0, 30.0, self._length,
                                             self._set_length))

        switch_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(10))
        switch_row.add_widget(label("Vertical 9:16", TEXT, 13))
        self.vertical = Switch(active=True, size_hint_x=None, width=dp(70))
        switch_row.add_widget(self.vertical)
        controls.add_widget(switch_row)
        root.add_widget(controls)

        clips_header = BoxLayout(size_hint_y=None, height=dp(26), spacing=dp(8))
        clips_header.add_widget(label("Auto clips", TEXT, 14, True, size_hint_x=0.4))
        self.hint = label("Idle", MUTED, 11, size_hint_x=0.6)
        self.hint.halign = "right"
        clips_header.add_widget(self.hint)
        root.add_widget(clips_header)

        self.scroll = ScrollView(bar_width=dp(3))
        self.clip_list = BoxLayout(orientation="vertical", spacing=dp(8),
                                   size_hint_y=None, padding=(0, 0, 0, dp(8)))
        self.clip_list.bind(minimum_height=self.clip_list.setter("height"))
        self.scroll.add_widget(self.clip_list)
        root.add_widget(self.scroll)

        Clock.schedule_interval(self._drain, 0.15)
        return root

    def _slider_row(self, title: str, low: float, high: float, value: float, on_change):
        box = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(10))
        box.add_widget(label(title, TEXT, 13, size_hint_x=0.34))
        slider = NeonSlider(min=low, max=high, value=value, step=0.1)
        slider.bind(value=lambda _, v: on_change(v))
        box.add_widget(slider)
        value_label = label(f"{value:.1f}", MUTED, 12, size_hint_x=0.16)
        value_label.halign = "right"
        slider.bind(value=lambda _, v: setattr(value_label, "text", f"{v:.1f}"))
        box.add_widget(value_label)
        return box

    def _set_sensitivity(self, value: float) -> None:
        self._sensitivity = value

    def _set_length(self, value: float) -> None:
        self._length = value

    def _on_source(self, _, text: str) -> None:
        self.path_input.disabled = text in ("Screen", "Camera")

    def _source_value(self) -> str:
        kind = self.source.text
        if kind == "Screen":
            return "screen"
        if kind == "Camera":
            return "camera"
        return self.path_input.text.strip()

    def toggle(self, *_):
        if self.clipper is not None:
            self.stop_session()
        else:
            self.start_session()

    def start_session(self) -> None:
        source = self._source_value()
        if not source:
            self.hint.text = "Add a stream URL or file path first"
            return
        out_dir = Path(self.user_data_dir) / "clips"
        out_dir.mkdir(parents=True, exist_ok=True)
        clip_cfg = ClipConfig(vertical=self.vertical.active).with_length(self._length)
        try:
            self.clipper = LiveClipper(
                source, out_dir,
                detect=DetectConfig().with_sensitivity(self._sensitivity),
                clip=clip_cfg,
                live=LiveConfig(),
                on_event=self.events.put,
            )
            self.clipper.start()
        except Exception as exc:
            self.clipper = None
            self.hint.text = f"Could not start: {exc}"[:90]
            return
        self.start_btn.text = "Stop"
        self.start_btn.background_color = DANGER
        self.pill.set_state(True)
        self.hint.text = "Watching for hype…"

    def stop_session(self) -> None:
        clipper, self.clipper = self.clipper, None
        self.start_btn.text = "Start clipping"
        self.start_btn.background_color = ACCENT
        self.pill.set_state(False)
        self.hint.text = "Stopped"
        if clipper is not None:
            threading.Thread(target=clipper.stop, daemon=True).start()

    def _drain(self, _dt) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            kind = event.get("type")
            if kind == "clip":
                start = float(event["start"])
                length = float(event["end"]) - start
                self.clip_list.add_widget(
                    ClipRow(
                        Path(event["path"]).name,
                        f"{length:.1f}s · score {event['score']:.2f}",
                        Path(event["path"]).parent,
                    )
                )
                self.wave.mark_peak()
                self.hint.text = f"{len(self.clip_list.children)} clip(s) ready"
            elif kind == "level":
                self.wave.push(float(event["score"]))
            elif kind == "state":
                self.pill.set_state(event.get("state") == "live")
            elif kind == "error":
                self.hint.text = str(event.get("message", "error"))[:90]


def main() -> None:
    LiveClipApp().run()


if __name__ == "__main__":
    main()
