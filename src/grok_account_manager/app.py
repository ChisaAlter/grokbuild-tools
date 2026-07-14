from __future__ import annotations

import threading
from datetime import datetime, timezone
from tkinter import simpledialog

import customtkinter as ctk

from . import __version__ as APP_VERSION
from .auth_bridge import AuthBridge
from .browser_util import open_private_url
from .login_flow import run_login_and_capture
from .models import Account, QuotaInfo
from .paths import AppPaths
from .quota import QuotaProbe
from .store import AccountStore
from .updater import UpdateInfo, apply_update, check_for_update, open_release_page


def _fmt_time(value: str | None, *, with_seconds: bool = False) -> str:
    if not value:
        return "—"
    text = str(value).strip()
    if not text:
        return "—"
    try:
        iso = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
        return local.strftime(fmt)
    except Exception:
        cleaned = text.replace("T", " ").split("+")[0].split(".")[0]
        return cleaned[:16] if len(cleaned) >= 16 else cleaned


def _fmt_num(n) -> str:
    if n is None:
        return "—"
    if isinstance(n, float) and not float(n).is_integer():
        return f"{n:.1f}"
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _quota_bar_color(rem_pct: float | None) -> str:
    if rem_pct is None:
        return "#555555"
    if rem_pct >= 50:
        return "#2d6a4f"
    if rem_pct >= 20:
        return "#b08900"
    return "#9b2226"


