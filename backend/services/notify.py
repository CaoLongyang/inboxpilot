"""Desktop cards for mail that arrives while you are doing something else.

Why not a Streamlit toast: it only exists while the tab is focused, which is
exactly when you do not need to be told. The point of a watcher is that it
reaches you when InboxPilot is not on screen.

Why Tkinter rather than a notification library: it ships with Python, needs no
install, and gives a card we control completely — colour by urgency, wrapped
text, click to open. The usual objection is that Tk looks dated, but a
borderless topmost frame with a coloured edge is a notification, not a dialog.

Threading: Tk is not thread-safe, so every widget call happens on the one
thread that created the root. `show()` only puts a message on a queue; the Tk
thread drains it from `after()`. That is the only supported way to talk to Tk
from a worker, and it is why the watcher can run its blocking poll loop in a
separate thread.
"""

from __future__ import annotations

import logging
import queue
import threading
import webbrowser
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Urgency levels, shared with the watcher. 3 = act today, 2 = worth a look.
LEVEL_COLOUR = {3: "#f85149", 2: "#d29922", 1: "#8b949e"}


def _enable_dpi_awareness() -> None:
    """Stop Windows from bitmap-stretching the card.

    Python is DPI-unaware by default, so on a 150% display Windows renders the
    window at 100% and scales the result up: the text goes soft, and a card is
    mostly text. Declaring awareness makes Tk draw at real pixels, which also
    means `winfo_screenwidth()` returns physical pixels and the corner maths
    stays correct.

    Must run before any window is created, and failing is fine — a blurry card
    is better than no card.
    """
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Windows 8.1+
        except Exception:  # noqa: BLE001 - older Windows
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - not Windows, or no ctypes
        pass


@dataclass(frozen=True)
class Notification:
    """One card. Text only, already truncated, no markup.

    Everything here comes from an email, which means it is attacker-controlled
    text. It is rendered as plain label text and never as a link, so a subject
    line cannot become a clickable target and the only clickable thing on the
    card is the card itself.
    """

    sender: str
    subject: str
    verdict: str
    detail: str = ""
    level: int = 2
    message_id: str = ""
    url: str = ""


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_text(notification: Notification) -> str:
    """The console form. Also what a log line or a test asserts against."""
    lines = [
        f"[{notification.level}] {_short(notification.sender, 40)}",
        f"    {_short(notification.subject, 80)}",
        f"    {notification.verdict}",
    ]
    if notification.detail:
        lines.append(f"    {_short(notification.detail, 100)}")
    return "\n".join(lines)


class ConsoleNotifier:
    """Fallback for headless machines, CI, and `--no-notify`."""

    def show(self, notification: Notification) -> None:
        print(render_text(notification), flush=True)

    def wait(self, timeout: float | None = None) -> None:
        return

    def stop(self) -> None:  # symmetry with TkNotifier
        return


