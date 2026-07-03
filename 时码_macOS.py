#!/usr/bin/env python3
"""
Digital Clock — 数字时钟
格式: HH:MM:SS:ms|50f|25f
支持三种毫秒制式: 毫秒(ms) / 50帧(50f) / 25帧(25f)
窗口可自由缩放，字体自动适配，内容始终居中
支持 NTP 网络校时（联网时自动校正，每10分钟同步一次）
支持自定义颜色（按 C 打开颜色设置面板）
"""

import tkinter as tk
from tkinter import colorchooser
import time
import socket
import struct
import threading
import json
import os
from datetime import datetime, timezone, timedelta

# 高精度计时器
_get_tick = time.monotonic if hasattr(time, 'monotonic') else time.perf_counter


NTP_SYNC_INTERVAL = 600
NTP_TIMEOUT = 5
NTP_SERVERS = [
    "time.apple.com",
    "cn.pool.ntp.org",
    "ntp.aliyun.com",
    "pool.ntp.org",
]


def get_ntp_time():
    """通过 NTP 协议获取网络 UTC 时间（先用 socket，失败则 fallback 到 HTTP）"""
    for server in NTP_SERVERS:
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.settimeout(NTP_TIMEOUT)
            pkt = b'\x1b' + 47 * b'\x00'
            client.sendto(pkt, (server, 123))
            data, _ = client.recvfrom(1024)
            client.close()
            t_ntp = struct.unpack_from('!I', data, 40)[0]
            return t_ntp - 2208988800
        except Exception:
            continue
    return _get_http_time()

