from __future__ import annotations

import threading
from tkinter import simpledialog

import customtkinter as ctk

from .auth_bridge import AuthBridge
from .models import Account
from .paths import AppPaths
from .process import restart_grok
from .quota import QuotaProbe
from .store import AccountStore
from .usage import UsageAggregator


def _fmt_int(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


class GrokAccountManagerApp(ctk.CTk):
    def __init__(self, paths: AppPaths | None = None) -> None:
        super().__init__()
        self.paths = paths or AppPaths.default()
        self.store = AccountStore(self.paths)
        self.auth = AuthBridge(self.paths)
        self.quota = QuotaProbe()
        self.usage = UsageAggregator(self.paths)

        self.title("Grok Account Manager")
        self.geometry("960x620")
        self.minsize(820, 520)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._selected_id: str | None = None
        self._busy = False
        self._account_buttons: dict[str, ctk.CTkButton] = {}

        self._build_ui()
        self.refresh_list()
        self._set_status("就绪")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Grok Account Manager",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        btns = ctk.CTkFrame(header, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(btns, text="刷新额度", width=100, command=self.on_refresh_all_quota).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btns, text="同步统计", width=100, command=self.on_sync_usage).pack(
            side="left", padx=4
        )

        # Body
        body = ctk.CTkFrame(self)
        body.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=12, pady=12)
        self.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Left list
        left = ctk.CTkFrame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(left, text="账号列表", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4)
        )
        self.list_frame = ctk.CTkScrollableFrame(left, width=280)
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

        # Right detail
        right = ctk.CTkFrame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        self.detail_title = ctk.CTkLabel(
            right, text="选择一个账号", font=ctk.CTkFont(size=16, weight="bold")
        )
        self.detail_title.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 6))
        self.detail_text = ctk.CTkTextbox(right, height=360, wrap="word")
        self.detail_text.grid(row=1, column=0, sticky="nsew", padx=14, pady=6)
        right.grid_rowconfigure(1, weight=1)

        actions = ctk.CTkFrame(right, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 14))
        ctk.CTkButton(actions, text="切换为当前", command=self.on_switch).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="刷新额度", command=self.on_refresh_one_quota).pack(
            side="left", padx=4
        )
        ctk.CTkButton(actions, text="改名", command=self.on_rename).pack(side="left", padx=4)
        ctk.CTkButton(
            actions, text="删除", fg_color="#8B3A3A", hover_color="#6E2E2E", command=self.on_delete
        ).pack(side="left", padx=4)

        # Footer
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        footer.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            footer, text="收录当前 Grok 登录", width=180, command=self.on_capture
        ).grid(row=0, column=0, sticky="w")
        self.status = ctk.CTkLabel(footer, text="", anchor="w")
        self.status.grid(row=0, column=1, sticky="ew", padx=12)

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)

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
                if err:
                    self._set_status(f"错误: {err}")
                if on_done:
                    on_done(result, err)
                self.refresh_list(keep_selection=True)

            self.after(0, finish)

        threading.Thread(target=runner, daemon=True).start()

    def refresh_list(self, keep_selection: bool = True) -> None:
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._account_buttons.clear()
        self.store.load()
        accounts = self.store.list_accounts()
        cur = self.auth.current_identity()
        cur_uid = cur.user_id if cur else None

        if not accounts:
            ctk.CTkLabel(
                self.list_frame,
                text="暂无账号。\n请先在 Grok 登录后点「收录」。",
                justify="left",
            ).pack(anchor="w", padx=6, pady=8)

        for acc in accounts:
            is_cur = cur_uid == acc.user_id
            mark = "● " if is_cur else "○ "
            short = (
                f"{mark}{acc.label}\n"
                f"  {_fmt_tokens(acc.stats.total_tokens)} tok"
            )
            if acc.quota.remaining_requests is not None:
                short += f" · 剩 {acc.quota.remaining_requests} req"
            btn = ctk.CTkButton(
                self.list_frame,
                text=short,
                anchor="w",
                height=52,
                fg_color=("#1f538d" if acc.id == self._selected_id else "transparent"),
                border_width=1,
                command=lambda i=acc.id: self.select_account(i),
            )
            btn.pack(fill="x", pady=3, padx=2)
            self._account_buttons[acc.id] = btn

        # Unassigned line
        u = self.store.unassigned_stats
        if u.total_tokens or u.turns:
            ctk.CTkLabel(
                self.list_frame,
                text=f"未归属: {_fmt_tokens(u.total_tokens)} tok / {u.turns} 轮",
                text_color="gray",
            ).pack(anchor="w", padx=8, pady=(10, 4))

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
        for aid, btn in self._account_buttons.items():
            btn.configure(fg_color=("#1f538d" if aid == account_id else "transparent"))
        is_cur = self.auth.is_current(acc)
        self.detail_title.configure(
            text=f"{acc.label}{'  (当前)' if is_cur else ''}"
        )
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", self._format_detail(acc, is_cur))

    def _format_detail(self, acc: Account, is_cur: bool) -> str:
        q = acc.quota
        s = acc.stats
        window_req = ""
        if q.limit_requests is not None and q.remaining_requests is not None:
            used = q.limit_requests - q.remaining_requests
            window_req = f"{_fmt_int(q.remaining_requests)} / {_fmt_int(q.limit_requests)}  (已用 {_fmt_int(used)})"
        else:
            window_req = "—"
        window_tok = ""
        if q.limit_tokens is not None and q.remaining_tokens is not None:
            used_t = q.limit_tokens - q.remaining_tokens
            window_tok = f"{_fmt_int(q.remaining_tokens)} / {_fmt_int(q.limit_tokens)}  (已用 {_fmt_int(used_t)})"
        else:
            window_tok = "—"

        lines = [
            f"邮箱:        {acc.email or '—'}",
            f"User ID:     {acc.user_id}",
            f"Team ID:     {acc.team_id or '—'}",
            f"Tier:        {q.tier if q.tier is not None else '—'}",
            f"凭证过期:    {q.expires_at or acc.auth_entry.get('expires_at') or '—'}",
            f"当前登录:    {'是' if is_cur else '否'}",
            "",
            "—— 当前额度窗口（API rate limit，非终身配额）——",
            f"请求剩余:    {window_req}",
            f"Token 剩余:  {window_tok}",
            f"探测模型:    {q.model_used or '—'}",
            f"上次探测:    {q.last_probed_at or '—'}",
            f"探测错误:    {q.error or '无'}",
            "",
            "—— 本账号累计消耗（会话日志归属）——",
            f"总 Token:    {_fmt_int(s.total_tokens)}",
            f"  输入:      {_fmt_int(s.input_tokens)}",
            f"  输出:      {_fmt_int(s.output_tokens)}",
            f"  推理:      {_fmt_int(s.reasoning_tokens)}",
            f"  缓存读:    {_fmt_int(s.cached_read_tokens)}",
            f"轮次 / 调用: {_fmt_int(s.turns)} / {_fmt_int(s.model_calls)}",
            f"上次同步:    {s.last_sync_at or '—'}",
            "",
            f"收录时间:    {acc.captured_at or '—'}",
            f"最近切换:    {acc.last_used_at or '—'}",
            "",
            "说明: 切换只改 ~/.grok/auth.json，不改 sessions 与 config。",
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

    def on_switch(self) -> None:
        if not self._selected_id:
            self._set_status("请先选择账号")
            return
        acc = self.store.get(self._selected_id)
        if not acc:
            return
        if not self.store.get_setting("skip_switch_confirm", False):
            # simple confirm via dialog
            if not self._confirm(f"切换到 {acc.label}？\n将写入 auth.json 并尝试重启 Grok。"):
                return

        aid = self._selected_id

        def work():
            self.auth.switch_to(self.store, aid)
            rr = restart_grok(grok_home=self.paths.grok_home)
            return rr

        def done(result, err):
            if err:
                return
            self._set_status(result.message if result else "已切换")

        self._set_status("正在切换…")
        self._run_bg(work, done)

    def on_refresh_one_quota(self) -> None:
        if not self._selected_id:
            return
        aid = self._selected_id

        def work():
            acc = self.store.get(aid)
            if not acc:
                raise KeyError("账号不存在")
            return self.quota.probe_account(self.store, acc)

        def done(result, err):
            if err:
                return
            if result and result.error:
                self._set_status(f"额度探测完成（有错误）: {result.error}")
            else:
                self._set_status("额度已刷新")

        self._set_status("探测额度中…")
        self._run_bg(work, done)

    def on_refresh_all_quota(self) -> None:
        def work():
            errors = []
            for acc in self.store.list_accounts():
                info = self.quota.probe_account(self.store, acc)
                if info.error:
                    errors.append(f"{acc.label}: {info.error}")
            return errors

        def done(result, err):
            if err:
                return
            if result:
                self._set_status("部分失败: " + "; ".join(result)[:200])
            else:
                self._set_status("全部账号额度已刷新")

        self._set_status("正在刷新全部额度…")
        self._run_bg(work, done)

    def on_sync_usage(self) -> None:
        def work():
            return self.usage.sync(self.store, full_rebuild=True)

        def done(result, err):
            if err:
                return
            self._set_status(
                f"统计同步完成: 处理 {result.get('events_processed')} 条, "
                f"归属 {result.get('attributed')}, 未归属 {result.get('unassigned')}"
            )

        self._set_status("同步会话统计中…")
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
        if not self._confirm(f"从本地仓库删除 {acc.label}？\n不会登出 Grok / 不改 auth.json。"):
            return
        self.store.delete(self._selected_id)
        self._selected_id = None
        self.refresh_list()
        self._set_status("已从本地删除")

    def _confirm(self, message: str) -> bool:
        dialog = ctk.CTkToplevel(self)
        dialog.title("确认")
        dialog.geometry("420x160")
        dialog.transient(self)
        dialog.grab_set()
        result = {"ok": False}
        ctk.CTkLabel(dialog, text=message, justify="left", wraplength=380).pack(
            padx=16, pady=16, anchor="w"
        )
        row = ctk.CTkFrame(dialog, fg_color="transparent")
        row.pack(pady=8)

        def yes() -> None:
            result["ok"] = True
            dialog.destroy()

        def no() -> None:
            dialog.destroy()

        ctk.CTkButton(row, text="取消", width=100, command=no).pack(side="left", padx=8)
        ctk.CTkButton(row, text="确定", width=100, command=yes).pack(side="left", padx=8)
        self.wait_window(dialog)
        return result["ok"]


def run_app(paths: AppPaths | None = None) -> None:
    app = GrokAccountManagerApp(paths=paths)
    app.mainloop()