class GrokAccountManagerApp(ctk.CTk):
    def __init__(self, paths: AppPaths | None = None) -> None:
        super().__init__()
        self.paths = paths or AppPaths.default()
        self.store = AccountStore(self.paths)
        self.auth = AuthBridge(self.paths)
        self.quota = QuotaProbe()

        self.title(f"Grok Account Manager  v{APP_VERSION}")
        self.geometry("1040x660")
        self.minsize(920, 560)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._selected_id: str | None = None
        self._busy = False
        self._account_rows: dict[str, ctk.CTkFrame] = {}
        self._auto_after_id: str | None = None
        self._pending_update: UpdateInfo | None = None

        # persisted timer prefs
        auto_on = bool(self.store.get_setting("auto_refresh_enabled", False))
        try:
            auto_min = int(self.store.get_setting("auto_refresh_minutes", 30) or 30)
        except (TypeError, ValueError):
            auto_min = 30
        auto_min = max(1, min(24 * 60, auto_min))
        self._auto_refresh_var = ctk.BooleanVar(value=auto_on)
        self._interval_var = ctk.StringVar(value=str(auto_min))

        self._build_ui()
        self.refresh_list()
        self._set_status(f"就绪 · v{APP_VERSION}")
        if auto_on:
            self._schedule_auto_refresh(announce=True)
        # background version check shortly after start
        self.after(1200, self._startup_check_update)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        header = ctk.CTkFrame(self, fg_color="transparent", height=52)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))
        header.grid_propagate(False)
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Grok Account Manager",
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(4, 8), pady=8)

        btns = ctk.CTkFrame(header, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e", pady=6)

        # Auto-refresh: switch + minutes
        ctk.CTkLabel(btns, text="定时刷新").pack(side="left", padx=(0, 4))
        self._auto_switch = ctk.CTkSwitch(
            btns,
            text="",
            width=42,
            variable=self._auto_refresh_var,
            command=self._on_auto_refresh_toggle,
        )
        self._auto_switch.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(btns, text="间隔").pack(side="left", padx=(4, 2))
        self._interval_entry = ctk.CTkEntry(
            btns,
            width=48,
            height=28,
            textvariable=self._interval_var,
            justify="center",
        )
        self._interval_entry.pack(side="left", padx=2)
        self._interval_entry.bind("<Return>", lambda _e: self._save_interval_minutes())
        self._interval_entry.bind("<FocusOut>", lambda _e: self._save_interval_minutes())
        ctk.CTkLabel(btns, text="分钟").pack(side="left", padx=(2, 10))

        self._version_label = ctk.CTkLabel(
            btns, text=f"v{APP_VERSION}", text_color="#aaaaaa"
        )
        self._version_label.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="检查更新", width=88, height=32, command=self.on_check_update
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            btns, text="刷新全部额度", width=120, height=32, command=self.on_refresh_all_quota
        ).pack(side="left", padx=4)

        body = ctk.CTkFrame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text="账号列表", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4)
        )
        self.list_frame = ctk.CTkScrollableFrame(left, width=320)
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

        right = ctk.CTkFrame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        self.detail_title = ctk.CTkLabel(
            right, text="选择一个账号", font=ctk.CTkFont(size=16, weight="bold")
        )
        self.detail_title.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 6))
        self.detail_text = ctk.CTkTextbox(right, height=360, wrap="word")
        self.detail_text.grid(row=1, column=0, sticky="nsew", padx=14, pady=6)

        actions = ctk.CTkFrame(right, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 14))
        ctk.CTkButton(actions, text="切换为当前", width=100, command=self.on_switch).pack(
            side="left", padx=4
        )
        ctk.CTkButton(actions, text="刷新额度", width=90, command=self.on_refresh_one_quota).pack(
            side="left", padx=4
        )
        ctk.CTkButton(actions, text="改名", width=70, command=self.on_rename).pack(
            side="left", padx=4
        )
        ctk.CTkButton(
            actions,
            text="删除",
            width=70,
            fg_color="#8B3A3A",
            hover_color="#6E2E2E",
            command=self.on_delete,
        ).pack(side="left", padx=4)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        footer.grid_columnconfigure(2, weight=1)
        ctk.CTkButton(
            footer,
            text="新增（登录）",
            width=120,
            fg_color="#2d6a4f",
            hover_color="#1b4332",
            command=self.on_add_login,
        ).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ctk.CTkButton(
            footer, text="收录当前 Grok 登录", width=160, command=self.on_capture
        ).grid(row=0, column=1, sticky="w")
        self.status = ctk.CTkLabel(footer, text="", anchor="w")
        self.status.grid(row=0, column=2, sticky="ew", padx=12)

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _parse_interval_minutes(self) -> int:
        try:
            m = int(str(self._interval_var.get()).strip())
        except (TypeError, ValueError):
            m = 30
        return max(1, min(24 * 60, m))

    def _save_interval_minutes(self) -> int:
        m = self._parse_interval_minutes()
        self._interval_var.set(str(m))
        self.store.set_setting("auto_refresh_minutes", m)
        if self._auto_refresh_var.get():
            self._schedule_auto_refresh(announce=False)
            self._set_status(f"定时刷新间隔已设为 {m} 分钟")
        return m

    def _on_auto_refresh_toggle(self) -> None:
        enabled = bool(self._auto_refresh_var.get())
        self.store.set_setting("auto_refresh_enabled", enabled)
        m = self._save_interval_minutes()
        if enabled:
            self._set_status(f"已开启定时刷新：每 {m} 分钟")
            self._schedule_auto_refresh(announce=False)
        else:
            self._cancel_auto_refresh()
            self._set_status("已关闭定时刷新")

    def _cancel_auto_refresh(self) -> None:
        if self._auto_after_id is not None:
            try:
                self.after_cancel(self._auto_after_id)
            except Exception:
                pass
            self._auto_after_id = None

    def _schedule_auto_refresh(self, *, announce: bool = False) -> None:
        self._cancel_auto_refresh()
        if not self._auto_refresh_var.get():
            return
        m = self._parse_interval_minutes()
        ms = m * 60 * 1000
        self._auto_after_id = self.after(ms, self._auto_refresh_tick)
        if announce:
            self._set_status(f"定时刷新已开启：每 {m} 分钟")

    def _auto_refresh_tick(self) -> None:
        """Timer fired: refresh quotas if idle, then reschedule."""
        self._auto_after_id = None
        if not self._auto_refresh_var.get():
            return
        if self._busy:
            self._set_status("定时刷新跳过（有任务进行中）")
        elif not self.store.list_accounts():
            self._set_status("定时刷新跳过（无账号）")
        else:
            # silent=True: no popup spam
            self.on_refresh_all_quota(silent=True, from_timer=True)
        # always arm next tick while enabled
        self._schedule_auto_refresh(announce=False)

    def _run_bg(self, work, on_done=None) -> None:
        if self._busy:
            self._set_status("请等待当前任务完成…")
            return

        def runner() -> None:
            self._busy = True
            err = None
            result = None
            try:
                result = work()
            except Exception as e:
                err = e
            finally:
                self._busy = False

            def finish() -> None:
                try:
                    self.store.load()
                except Exception:
                    pass
                if err:
                    self._set_status(f"错误: {err}")
                if on_done:
                    on_done(result, err)
                self.refresh_list(keep_selection=True)

            self.after(0, finish)

        threading.Thread(target=runner, daemon=True).start()

    def _quota_progress_values(self, q: QuotaInfo) -> tuple[float | None, str]:
        """Return (progress 0..1 for remaining, label text)."""
        rem_pct = q.remaining_percent()
        if rem_pct is not None:
            used_pct = 100.0 - rem_pct
            if q.credits_remaining is not None and q.credits_limit is not None:
                label = (
                    f"剩余 {_fmt_num(q.credits_remaining)}/{_fmt_num(q.credits_limit)}  "
                    f"({rem_pct:.0f}%)  ·  已用 {used_pct:.0f}%"
                )
            else:
                label = f"剩余 {rem_pct:.0f}%  ·  已用 {used_pct:.0f}%"
            return max(0.0, min(1.0, rem_pct / 100.0)), label
        return None, "额度未探测 · 点「刷新额度」"

    def refresh_list(self, keep_selection: bool = True) -> None:
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._account_rows.clear()
        self.store.load()
        accounts = self.store.list_accounts()
        cur = self.auth.current_identity()
        cur_uid = cur.user_id if cur else None

        if not accounts:
            ctk.CTkLabel(
                self.list_frame,
                text="暂无账号。\n点「新增（登录）」或「收录当前」。",
                justify="left",
            ).pack(anchor="w", padx=6, pady=8)

        for acc in accounts:
            is_cur = cur_uid == acc.user_id
            selected = acc.id == self._selected_id
            row = ctk.CTkFrame(
                self.list_frame,
                fg_color=("#1f538d" if selected else ("#2b2b2b" if is_cur else "transparent")),
                border_width=1,
                border_color="#3a3a3a",
                corner_radius=8,
            )
            row.pack(fill="x", pady=5, padx=2)
            row.grid_columnconfigure(0, weight=1)

            mark = "● " if is_cur else "○ "
            chat = ""
            if acc.quota.chat_ok is True:
                chat = "  对话✓"
            elif acc.quota.chat_ok is False:
                chat = "  对话✗"

            title = ctk.CTkLabel(
                row,
                text=f"{mark}{acc.label}{chat}",
                anchor="w",
                font=ctk.CTkFont(size=13, weight="bold"),
            )
            title.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))

            prog_val, prog_label = self._quota_progress_values(acc.quota)
            sub = ctk.CTkLabel(
                row,
                text=prog_label,
                anchor="w",
                font=ctk.CTkFont(size=11),
                text_color="#cfcfcf",
            )
            sub.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))

            bar = ctk.CTkProgressBar(row, height=10, corner_radius=4)
            bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
            if prog_val is None:
                bar.set(0)
                bar.configure(progress_color="#555555")
            else:
                bar.set(prog_val)
                bar.configure(progress_color=_quota_bar_color(prog_val * 100.0))

            # Click anywhere on row
            def bind_click(widget, aid=acc.id):
                widget.bind("<Button-1>", lambda _e, i=aid: self.select_account(i))

            for w in (row, title, sub, bar):
                bind_click(w)

            self._account_rows[acc.id] = row

        if keep_selection and self._selected_id and self.store.get(self._selected_id):
            self.select_account(self._selected_id)
        elif accounts:
            self.select_account(accounts[0].id)
        else:
            self._selected_id = None
            self.detail_title.configure(text="选择一个账号")
            self.detail_text.delete("1.0", "end")

    def select_account(self, account_id: str) -> None:
        self._selected_id = account_id
        acc = self.store.get(account_id)
        if not acc:
            return
        # recolor rows
        cur = self.auth.current_identity()
        cur_uid = cur.user_id if cur else None
        for aid, row in self._account_rows.items():
            a = self.store.get(aid)
            is_cur = bool(a and cur_uid == a.user_id)
            selected = aid == account_id
            row.configure(
                fg_color=("#1f538d" if selected else ("#2b2b2b" if is_cur else "transparent"))
            )
        is_cur = self.auth.is_current(acc)
        self.detail_title.configure(text=f"{acc.label}{'  (当前)' if is_cur else ''}")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", self._format_detail(acc, is_cur))

    def _format_detail(self, acc: Account, is_cur: bool) -> str:
        q = acc.quota
        rem_pct = q.remaining_percent()
        used_pct = (
            f"{q.credit_usage_percent:.1f}%"
            if q.credit_usage_percent is not None
            else ("—" if rem_pct is None else f"{100.0 - rem_pct:.1f}%")
        )
        rem_pct_line = f"{rem_pct:.0f}%" if rem_pct is not None else "—"

        if q.credits_remaining is not None and q.credits_limit is not None:
            remain_line = f"{_fmt_num(q.credits_remaining)} / {_fmt_num(q.credits_limit)}"
        elif rem_pct is not None:
            remain_line = f"约 {rem_pct_line}"
        else:
            remain_line = "—"

        used_line = "—"
        if q.credits_used is not None and q.credits_limit is not None:
            used_line = f"{_fmt_num(q.credits_used)} / {_fmt_num(q.credits_limit)}"
        elif q.credits_used is not None:
            used_line = _fmt_num(q.credits_used)

        period = q.period_type or "—"
        if isinstance(period, str) and period.startswith("USAGE_PERIOD_TYPE_"):
            period = period.replace("USAGE_PERIOD_TYPE_", "")

        lines = [
            f"邮箱:        {acc.email or '—'}",
            f"User ID:     {acc.user_id}",
            f"当前登录:    {'是' if is_cur else '否'}",
            f"对话探测:    "
            + (
                "可用 ✓"
                if q.chat_ok is True
                else ("403 ✗" if q.chat_ok is False else "未检测")
            ),
            f"凭证过期:    {_fmt_time(q.expires_at or acc.auth_entry.get('expires_at'))}",
            "",
            "—— 额度 ——",
            f"剩余额度:    {remain_line}",
            f"剩余百分比:  {rem_pct_line}",
            f"已用额度:    {used_line}",
            f"已用百分比:  {used_pct}",
            f"周期:        {period}",
            f"周期开始:    {_fmt_time(q.period_start)}",
            f"周期结束:    {_fmt_time(q.period_end)}",
            f"上次刷新:    {_fmt_time(q.last_probed_at, with_seconds=True)}",
            f"探测信息:    {q.error or '无'}",
            "",
            f"收录时间:    {_fmt_time(acc.captured_at, with_seconds=True)}",
            f"最近切换:    {_fmt_time(acc.last_used_at, with_seconds=True)}",
            "",
            "说明: 额度来自 billing 接口；进度条表示剩余比例。",
            "切换会改 auth.json 并重启 Grok；会话历史不删。",
        ]
        return "\n".join(lines)

    def on_capture(self) -> None:
        def work():
            return self.auth.capture_current(self.store)

        def done(result, err):
            if err:
                return
            self._selected_id = result.id
            self._set_status(f"已收录: {result.label}")

        self._set_status("正在收录…")
        self._run_bg(work, done)

    def on_add_login(self) -> None:
        if not self._confirm(
            "新增账号（设备码 + 仅无痕）\n\n"
            "• 禁止 Grok 自带普通浏览器弹窗\n"
            "• 只开 Edge/Chrome 无痕\n"
            "• 面板显示验证码，可「再开无痕」\n"
            "需要本机已安装 Chrome / Edge / Firefox。",
            title="新增账号",
        ):
            return

        panel = ctk.CTkToplevel(self)
        panel.title("登录进行中")
        panel.geometry("520x320")
        panel.minsize(480, 280)
        panel.transient(self)
        panel.lift()
        login_state: dict[str, str | None] = {"url": None, "code": None}

        ctk.CTkLabel(
            panel,
            text="请只在【无痕窗口】登录新账号",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 6))
        info_box = ctk.CTkTextbox(panel, height=160, wrap="word")
        info_box.pack(fill="both", expand=True, padx=16, pady=6)
        info_box.insert("1.0", "正在启动设备码登录…\n")
        info_box.configure(state="disabled")

        def set_info(text: str) -> None:
            info_box.configure(state="normal")
            info_box.delete("1.0", "end")
            info_box.insert("1.0", text)
            info_box.configure(state="disabled")

        def reopen_private() -> None:
            url = login_state.get("url")
            if not url:
                self._set_status("还没有登录链接，请稍等…")
                return
            ok, detail = open_private_url(url)
            self._set_status(detail if ok else f"打开失败: {detail}")

        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(4, 14))
        ctk.CTkButton(row, text="再开无痕窗口", width=130, command=reopen_private).pack(
            side="left", padx=4
        )
        ctk.CTkButton(row, text="关闭此面板", width=100, command=panel.destroy).pack(
            side="left", padx=4
        )

        def work():
            def on_status(msg: str) -> None:
                self.after(0, lambda m=msg: self._set_status(m))

            def on_login_info(msg: str, url: str | None, code: str | None) -> None:
                def ui() -> None:
                    if url:
                        login_state["url"] = url
                    if code:
                        login_state["code"] = code
                    text = (
                        f"{msg}\n\n"
                        f"验证码: {login_state.get('code') or '（等待中）'}\n"
                        f"链接:\n{login_state.get('url') or '（等待中）'}\n\n"
                        "只使用无痕窗口，用新邮箱登录。"
                    )
                    if panel.winfo_exists():
                        set_info(text)
                    self._set_status(msg)

                self.after(0, ui)

            return run_login_and_capture(
                self.auth,
                self.store,
                grok_home=self.paths.grok_home,
                timeout_secs=600,
                fresh_browser=True,
                on_status=on_status,
                on_login_info=on_login_info,
            )

        def done(result, err):
            try:
                self.store.load()
            except Exception:
                pass
            try:
                if panel.winfo_exists():
                    panel.destroy()
            except Exception:
                pass
            if err:
                self._alert(f"新增失败\n\n{err}")
                return
            self._selected_id = result.account.id
            self._set_status(result.message)
            if result.is_new:
                self._alert(
                    f"新增成功\n\n{result.account.email or result.account.label}\n已加入左侧列表。"
                )
            else:
                self._alert(
                    f"登录完成（未新增行）\n\n"
                    f"{result.account.email or result.account.label}\n"
                    f"该邮箱已在列表中，已更新凭证。"
                )

        self._set_status("准备设备码无痕登录…")
        self._run_bg(work, done)

    def on_switch(self) -> None:
        if not self._selected_id:
            self._set_status("请先选择账号")
            return
        acc = self.store.get(self._selected_id)
        if not acc:
            return
        if not self.store.get_setting("skip_switch_confirm", False):
            msg = (
                f"切换到 {acc.label}？\n\n"
                "将结束当前 Grok → 写入新账号 → 用 --continue 续聊。\n"
                "对话在磁盘上不会删除。"
            )
            if not self._confirm(msg, title="切换账号"):
                return

        aid = self._selected_id

        def work():
            switched, meta = self.auth.switch_to(
                self.store,
                aid,
                sticky_secs=3.0,
                kill_running_grok=True,
                restart_grok_after=True,
                grok_cwd=None,
            )
            disk = self.auth.current_identity()
            try:
                QuotaProbe().probe_account(self.store, switched)
            except Exception:
                pass
            return {
                "account": switched,
                "disk_email": disk.email if disk else None,
                "meta": meta,
            }

        def done(result, err):
            if err:
                return
            acc2 = result.get("account")
            label = acc2.label if acc2 else acc.label
            disk_email = result.get("disk_email")
            meta = result.get("meta") or {}
            killed = meta.get("killed_pids") or []
            restarted = meta.get("restarted")
            chat_note = ""
            if meta.get("chat_ok") is True:
                chat_note = " 对话探测通过。"
            elif meta.get("chat_ok") is False:
                chat_note = " 对话探测失败（若终端 403 请换号）。"
            self._set_status(
                f"已切换为 {label}（磁盘: {disk_email or '—'}）。"
                f"结束 {len(killed)} 个进程；"
                f"{'已新开 Grok。' if restarted else '请手动 grok --continue。'}"
                f"{chat_note}"
            )

        self._set_status("正在切换…")
        self._run_bg(work, done)

    def on_refresh_one_quota(self) -> None:
        if not self._selected_id:
            return
        aid = self._selected_id

        def work():
            self.store.load()
            acc = self.store.get(aid)
            if not acc:
                raise KeyError("账号不存在")
            return self.quota.probe_account(self.store, acc)

        def done(result, err):
            if err:
                self._alert(f"刷新失败\n\n{err}")
                return
            if not result:
                return
            acc = self.store.get(aid)
            name = (acc.email or acc.label) if acc else ""
            rem = result.remaining_percent()
            if result.chat_ok is True:
                self._set_status(
                    f"{name}: 对话✓ · 剩余 {rem:.0f}%" if rem is not None else f"{name}: 对话✓"
                )
            elif result.chat_ok is False:
                self._set_status(f"{name}: 额度已刷新，对话 403")
            else:
                self._set_status(f"{name}: 额度已刷新")

        self._set_status("刷新额度中…")
        self._run_bg(work, done)

    def _github_repo_setting(self) -> str | None:
        # Hardcoded canonical repo; optional override only via env / rare setting
        repo = self.store.get_setting("github_repo", None)
        if isinstance(repo, str) and repo.strip():
            return repo.strip()
        return None  # updater falls back to DEFAULT_GITHUB_REPO

    def _startup_check_update(self) -> None:
        if self.store.get_setting("skip_startup_update_check", False):
            return

        def work():
            return check_for_update(github_repo=self._github_repo_setting())

        def done(info, err):
            if err or not info:
                return
            if info.source == "error":
                return  # quiet on startup when offline / API hiccup
            if info.has_update:
                self._pending_update = info
                self._version_label.configure(
                    text=f"v{APP_VERSION} → {info.latest}", text_color="#f4a261"
                )
                self._set_status(f"发现新版本 v{info.latest}（当前 v{info.current}），可点「检查更新」")

        self._run_bg(work, done)

    def on_check_update(self) -> None:
        def work():
            return check_for_update(github_repo=self._github_repo_setting())

        def done(info, err):
            if err:
                self._alert(f"检查更新失败\n\n{err}")
                return
            if not info:
                return
            if info.source == "error":
                self._alert(f"检查更新失败\n\n{info.message}")
                return
            self._pending_update = info
            if not info.has_update:
                self._version_label.configure(
                    text=f"v{APP_VERSION}", text_color="#aaaaaa"
                )
                self._alert(
                    f"已是最新版本\n\n当前: v{info.current}\n远程: v{info.latest}\n"
                    f"仓库: ChisaAlter/grokbuild-tools\n来源: {info.source}",
                    title="检查更新",
                )
                self._set_status(f"已是最新 v{info.current}")
                return

            self._version_label.configure(
                text=f"v{APP_VERSION} → {info.latest}", text_color="#f4a261"
            )
            body = (info.body or "").strip()
            msg = (
                f"发现新版本！\n\n"
                f"当前: v{info.current}\n"
                f"最新: v{info.latest}\n"
                f"标签: {info.tag or '—'}\n"
                f"仓库: ChisaAlter/grokbuild-tools\n\n"
            )
            if body:
                msg += f"更新说明:\n{body[:800]}\n\n"
            msg += "是否立即在线更新？\n（git 优先；否则下载 GitHub zip）"
            if not self._confirm(msg, title="发现新版本"):
                if info.release_url and self._confirm(
                    "是否打开 GitHub Release 页面手动查看？", title="打开网页"
                ):
                    open_release_page(info)
                return
            self._do_apply_update(info)

        self._set_status("正在检查 GitHub 更新…")
        self._run_bg(work, done)

    def _do_apply_update(self, info: UpdateInfo) -> None:
        def work():
            return apply_update(info, github_repo=self._github_repo_setting())

        def done(result, err):
            if err:
                self._alert(f"更新失败\n\n{err}")
                return
            ok, msg = result if isinstance(result, tuple) else (False, str(result))
            if not ok:
                self._alert(f"更新失败\n\n{msg}")
                if info.release_url and self._confirm(
                    "是否打开 GitHub 页面手动更新？", title="更新失败"
                ):
                    open_release_page(info)
                return
            self._alert(f"{msg}\n\n点击确定后将尝试重启应用。", title="更新成功")
            self._restart_app()

        self._set_status(f"正在更新到 v{info.latest}…")
        self._run_bg(work, done)

    def _restart_app(self) -> None:
        """Relaunch current module and exit."""
        import subprocess
        import sys
        from pathlib import Path

        try:
            root = Path(__file__).resolve().parents[2]
            subprocess.Popen(
                [sys.executable, "-m", "grok_account_manager"],
                cwd=str(root),
            )
        except Exception as e:
            self._alert(f"自动重启失败，请手动重新打开应用。\n{e}")
            return
        self.destroy()

    def on_refresh_all_quota(self, silent: bool = False, from_timer: bool = False) -> None:
        def work():
            self.store.load()
            ok_chat, bad_chat = [], []
            for acc in self.store.list_accounts():
                name = acc.email or acc.label
                info = self.quota.probe_account(self.store, acc)
                if info.chat_ok is True:
                    ok_chat.append(name)
                elif info.chat_ok is False:
                    bad_chat.append(name)
            return {"ok": ok_chat, "bad": bad_chat, "total": len(self.store.list_accounts())}

        def done(result, err):
            if err:
                if silent:
                    self._set_status(f"定时刷新失败: {err}")
                else:
                    self._alert(f"刷新失败\n\n{err}")
                return
            ok = result.get("ok") or []
            bad = result.get("bad") or []
            prefix = "定时刷新" if from_timer else "额度已刷新"
            self._set_status(
                f"{prefix}: {len(ok)}/{result.get('total', 0)} 对话可用"
                + (f" · {len(bad)} 个 403" if bad else "")
            )

        self._set_status("定时刷新额度中…" if from_timer else "正在刷新全部额度…")
        self._run_bg(work, done)

    def on_rename(self) -> None:
        if not self._selected_id:
            return
        acc = self.store.get(self._selected_id)
        if not acc:
            return
        new = simpledialog.askstring("改名", "新别名:", initialvalue=acc.label, parent=self)
        if new is None:
            return
        self.store.rename(self._selected_id, new)
        self.refresh_list()
        self._set_status("已改名")

    def on_delete(self) -> None:
        if not self._selected_id:
            return
        acc = self.store.get(self._selected_id)
        if not acc:
            return
        if not self._confirm(
            f"从本地列表删除 {acc.label}？\n不会登出 Grok / 不改 auth.json。"
        ):
            return
        self.store.delete(self._selected_id)
        self._selected_id = None
        self.refresh_list()
        self._set_status("已从本地删除")

    def _confirm(self, message: str, *, title: str = "确认") -> bool:
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("480x280")
        dialog.minsize(420, 220)
        dialog.transient(self)
        dialog.grab_set()
        dialog.lift()
        result = {"ok": False}
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(0, weight=1)
        body = ctk.CTkTextbox(dialog, wrap="word", height=160)
        body.grid(row=0, column=0, sticky="nsew", padx=16, pady=(16, 8))
        body.insert("1.0", message)
        body.configure(state="disabled")
        row = ctk.CTkFrame(dialog, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))

        def yes() -> None:
            result["ok"] = True
            dialog.destroy()

        def no() -> None:
            dialog.destroy()

        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.pack()
        ctk.CTkButton(btns, text="取消", width=110, height=34, command=no).pack(
            side="left", padx=8
        )
        ctk.CTkButton(btns, text="确定", width=110, height=34, command=yes).pack(
            side="left", padx=8
        )
        try:
            dialog.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - 480) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - 280) // 2)
            dialog.geometry(f"480x280+{x}+{y}")
        except Exception:
            pass
        self.wait_window(dialog)
        return result["ok"]

    def _alert(self, message: str, *, title: str = "提示") -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("460x240")
        dialog.minsize(400, 200)
        dialog.transient(self)
        dialog.grab_set()
        dialog.lift()
        body = ctk.CTkTextbox(dialog, wrap="word", height=140)
        body.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        body.insert("1.0", message)
        body.configure(state="disabled")
        ctk.CTkButton(dialog, text="知道了", width=120, command=dialog.destroy).pack(
            pady=(0, 16)
        )
        try:
            dialog.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - 460) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - 240) // 2)
            dialog.geometry(f"460x240+{x}+{y}")
        except Exception:
            pass
        self.wait_window(dialog)


def run_app(paths: AppPaths | None = None) -> None:
    app = GrokAccountManagerApp(paths=paths)
    app.mainloop()
