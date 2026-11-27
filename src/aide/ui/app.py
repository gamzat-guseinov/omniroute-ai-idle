"""Minimal Textual chat UI (Stage 2: streaming chat only, no tools yet)."""
from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Static

from .. import __version__
from ..client.errors import OmniRouteError
from ..client.events import Done, ReasoningDelta, RetryEvent, TextDelta, Usage
from ..client.omniroute import OmniRouteClient
from ..config import Config
from ..debuglog import DebugLog

# Temporary prompt for Stage 2. The full, editable agent prompt arrives with the agent loop.
SYSTEM_PROMPT = (
    "You are aide, an AI coding assistant running in the user's terminal. "
    "The active workspace is {workspace}. You cannot read files or run commands yet: "
    "answer questions, explain code and propose changes as text. Be concise and precise."
)

HELP_TEXT = (
    "Commands:\n"
    "  /help    show this help\n"
    "  /clear   forget the conversation and start fresh\n"
    "  /exit    quit\n"
    "Keys:\n"
    "  Enter  send     Ctrl+C  stop generating     Ctrl+Q  quit\n"
    "More commands (/model, /mode, /undo, /diff, /tokens, /save) arrive in later stages."
)

PAINT_INTERVAL = 0.04  # seconds between screen updates while streaming


class AideApp(App):
    TITLE = "aide"
    CSS = """
    #status { height: 1; padding: 0 1; background: $panel; }
    #chat { height: 1fr; padding: 0 2; }
    .msg { width: 100%; margin-top: 1; }
    .user { color: cyan; }
    .info { color: gray; }
    .error { color: red; }
    """
    BINDINGS = [
        Binding("ctrl+c", "stop", "Stop", priority=True),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, config: Config, workspace: Path, debug: DebugLog) -> None:
        super().__init__()
        self._cfg = config
        self._workspace = workspace
        self._debug = debug
        self._client = OmniRouteClient(config, debug=debug)
        self._history: list[dict] = [self._system_message()]
        self._busy = False
        self._worker = None
        self._last_usage: Usage | None = None
        self.sub_title = f"workspace: {workspace}"

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(Text(""), id="status")
        yield VerticalScroll(id="chat")
        yield Input(placeholder="Message aide...  (/help for commands)", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_status()
        self._add_message(
            f"aide {__version__}  ·  workspace: {self._workspace}\n"
            f"model: {self._cfg.model}  ·  Stage 2: chat only (file and command tools come next)."
            + (f"\ndebug log: {self._debug.path}" if self._debug.enabled else ""),
            "info",
        )
        self.query_one("#prompt", Input).focus()

    async def on_unmount(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass
        self._debug.close()

    # ------------------------------------------------------------------ helpers
    def _system_message(self) -> dict:
        return {"role": "system", "content": SYSTEM_PROMPT.format(workspace=self._workspace)}

    def _chat_view(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    def _scroll_end(self) -> None:
        chat = self._chat_view()
        chat.call_after_refresh(chat.scroll_end, animate=False)

    def _add_message(self, text: str, kind: str) -> Static:
        widget = Static(Text(text), classes=f"msg {kind}")
        self._chat_view().mount(widget)
        self._scroll_end()
        return widget

    def _refresh_status(self, note: str = "") -> None:
        parts = [f"model: {self._cfg.model}"]
        if self._last_usage:
            u = self._last_usage
            parts.append(f"last reply: {u.prompt_tokens} in / {u.completion_tokens} out tokens")
        if note:
            parts.append(note)
        self.query_one("#status", Static).update(Text("  |  ".join(parts), no_wrap=True,
                                                      overflow="ellipsis"))

    # ------------------------------------------------------------------ input
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self._busy:
            self.notify("Still generating - press Ctrl+C to stop it first.", severity="warning")
            return
        event.input.value = ""
        if text.startswith("/"):
            self._run_command(text)
        else:
            self._send(text)

    def _run_command(self, text: str) -> None:
        command = text.split()[0].lower()
        if command == "/help":
            self._add_message(HELP_TEXT, "info")
        elif command == "/clear":
            self._history = [self._system_message()]
            self._chat_view().remove_children()
            self._add_message("Conversation cleared.", "info")
        elif command in ("/exit", "/quit"):
            self.exit()
        else:
            self._add_message(f"Unknown command {command}. Type /help.", "error")

    def action_stop(self) -> None:
        if self._busy and self._worker is not None:
            self._worker.cancel()
        else:
            self.notify("Nothing is running. Press Ctrl+Q or type /exit to quit.")

    # ------------------------------------------------------------------ generation
    def _send(self, text: str) -> None:
        self._history.append({"role": "user", "content": text})
        self._add_message(f"> {text}", "user")
        self._busy = True
        self._refresh_status("generating... (Ctrl+C to stop)")
        self._worker = self.run_worker(self._generate(text), group="generate", exclusive=True)

    async def _generate(self, user_text: str) -> None:
        widget: Static | None = None
        chunks: list[str] = []
        last_paint = 0.0
        finish: str | None = None
        error_text: str | None = None
        cancelled = False
        try:
            async with aclosing(self._client.stream_chat(self._history)) as stream:
                async for event in stream:
                    if isinstance(event, TextDelta):
                        if widget is None:
                            widget = self._add_message("", "assistant")
                        chunks.append(event.text)
                        now = time.monotonic()
                        if now - last_paint >= PAINT_INTERVAL:
                            widget.update(Text("".join(chunks)))
                            self._scroll_end()
                            last_paint = now
                    elif isinstance(event, ReasoningDelta):
                        self._refresh_status("model is thinking... (Ctrl+C to stop)")
                    elif isinstance(event, RetryEvent):
                        self._add_message(
                            f"{event.error.message} Retrying in {event.delay:.1f}s "
                            f"(attempt {event.attempt}/{event.max_retries})...", "info")
                    elif isinstance(event, Usage):
                        self._last_usage = event
                    elif isinstance(event, Done):
                        finish = event.finish_reason
        except asyncio.CancelledError:
            cancelled = True
            raise
        except OmniRouteError as exc:
            error_text = exc.user_message()
        except Exception as exc:  # a bug must not crash the whole UI
            error_text = f"Unexpected error: {exc!r}"
        finally:
            self._finish_turn(user_text, "".join(chunks), widget, finish, error_text, cancelled)

    def _finish_turn(self, user_text: str, reply: str, widget: Static | None,
                     finish: str | None, error_text: str | None, cancelled: bool) -> None:
        """Synchronous on purpose: it also runs while the worker is being cancelled."""
        self._busy = False
        self._worker = None
        if widget is not None:
            widget.update(Text(reply))
        if reply:
            self._history.append({"role": "assistant", "content": reply})
        else:  # nothing came back: drop the question so the history stays consistent
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            prompt = self.query_one("#prompt", Input)
            if (error_text or cancelled) and not prompt.value:
                prompt.value = user_text  # let the user retry with one Enter
        if cancelled:
            self._add_message("Stopped.", "info")
        if error_text:
            self._add_message(f"Error: {error_text}", "error")
        if finish == "length":
            self._add_message("The reply hit the max_tokens limit and was cut off.", "info")
        self._refresh_status()
        self._scroll_end()
