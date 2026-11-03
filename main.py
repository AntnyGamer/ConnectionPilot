"""ConnectionPilot: local desktop UI for a self-directed LinkedIn invitation campaign.

Warning: LinkedIn prohibits unauthorized automation. Cooldowns cannot ensure safety.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from automation import ProtectionScreen, StopRun, run_campaign
from google_login import open_google_signin
from logic import (DEFAULT_MATCH_TERMS, DEFAULT_SEARCHES, FILTER_FIELDS,
                   MIN_COOLDOWN_SECONDS, MAX_COOLDOWN_SECONDS, theoretical_daily_capacity,
                   valid_cooldown_seconds, safe_saved_int)
from storage import Store
from security import restrict_posix_data, safe_diagnostic

APP_DIR = Path.home() / "ConnectionPilot"
APP_DIR.mkdir(parents=True, exist_ok=True)
restrict_posix_data(APP_DIR)
STORE = Store(APP_DIR / "connectionpilot.sqlite3")
# New profile layouts made the old pre-click header check skip valid people.
# Retry ONLY those safe, confirmed-not-clicked cases, once per upgrade.
if STORE.setting("migration:layout_v8") != "1":
    STORE.requeue_layout_skips()
    STORE.set_setting("migration:layout_v8", "1")
# Migrate the original prototype's 8/day default; retain other custom caps.
if STORE.setting("ui:settings_version") != "2":
    if STORE.setting("ui:daily") == "8":
        STORE.set_setting("ui:daily", "500")
    STORE.set_setting("ui:settings_version", "2")
# Older builds stored integer minutes; migrate once without accelerating campaigns.
# New installations start at 90 seconds, and old saved intervals keep their duration.
if STORE.setting("ui:cooldown_unit") != "seconds":
    previous = STORE.setting("ui:cooldown")
    if previous is not None:
        try:
            STORE.set_setting("ui:cooldown", str(float(previous) * 60))
        except ValueError:
            pass
    STORE.set_setting("ui:cooldown_unit", "seconds")

APP_VERSION = "v1"  # Stable user-visible label; internal migrations retain their own keys.


def asset_path(name: str) -> Path:
    """Locate packaged branding or source-tree assets without relying on CWD."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / name


def set_window_branding(root):
    """Set a real Windows executable/taskbar icon and a Tk window icon."""
    png = asset_path("connectionpilot.png")
    if png.is_file():
        try:
            photo = tk.PhotoImage(master=root, file=str(png))
            root.iconphoto(True, photo)
            root._connectionpilot_icon = photo  # Tk needs a persistent reference.
        except tk.TclError:
            pass
    # Set the multi-resolution .ico LAST on Windows: iconphoto() replaces the
    # native taskbar/titlebar icon if called after iconbitmap().
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ConnectionPilot.App.v1")
        except (AttributeError, OSError):
            pass
        ico = asset_path("connectionpilot.ico")
        if ico.is_file():
            try:
                root.iconbitmap(default=str(ico))
            except tk.TclError:
                pass


COLORS = {
    "bg": "#f3f6fb", "panel": "#ffffff", "ink": "#15253a", "muted": "#52647b",
    "accent": "#1867bd", "alert": "#9b391b", "line": "#d6e0ec",
}