class TkNotifier:
    """Cards in the bottom-right corner, newest nearest the corner."""

    WIDTH = 380
    HEIGHT = 104
    GAP = 10
    MARGIN = 24
    LIFETIME_MS = 12_000

    def __init__(self) -> None:
        self._queue: queue.Queue[Notification | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._root = None
        self._cards: list[object] = []
        self._failed = False

    # -- public API, callable from any thread ------------------------------

    def show(self, notification: Notification) -> None:
        if self._failed:
            print(render_text(notification), flush=True)
            return
        self._start()
        self._queue.put(notification)

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)

    def wait(self, timeout: float | None = None) -> None:
        """Block so cards stay on screen.

        `--once` exits as soon as its cycle finishes, and the card thread is a
        daemon: without this the window would appear and vanish in the same
        frame. The Tk loop itself never returns, so this waits for the timeout
        rather than for an "all cards closed" signal.
        """
        if self._thread is not None:
            self._thread.join(timeout)

    # -- Tk thread ---------------------------------------------------------

    def _start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="inboxpilot-cards", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        _enable_dpi_awareness()
        try:
            import tkinter as tk

            root = tk.Tk()
        except Exception as exc:  # noqa: BLE001 - no display, no Tk, no problem
            log.warning("notify.tk_unavailable error=%s", type(exc).__name__)
            self._failed = True
            self._drain_to_console()
            return

        root.withdraw()
        self._root = root
        root.after(150, self._drain)
        try:
            root.mainloop()
        except Exception as exc:  # noqa: BLE001 - window manager going away
            log.warning("notify.loop_failed error=%s", type(exc).__name__)

    def _drain_to_console(self) -> None:
        while True:
            try:
                notification = self._queue.get_nowait()
            except queue.Empty:
                return
            if notification is not None:
                print(render_text(notification), flush=True)

    def _drain(self) -> None:
        while True:
            try:
                notification = self._queue.get_nowait()
            except queue.Empty:
                break
            if notification is None:
                self._root.quit()
                return
            try:
                self._spawn(notification)
            except Exception as exc:  # noqa: BLE001 - one bad card must not stop the rest
                log.warning("notify.spawn_failed error=%s", type(exc).__name__)
                print(render_text(notification), flush=True)
        self._root.after(150, self._drain)

    def _spawn(self, notification: Notification) -> None:
        import tkinter as tk

        root = self._root
        card = tk.Toplevel(root)
        card.overrideredirect(True)
        card.attributes("-topmost", True)
        card.configure(bg="#161b22")

        frame = tk.Frame(card, bg="#161b22", highlightthickness=1, highlightbackground="#30363d")
        frame.pack(fill="both", expand=True)

        accent = tk.Frame(frame, bg=LEVEL_COLOUR.get(notification.level, "#8b949e"), width=5)
        accent.pack(side="left", fill="y")

        body = tk.Frame(frame, bg="#161b22")
        body.pack(side="left", fill="both", expand=True, padx=(12, 12), pady=(10, 10))

        tk.Label(
            body, text=_short(notification.sender, 46), bg="#161b22", fg="#8b949e",
            font=("Segoe UI", 9), anchor="w", justify="left",
        ).pack(fill="x")
        tk.Label(
            body, text=_short(notification.subject, 54), bg="#161b22", fg="#e6edf3",
            font=("Segoe UI", 10, "bold"), anchor="w", justify="left",
        ).pack(fill="x", pady=(2, 0))
        tk.Label(
            body, text=_short(notification.verdict, 52), bg="#161b22",
            fg=LEVEL_COLOUR.get(notification.level, "#8b949e"),
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left",
        ).pack(fill="x", pady=(4, 0))

        for widget in (card, frame, accent, body, *body.winfo_children()):
            widget.bind("<Button-1>", lambda _event, n=notification: self._open(n, card))

        self._cards.append(card)
        self._place(card)
        self._arm(card)
        card.bind("<Enter>", lambda _event: self._cancel(card))
        card.bind("<Leave>", lambda _event: self._arm(card))

    def _place(self, card: object) -> None:
        index = self._cards.index(card)
        screen_w = card.winfo_screenwidth()
        screen_h = card.winfo_screenheight()
        x = screen_w - self.WIDTH - self.MARGIN
        y = screen_h - 96 - self.HEIGHT - index * (self.HEIGHT + self.GAP)
        card.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{max(y, self.MARGIN)}")

    def _arm(self, card: object) -> None:
        # Hovering pauses the countdown: a card that disappears while being
        # read is worse than no card.
        card._timer = card.after(self.LIFETIME_MS, lambda: self._close(card))

    def _cancel(self, card: object) -> None:
        timer = getattr(card, "_timer", None)
        if timer is not None:
            card.after_cancel(timer)
            card._timer = None

    def _close(self, card: object) -> None:
        self._cancel(card)
        try:
            card.destroy()
        except Exception:  # noqa: BLE001 - already gone
            pass
        if card in self._cards:
            self._cards.remove(card)
        for remaining in self._cards:
            try:
                self._place(remaining)
            except Exception:  # noqa: BLE001 - window closing
                pass

    def _open(self, notification: Notification, card: object) -> None:
        if notification.url:
            try:
                webbrowser.open(notification.url)
            except Exception as exc:  # noqa: BLE001 - no browser is not fatal
                log.warning("notify.open_failed error=%s", type(exc).__name__)
        self._close(card)


def make_notifier(*, desktop: bool = True) -> ConsoleNotifier | TkNotifier:
    """Desktop cards when asked for, console otherwise.

    Returning a working notifier matters more than returning the pretty one:
    the watcher must keep running on a headless machine, and TkNotifier already
    falls back to the console by itself if Tk cannot start.
    """
    return TkNotifier() if desktop else ConsoleNotifier()