def _get_http_time():
    """通过 HTTP 头获取网络时间"""
    import subprocess
    urls = ['https://www.baidu.com', 'https://www.apple.com', 'https://www.google.com']
    for url in urls:
        try:
            result = subprocess.run(
                ['curl', '-sI', url],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split('\n'):
                if line.lower().startswith('date:'):
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(line[5:].strip())
                    return dt.timestamp()
        except Exception:
            continue
    return None


class ClockApp:
    CONFIG_FILE = os.path.expanduser("~/.digital_clock_config.json")
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Digital Clock")
        self.root.resizable(True, True)
        self.root.attributes("-topmost", True)
        self.root.bind("<Button-1>", self._on_click_raise)

        self._ntp_synced = False
        self._tick_anchor = None

        self._base_font_size = 48
        self._base_w = 560
        self._base_h = 160
        self._resize_after_id = None

        self.mode = 0
        self.mode_labels = ["ms", "50f", "25f"]
        
        self._default_colors = {
            'bg': '#0d0d0d',
            'fg': '#00ffcc',
            'btn_bg': '#1a1f26',
            'btn_hl': '#ff4622',
            'btn_txt': '#5a7a8a',
            'btn_txt_hl': '#0d0d0d',
        }
        
        self.colors = self._load_config()
        self._color_picker = None

        self._build_ui()
        
        self._offset = None
        for widget in (self.root, self.time_label):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)

        self.root.bind("<Configure>", self._on_resize)

        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("1", lambda e: self._switch_mode(0))
        self.root.bind("2", lambda e: self._switch_mode(1))
        self.root.bind("3", lambda e: self._switch_mode(2))
        self.root.bind("c", lambda e: self._open_color_picker())
        self.root.bind("C", lambda e: self._open_color_picker())

        self._sync_network_time_blocking()
        self._tick()
        self.root.mainloop()

    def _build_ui(self):
        self.root.configure(bg=self.colors['bg'])
        
        main = tk.Frame(self.root, bg=self.colors['bg'])
        main.pack(fill="both", expand=True, padx=20, pady=(16, 10))
        self._main_frame = main

        self.time_var = tk.StringVar(value="00:00:00:000")
        self.time_label = tk.Label(
            main,
            textvariable=self.time_var,
            font=("Courier New", self._base_font_size, "bold"),
            fg=self.colors['fg'],
            bg=self.colors['bg'],
        )
        self.time_label.pack(expand=True)

        btn_frame = tk.Frame(main, bg=self.colors['bg'])
        btn_frame.pack(pady=(12, 0))
        self._btn_frame = btn_frame

        self.btn_widgets = []
        for i, lbl in enumerate(self.mode_labels):
            b = tk.Button(
                btn_frame,
                text=lbl,
                font=("Courier New", 10, "bold"),
                width=8,
                command=lambda idx=i: self._switch_mode(idx),
            )
            b.pack(side="left", padx=6)
            self.btn_widgets.append(b)

        self._update_buttons()

        settings_btn = tk.Button(
            main,
            text="设置",
            font=("Courier New", 9),
            width=8,
            command=self._open_color_picker,
            bg=self.colors['btn_bg'],
            fg=self.colors['btn_txt']
        )
        settings_btn.pack(anchor='n', padx=0, pady=(12, 0))
        self._settings_btn = settings_btn
        
        hint = tk.Label(main, text="ESC 关闭 | 1/2/3切换制式 | C 自定义颜色 | 拖拽移动 | 可缩放窗口",
                        font=("Courier New", 7), fg="#ff4622", bg=self.colors['bg'])
        hint.pack(anchor='n', pady=(10, 0))
        self._hint_label = hint

    def _update_colors(self):
        bg = self.colors['bg']
        fg = self.colors['fg']
        
        self.root.configure(bg=bg)
        self._main_frame.configure(bg=bg)
        self._btn_frame.configure(bg=bg)
        
        self.time_label.configure(bg=bg, fg=fg)
        self._hint_label.configure(bg=bg)
        self._settings_btn.configure(bg=self.colors['btn_bg'], fg=self.colors['btn_txt'])
        
        self._update_buttons()

    def _update_buttons(self):
        for i, b in enumerate(self.btn_widgets):
            if i == self.mode:
                b.config(bg=self.colors['btn_hl'], fg=self.colors['btn_txt_hl'],
                          activebackground=self.colors['btn_hl'], activeforeground=self.colors['btn_txt_hl'],
                          relief="sunken", bd=2)
            else:
                b.config(bg=self.colors['btn_bg'], fg=self.colors['btn_txt'],
                          activebackground="#2a3040", activeforeground=self.colors['btn_txt'],
                          relief="raised", bd=1)

    def _open_color_picker(self):
        if self._color_picker is not None and self._color_picker.winfo_exists():
            self._color_picker.lift()
            self._color_picker.focus_force()
            return
            
        picker = tk.Toplevel(self.root)
        picker.title("自定义颜色")
        picker.configure(bg='#2a2a2a')
        picker.resizable(False, False)
        picker.geometry("360x380")
        picker.attributes("-topmost", True)
        picker.focus_force()
        
        self._color_picker = picker
        
        color_items = [
            ('bg', '背景颜色'),
            ('fg', '数字颜色'),
            ('btn_bg', '按钮背景'),
            ('btn_hl', '按钮高亮'),
            ('btn_txt', '按钮文字'),
            ('btn_txt_hl', '按钮文字高亮'),
        ]
        
        self._color_vars = {}
        
        for key, label in color_items:
            frame = tk.Frame(picker, bg='#2a2a2a')
            frame.pack(fill='x', padx=15, pady=6)
            
            tk.Label(frame, text=label, bg='#2a2a2a', fg='white', width=10, anchor='w').pack(side='left')
            
            var = tk.StringVar(value=self.colors[key])
            self._color_vars[key] = var
            
            entry = tk.Entry(frame, textvariable=var, width=12, font=('Courier New', 10))
            entry.pack(side='left', padx=8)
            
            preview = tk.Label(frame, bg=self.colors[key], width=4, height=1, relief='solid', bd=1)
            preview.pack(side='left', padx=5)
            self._color_vars[key + '_preview'] = preview
            
            def make_callback(k, v, p):
                return lambda: self._pick_color(k, v, p)
            
            tk.Button(frame, text='选择', width=6, command=make_callback(key, var, preview)).pack(side='left', padx=5)
        
        btn_frame = tk.Frame(picker, bg='#2a2a2a')
        btn_frame.pack(fill='x', padx=15, pady=20)
        
        def _close_picker():
            picker.destroy()
            self._color_picker = None
            
        def _apply_and_close():
            self._apply_colors()
            _close_picker()
            
        tk.Button(btn_frame, text='应用', width=8, command=_apply_and_close, bg='#00ffcc', fg='black').pack(side='left', padx=5)
        tk.Button(btn_frame, text='重置', width=8, command=self._reset_colors, bg='#666666', fg='white').pack(side='left', padx=5)
        tk.Button(btn_frame, text='关闭', width=8, command=_close_picker, bg='#444444', fg='white').pack(side='right', padx=5)

    def _pick_color(self, key, var, preview):
        self.root.attributes("-topmost", False)
        if self._color_picker:
            self._color_picker.attributes("-topmost", False)
        
        try:
            color = colorchooser.askcolor(initialcolor=var.get(), title=f"选择{key}")[1]
            if color:
                var.set(color)
                preview.configure(bg=color)
        finally:
            self.root.attributes("-topmost", True)
            if self._color_picker and self._color_picker.winfo_exists():
                self._color_picker.attributes("-topmost", True)

    def _apply_colors(self):
        for key in ['bg', 'fg', 'btn_bg', 'btn_hl', 'btn_txt', 'btn_txt_hl']:
            self.colors[key] = self._color_vars[key].get()
        self._update_colors()
        self._save_config()

    def _reset_colors(self):
        self.colors.update(self._default_colors)
        for key in self._default_colors:
            self._color_vars[key].set(self._default_colors[key])
            self._color_vars[key + '_preview'].configure(bg=self._default_colors[key])
        self._update_colors()
        self._save_config()

    def _load_config(self):
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    colors = self._default_colors.copy()
                    colors.update(config.get('colors', {}))
                    return colors
        except Exception:
            pass
        return self._default_colors.copy()
    
    def _save_config(self):
        try:
            config = {'colors': self.colors}
            with open(self.CONFIG_FILE, 'w') as f:
                json.dump(config, f)
        except Exception:
            pass
    
    def _on_click_raise(self, event):
        self.root.lift()
        self.root.focus_force()

    def _sync_network_time_blocking(self, timeout=5):
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(get_ntp_time)
            try:
                net_t = future.result(timeout=timeout)
                if net_t is not None:
                    self._tick_anchor = (_get_tick(), net_t)
                    self._ntp_synced = True
                    local_now = time.time()
                    diff = net_t - local_now
                    self.root.title(f"Digital Clock [NTP OK {diff:+.3f}s]")
                    print(f"[NTP] 校时成功，网络时间领先本地 {diff:+.3f}s")
                else:
                    print("[NTP] 校时失败，使用本地时间")
                    self._tick_anchor = (_get_tick(), time.time())
                    self._ntp_synced = False
                    self.root.title("Digital Clock [本地时间]")
            except concurrent.futures.TimeoutError:
                print("[NTP] 校时超时，使用本地时间")
                self._tick_anchor = (_get_tick(), time.time())
                self._ntp_synced = False
                self.root.title("Digital Clock [本地时间]")
        self.root.after(NTP_SYNC_INTERVAL * 1000, self._schedule_next_sync)

    def _schedule_next_sync(self):
        def worker():
            net_t = get_ntp_time()
            if net_t is not None:
                self._tick_anchor = (_get_tick(), net_t)
                self._ntp_synced = True
                print(f"[NTP] 定期校时成功")
        threading.Thread(target=worker, daemon=True).start()
        self.root.after(NTP_SYNC_INTERVAL * 1000, self._schedule_next_sync)

    def _get_adjusted_time(self):
        if self._tick_anchor is None:
            return time.time()
        anchor_tick, anchor_time = self._tick_anchor
        elapsed = _get_tick() - anchor_tick
        return anchor_time + elapsed

    def _get_local_time_components(self, t):
        dt = datetime.fromtimestamp(t)
        ms = int((t - int(t)) * 1000)
        return dt.hour, dt.minute, dt.second, ms

    def _get_last_part(self, ms):
        if self.mode == 0:
            return f"{ms % 1000:03d}"
        elif self.mode == 1:
            return f"{(ms % 1000) // 20:03d}"
        else:
            return f"{(ms % 1000) // 40:03d}"

    def _tick(self):
        t = self._get_adjusted_time()
        h, m, s, ms = self._get_local_time_components(t)
        self.time_var.set(f"{h:02d}:{m:02d}:{s:02d}:{self._get_last_part(ms)}")
        self.root.after(8, self._tick)

    def _switch_mode(self, idx):
        if idx == self.mode:
            return
        self.mode = idx
        self._update_buttons()

    def _start_drag(self, e):
        self._offset = (e.x_root - self.root.winfo_x(),
                         e.y_root - self.root.winfo_y())

    def _do_drag(self, e):
        if self._offset:
            ox, oy = self._offset
            self.root.geometry(f"+{e.x_root - ox}+{e.y_root - oy}")

    def _on_resize(self, event):
        if event.widget != self.root:
            return
        if self._resize_after_id:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(
            80, lambda: self._apply_resize(event.width, event.height)
        )

    def _apply_resize(self, w, h):
        scale = min(w / self._base_w, h / self._base_h)
        new_size = max(12, int(self._base_font_size * scale))
        self.time_label.config(font=("Courier New", new_size, "bold"))


if __name__ == "__main__":
    ClockApp()