class App:
    def __init__(self, root):
        self.root = root
        set_window_branding(self.root)
        self.root.title(f"ConnectionPilot {APP_VERSION} — LinkedIn Campaign")
        self.root.geometry(f"830x{min(860, max(620, self.root.winfo_screenheight() - 100))}")
        self.root.minsize(760, 620)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker = None
        self.running = False
        self.closing = False

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["panel"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["ink"], font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=COLORS["panel"], foreground=COLORS["ink"], font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["ink"], font=("Segoe UI Semibold", 22))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8))
        style.configure("Primary.TButton", background=COLORS["accent"], foreground="#ffffff")
        style.map("Primary.TButton", background=[("active", "#0b4d96"), ("disabled", "#99b3d1")])
        style.configure("TLabelframe", background=COLORS["panel"], foreground=COLORS["ink"])
        style.configure("TLabelframe.Label", background=COLORS["panel"], foreground=COLORS["ink"], font=("Segoe UI Semibold", 10))
        self.root.configure(background=COLORS["bg"])

        # Scrolling keeps the controls reachable on 768p laptops and at large
        # Windows display scaling, instead of placing the log below the screen.
        canvas = tk.Canvas(root, bg=COLORS["bg"], bd=0, highlightthickness=0)
        outer_scroll = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=outer_scroll.set)
        outer_scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        outer = ttk.Frame(canvas, padding=20)
        outer_window = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(outer_window, width=event.width))
        header = ttk.Frame(outer)
        header.pack(fill="x")
        # This 48px PNG is rendered for its display size, not subsampled
        # from the full logo (which produced jagged edges and soft details).
        logo_file = asset_path("connectionpilot-header.png")
        if logo_file.is_file():
            try:
                self.header_logo = tk.PhotoImage(master=root, file=str(logo_file))
                ttk.Label(header, image=self.header_logo).pack(side="left", padx=(0, 12))
            except tk.TclError:
                pass
        ttk.Label(header, text=f"ConnectionPilot {APP_VERSION}", style="Title.TLabel").pack(side="left")
        ttk.Label(outer, text="Find relevant professionals and manage a paced invitation campaign.").pack(anchor="w", pady=(2, 10))

        warning = tk.Label(outer, text=("IMPORTANT: LinkedIn prohibits unauthorized automated invitations. "
                         "A cooldown does not prevent restrictions or suspension."),
                           fg=COLORS["alert"], bg="#fff2e9", font=("Segoe UI", 9),
                           justify="left", wraplength=690, padx=12, pady=9)
        warning.pack(fill="x", pady=(0, 12))

        panel = ttk.Frame(outer, style="Card.TFrame", padding=15)
        panel.pack(fill="both", expand=True)

        top = ttk.Frame(panel, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="Invitation goal", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.target = tk.IntVar(value=safe_saved_int(STORE.setting("ui:target", "200"), 200, 1, 5000))
        ttk.Spinbox(top, textvariable=self.target, from_=1, to=5000, width=8).grid(row=1, column=0, sticky="w", pady=(3, 10))
        ttk.Label(top, text="Daily invitation cap", style="Card.TLabel").grid(row=0, column=1, sticky="w", padx=(25, 0))
        self.daily = tk.IntVar(value=safe_saved_int(STORE.setting("ui:daily", "500"), 500, 1, 500))
        ttk.Spinbox(top, textvariable=self.daily, from_=1, to=500, width=8).grid(row=1, column=1, sticky="w", padx=(25, 0), pady=(3, 10))
        ttk.Label(top, text="Cooldown (seconds)", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=(25, 0))
        saved_cooldown = STORE.setting("ui:cooldown", "90")
        self.cooldown = tk.DoubleVar(value=float(saved_cooldown) if valid_cooldown_seconds(saved_cooldown) else 90.0)
        ttk.Spinbox(top, textvariable=self.cooldown, from_=MIN_COOLDOWN_SECONDS,
                    to=MAX_COOLDOWN_SECONDS, increment=0.1, format="%.1f", width=10).grid(row=1, column=2, sticky="w", padx=(25, 0), pady=(3, 10))
        top.columnconfigure(3, weight=1)

        ttk.Label(panel, text="Search phrases (one per line)", style="Card.TLabel").pack(anchor="w")
        self.search_box = tk.Text(panel, height=5, bg="#fbfdff", fg=COLORS["ink"],
                                  font=("Segoe UI", 9), relief="solid", bd=1, wrap="word")
        self.search_box.pack(fill="x", pady=(4, 10))
        self.search_box.insert("1.0", STORE.setting("ui:searches", "\n".join(DEFAULT_SEARCHES)))

        ttk.Label(panel, text="Relevant profile terms (comma-separated)", style="Card.TLabel").pack(anchor="w")
        self.match_box = tk.Text(panel, height=2, bg="#fbfdff", fg=COLORS["ink"],
                                 font=("Segoe UI", 9), relief="solid", bd=1, wrap="word")
        self.match_box.pack(fill="x", pady=(4, 8))
        self.match_box.insert("1.0", STORE.setting("ui:matches", ", ".join(DEFAULT_MATCH_TERMS)))

        self.filters = self.load_filters()
        filter_row = ttk.Frame(panel, style="Card.TFrame")
        filter_row.pack(fill="x", pady=(0, 8))
        ttk.Button(filter_row, text="Advanced filters…", command=self.edit_filters).pack(side="left")
        self.filter_summary = tk.StringVar(value=self.describe_filters())
        ttk.Label(filter_row, textvariable=self.filter_summary, style="Muted.TLabel", wraplength=500).pack(
            side="left", padx=(12, 0), fill="x", expand=True)

        self.pacing = tk.StringVar()
        ttk.Label(panel, textvariable=self.pacing, style="Muted.TLabel", wraplength=735).pack(anchor="w", pady=(0, 6))
        self.cooldown.trace_add("write", lambda *_: self.refresh_pacing())
        self.daily.trace_add("write", lambda *_: self.refresh_pacing())
        self.refresh_pacing()

        login_row = ttk.Frame(panel, style="Card.TFrame")
        login_row.pack(fill="x", pady=(0, 8))
        self.signin_btn = ttk.Button(login_row, text="Sign in with Google (Chrome / Edge)",
                                     command=self.google_signin)
        self.signin_btn.pack(side="left")
        ttk.Label(login_row, text="Use the Google option in a normal browser; close it before starting.",
                  style="Muted.TLabel", wraplength=390).pack(side="left", padx=(10, 0))

        row = ttk.Frame(panel, style="Card.TFrame")
        row.pack(fill="x", pady=(3, 8))
        self.start_btn = ttk.Button(row, text="Start campaign", command=self.start, style="Primary.TButton")
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(row, text="Pause", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(row, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")

        self.stats = tk.StringVar(value="Loading progress…")
        ttk.Label(panel, textvariable=self.stats, style="Card.TLabel").pack(anchor="w", pady=(6, 7))
        self.status = tk.StringVar(value="Ready. For Google accounts, click Sign in with Google first.")
        status_label = tk.Label(panel, textvariable=self.status, bg="#edf5fd", fg=COLORS["ink"],
                                wraplength=690, justify="left", anchor="w", padx=10, pady=8)
        status_label.pack(fill="x")

        ttk.Label(panel, text="Activity log", style="Card.TLabel").pack(anchor="w", pady=(10, 4))
        log_frame = ttk.Frame(panel, style="Card.TFrame")
        log_frame.pack(fill="both", expand=True)
        self.log_widget = tk.Text(log_frame, bg="#fbfdff", fg=COLORS["ink"],
                                  font=("Consolas", 9), wrap="word", relief="solid", bd=1,
                                  height=6, state="disabled")
        self.log_widget.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_widget.yview)
        scroll.pack(side="right", fill="y")
        self.log_widget.configure(yscrollcommand=scroll.set)
        ttk.Label(panel, text="Local files: " + str(APP_DIR), style="Muted.TLabel").pack(anchor="w", pady=(7, 0))

        self.refresh()
        self.root.after(250, self.pump)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def load_filters(self):
        try:
            saved = json.loads(STORE.setting("ui:filters", "{}"))
            if not isinstance(saved, dict):
                raise ValueError("Invalid filters")
        except (ValueError, TypeError):
            saved = {}
        result = {name: str(saved.get(name, "")) for name in (*FILTER_FIELDS, "exclude")}
        result["degree"] = saved.get("degree", "Any") if saved.get("degree") in ("Any", "2nd only", "2nd or 3rd") else "Any"
        try:
            result["min_score"] = max(0, min(100, int(saved.get("min_score", 25))))
        except (ValueError, TypeError):
            result["min_score"] = 25
        return result

    def describe_filters(self):
        chosen = [field.capitalize() for field in (*FILTER_FIELDS, "exclude") if self.filters.get(field, "").strip()]
        if self.filters["degree"] != "Any":
            chosen.append("degree")
        return "Profile keywords: " + (", ".join(chosen) if chosen else "none") + f" · Match score ≥ {self.filters['min_score']}"

    def refresh_pacing(self):
        try:
            pace = float(self.cooldown.get())
            daily = int(self.daily.get())
            if not valid_cooldown_seconds(pace) or not 1 <= daily <= 500:
                raise ValueError("Invalid cooldown or cap")
            maximum = theoretical_daily_capacity(pace)
            self.pacing.set(f"Daily cap: {daily} (your setting, NOT LinkedIn's limit). "
                            f"At {pace:g}-second minimum spacing, pacing-only ceiling ≈ {min(daily, maximum)} per day "
                            "during 9 AM–7 PM Eastern; no guarantee of delivery.")
        except (ValueError, tk.TclError):
            self.pacing.set("Set valid daily cap and cooldown values.")

    def edit_filters(self):
        if self.running:
            messagebox.showinfo("Pause first", "Stop the campaign before changing filters.")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Advanced profile filters")
        dlg.geometry("670x670")
        dlg.minsize(580, 620)
        dlg.configure(bg=COLORS["bg"])
        dlg.transient(self.root)
        dlg.grab_set()
        container = ttk.Frame(dlg, padding=20)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Find relevant people", style="Title.TLabel").pack(anchor="w")
        ttk.Label(container, text="Optional visible-profile keyword checks. Comma = OR; different categories = AND. "
                  "If information is missing, the profile is excluded.", wraplength=610).pack(anchor="w", pady=(5, 12))
        fields = [
            ("locations", "Locations (e.g. New Jersey, Philadelphia, Washington DC)"),
            ("titles", "Job titles (e.g. legislative aide, policy analyst)"),
            ("companies", "Organizations or employers (e.g. NJ Legislature, Rowan University)"),
            ("schools", "Schools (e.g. Rowan University, Rutgers)"),
            ("industries", "Industry/sector words (e.g. government, public policy)"),
            ("exclude", "Exclude if any of these words appear"),
        ]
        edits = {}
        for name, title in fields:
            ttk.Label(container, text=title).pack(anchor="w", pady=(5, 1))
            entry = ttk.Entry(container)
            entry.insert(0, self.filters.get(name, ""))
            entry.pack(fill="x", pady=(0, 5))
            edits[name] = entry
        opts = ttk.Frame(container)
        opts.pack(fill="x", pady=(8, 10))
        ttk.Label(opts, text="Connection degree (when visible)").grid(row=0, column=0, sticky="w")
        degree = tk.StringVar(value=self.filters["degree"])
        ttk.Combobox(opts, textvariable=degree, state="readonly", width=18,
                     values=("Any", "2nd only", "2nd or 3rd")).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Label(opts, text="Minimum interest score (0–100)").grid(row=0, column=1, sticky="w", padx=(25, 0))
        minimum = tk.IntVar(value=self.filters["min_score"])
        ttk.Spinbox(opts, textvariable=minimum, from_=0, to=100, width=7).grid(row=1, column=1, sticky="w", padx=(25, 0), pady=4)
        ttk.Label(container, text="Matches are checked against visible profile text, not verified LinkedIn "
                  "metadata. Search terms still control initial discovery.", wraplength=610).pack(anchor="w", pady=(0, 10))
        def save():
            try:
                value = int(minimum.get())
                if not 0 <= value <= 100:
                    raise ValueError("Score out of range")
            except (ValueError, AssertionError, tk.TclError):
                messagebox.showerror("Invalid score", "Choose a minimum score from 0 to 100.", parent=dlg)
                return
            self.filters = {name: edit.get().strip() for name, edit in edits.items()}
            self.filters.update({"degree": degree.get(), "min_score": value})
            STORE.set_setting("ui:filters", json.dumps(self.filters, sort_keys=True))
            self.filter_summary.set(self.describe_filters())
            dlg.destroy()
        ttk.Button(container, text="Save filters", command=save, style="Primary.TButton").pack(anchor="e")
        dlg.bind("<Escape>", lambda _: dlg.destroy())

    def put_log(self, msg):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", f"[{datetime.now():%H:%M:%S}] {msg}\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def refresh(self):
        counts = STORE.counts()
        self.stats.set(f"Attempts: {STORE.total_attempts()}    Sent: {counts.get('sent', 0)}    "
                       f"Queued: {counts.get('queued', 0)}    Filtered: {counts.get('filtered', 0)}    "
                       f"Skipped: {counts.get('skipped', 0)}    Unconfirmed: {counts.get('uncertain', 0)}    "
                       f"Today: {STORE.sent_today()} confirmed / {STORE.attempts_today()} possible")

    def emit(self, kind, data=""):
        self.events.put((kind, data))

    def pump(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "status":
                    self.status.set(data)
                    # Don't turn every minute of waiting into an enormous log.
                    if "Next action in" not in data:
                        self.put_log(data)
                elif kind == "refresh":
                    self.refresh()
                elif kind == "done":
                    self.running = False
                    self.start_btn.configure(state="normal")
                    self.signin_btn.configure(state="normal")
                    self.pause_btn.configure(state="disabled", text="Pause")
                    self.stop_btn.configure(state="disabled")
                    self.pause_event.clear()
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(250, self.pump)

    def google_signin(self):
        if self.running:
            messagebox.showinfo("Stop first", "Stop the campaign before signing in again.")
            return
        try:
            channel = open_google_signin(APP_DIR)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot open browser", str(exc))
            return
        STORE.set_setting("ui:browser_channel", channel)
        self.status.set("Google sign-in opened in " + ("Chrome" if channel == "chrome" else "Edge")
                        + ". Complete Continue with Google on LinkedIn, CLOSE that browser window, then click Start campaign.")
        messagebox.showinfo("Sign in with Google",
                            "1. In the normal browser window, use LinkedIn's 'Continue with Google'.\n"
                            "2. Complete any Google verification yourself.\n"
                            "3. Once LinkedIn shows your feed, CLOSE that browser window completely.\n"
                            "4. Return here and click Start campaign.\n\n"
                            "This uses a dedicated local browser profile. Never enter credentials in ConnectionPilot.")

    def start(self):
        if self.running:
            return
        try:
            target = int(self.target.get())
            daily = int(self.daily.get())
            cooldown = float(self.cooldown.get())
            if not (1 <= target <= 5000 and 1 <= daily <= 500 and valid_cooldown_seconds(cooldown)):
                raise ValueError("Campaign setting out of range")
            searches = [s.strip() for s in self.search_box.get("1.0", "end").splitlines() if s.strip()]
            matches = [s.strip() for s in self.match_box.get("1.0", "end").split(",") if s.strip()]
            if not searches or not matches:
                raise ValueError("Search and match terms required")
        except (ValueError, AssertionError, tk.TclError):
            messagebox.showerror("Invalid configuration", "Choose a goal (1–5000), daily cap (1–500), cooldown (0.1–21,600 seconds), and nonempty search/match terms.")
            return
        # User explicitly starts the campaign with this button. A second
        # confirmation would defeat one-click operation; permanent risk
        # disclosure remains visible in the main window.
        previous_searches = STORE.setting("ui:searches")
        # An edited search means the old unsent queue may no longer belong to
        # the user's requested audience. Preserve every recorded click and
        # discard only leads that have never been attempted.
        old_terms = {s.strip().casefold() for s in (previous_searches or "").splitlines() if s.strip()}
        new_terms = {s.casefold() for s in searches}
        if previous_searches is not None and not old_terms.issubset(new_terms):
            removed = STORE.discard_stale_leads()
            STORE.reset_search_pages(searches)
            self.emit("status", f"Searches changed: removed {removed} older unsent lead(s); restarting discovery.")
        current_filters_json = json.dumps(self.filters, sort_keys=True)
        matching_config = json.dumps({"filters": self.filters, "terms": matches}, sort_keys=True)
        if STORE.setting("run:matching") != matching_config:
            STORE.requeue_filtered()
            STORE.reset_search_pages(searches)
            STORE.set_setting("run:matching", matching_config)
        for k, val in {"target": target, "daily": daily, "cooldown": cooldown,
                       "searches": "\n".join(searches), "matches": ", ".join(matches),
                       "filters": current_filters_json}.items():
            STORE.set_setting("ui:" + k, val)
        self.stop_event.clear()
        self.pause_event.clear()
        self.running = True
        self.start_btn.configure(state="disabled")
        self.signin_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Pause")
        self.stop_btn.configure(state="normal")
        config = {"target": target, "daily_limit": daily, "cooldown": cooldown,
                  "searches": searches, "match_terms": matches, "filters": dict(self.filters),
                  "data_dir": str(APP_DIR), "browser_channel": STORE.setting("ui:browser_channel", "chromium"),
                  "start_hour": 9, "end_hour": 19}
        self.worker = threading.Thread(target=self.work, args=(config,), daemon=True)
        self.worker.start()

    def work(self, config):
        try:
            run_campaign(STORE, config, self.stop_event, self.pause_event,
                         lambda s: self.emit("status", s), lambda: self.emit("refresh"))
        except ProtectionScreen as exc:
            STORE.log("HALTED", str(exc))
            self.emit("status", "STOPPED: " + str(exc) + ". Do not restart while restricted.")
        except StopRun as exc:
            self.emit("status", "Stopped. " + str(exc))
        except Exception as exc:
            STORE.log("ERROR", safe_diagnostic(exc, 200))
            self.emit("status", "Error: " + safe_diagnostic(exc, 200) + ". No further requests will be sent.")
        finally:
            self.emit("done")

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_btn.configure(text="Pause")
            self.emit("status", "Resumed.")
        else:
            self.pause_event.set()
            self.pause_btn.configure(text="Resume")
            self.emit("status", "Pause requested. Current browser action may finish first.")

    def stop(self):
        self.stop_event.set()
        self.pause_event.clear()
        self.emit("status", "Stop requested. Closing browser after current action.")

    def close(self):
        if self.closing:
            return
        if self.running:
            if not messagebox.askyesno("Exit program?", "Stop the running campaign and close the application?"):
                return
            self.closing = True
            self.stop_event.set()
            self.pause_event.clear()
            self.status.set("Closing the automation browser and saving progress…")
            self.start_btn.configure(state="disabled")
            self.pause_btn.configure(state="disabled")
            self.stop_btn.configure(state="disabled")
            self.root.after(300, self.finish_close)
        else:
            self.root.destroy()

    def finish_close(self):
        if self.worker and self.worker.is_alive():
            self.root.after(300, self.finish_close)
        else:
            self.root.destroy()

def acquire_instance_lock(path):
    """Prevent two simultaneous campaigns using one account/data directory.

    OS locks are released on process exit, including unexpected crashes.
    """
    import os
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


if __name__ == "__main__":
    instance_lock = acquire_instance_lock(APP_DIR / "instance.lock")
    if instance_lock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("ConnectionPilot already open",
                             "Close the other ConnectionPilot window before starting another campaign.")
        root.destroy()
    else:
        root = tk.Tk()
        try:
            App(root)
            root.mainloop()
        finally:
            instance_lock.close()
