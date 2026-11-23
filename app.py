"""Small Windows desktop UI for archiving X account media."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from core import ALLOWED_BROWSERS, build_check_command, build_command, parse_handle
from browser_session import ManagedChromeSession


def default_browser() -> str:
    """Best effort lookup of the Windows default HTTPS browser."""
    if os.name != "nt":
        return "없음"
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            prog_id = str(winreg.QueryValueEx(key, "ProgId")[0]).lower()
        for marker, browser in (("chrome", "Chrome"), ("msedge", "Edge"),
                                ("firefox", "Firefox"), ("brave", "Brave")):
            if marker in prog_id:
                return browser
    except OSError:
        pass
    return "없음"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("X 사진·영상 저장기")
        self.geometry("760x560")
        self.minsize(620, 450)
        self.handle = tk.StringVar()
        self.destination = tk.StringVar(value=str(Path.home() / "Downloads" / "X-미디어"))
        self.browser = tk.StringVar(value="프로그램 전용 Chrome")
        self.cookie_file = tk.StringVar()
        self.status = tk.StringVar(value="대기 중")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.stopping = False
        self.auth_error = False
        self.cookie_error = False
        self.checking = False
        self.session = ManagedChromeSession()
        self.temporary_files: set[Path] = set()
        self._build_ui()
        for setting in (self.handle, self.browser, self.cookie_file):
            setting.trace_add("write", self._access_setting_changed)
        self.after(100, self._poll)

    def _access_setting_changed(self, *_: object) -> None:
        if not self.checking and not self.process:
            self.status.set("계정 또는 로그인 설정이 바뀌었습니다. 다시 접근 확인을 누르세요.")

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="X 계정 사진·영상 저장", font=("맑은 고딕", 17, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))
        ttk.Label(frame, text="계정 URL 또는 아이디").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.handle).grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(frame, text="저장 폴더").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.destination).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Button(frame, text="찾기", command=self._choose_folder).grid(row=2, column=2, padx=(8, 0))
        ttk.Label(frame, text="로그인 브라우저").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Combobox(frame, textvariable=self.browser, values=["프로그램 전용 Chrome", "Chrome", "Edge", "Firefox", "Brave", "없음"], state="readonly", width=22).grid(row=3, column=1, sticky="w", pady=5)
        ttk.Button(frame, text="X 로그인 열기", command=self._open_login).grid(row=3, column=2, padx=(8, 0))
        ttk.Label(frame, text="쿠키 파일 (선택)").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.cookie_file).grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Button(frame, text="찾기", command=self._choose_cookie).grid(row=4, column=2, padx=(8, 0))
        ttk.Label(frame, text="권장: 프로그램 전용 Chrome에서 X 로그인. 창을 닫지 않고 로그인·접근 확인을 누를 수 있습니다.", wraplength=690, foreground="#555").grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 14))
        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, columnspan=3, sticky="w", pady=(0, 12))
        self.start_button = ttk.Button(actions, text="저장 시작", command=self._start)
        self.start_button.pack(side="left")
        self.check_button = ttk.Button(actions, text="로그인·접근 확인", command=self._check_access)
        self.check_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(actions, text="중단", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(actions, text="저장 폴더 열기", command=self._open_folder).pack(side="left")
        ttk.Label(frame, textvariable=self.status).grid(row=7, column=0, columnspan=3, sticky="w")
        frame.rowconfigure(8, weight=1)
        self.log = scrolledtext.ScrolledText(frame, height=12, state="disabled", wrap="word")
        self.log.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(initialdir=self.destination.get())
        if path:
            self.destination.set(path)

    def _choose_cookie(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("쿠키 파일", "*.txt"), ("모든 파일", "*.*")])
        if path:
            self.cookie_file.set(path)
            self.browser.set("없음")

    def _open_login(self) -> None:
        if self.browser.get() == "프로그램 전용 Chrome":
            try:
                self.session.open_login()
                self.status.set("전용 Chrome 창에서 X 로그인 후 '로그인·접근 확인'을 누르세요.")
            except (OSError, RuntimeError) as exc:
                messagebox.showerror("로그인 창", str(exc))
        else:
            webbrowser.open("https://x.com/i/flow/login")
            self.status.set("선택한 브라우저에서 X 로그인 후 접근 확인을 누르세요.")

    def _auth_selection(self) -> tuple[str, str, Path | None]:
        browser = self.browser.get()
        cookie_file = self.cookie_file.get().strip()
        if browser == "프로그램 전용 Chrome":
            temporary = self.session.export_cookie_file()
            self.temporary_files.add(temporary)
            return "없음", str(temporary), temporary
        return browser, cookie_file, None

    def _account_folder(self) -> Path:
        handle = parse_handle(self.handle.get())
        root = Path(self.destination.get().strip()).expanduser()
        if not str(root).strip() or not self.destination.get().strip():
            raise ValueError("저장 폴더를 입력하세요.")
        return root / handle

    def _check_access(self) -> None:
        if self.checking or self.process:
            return
        try:
            handle = parse_handle(self.handle.get())
            browser, cookie_file, temporary = self._auth_selection()
            command = build_check_command(handle, browser, cookie_file)
        except (ValueError, RuntimeError, OSError) as exc:
            messagebox.showerror("입력 확인", str(exc))
            return
        self.checking = True
        self.check_button.config(state="disabled")
        self.start_button.config(state="disabled")
        self.status.set(f"@{handle} 접근 확인 중...")
        settings = (self.handle.get(), self.browser.get(), self.cookie_file.get())
        threading.Thread(target=self._run_check, args=(command, handle, settings, temporary), daemon=True).start()

    def _run_check(self, command: list[str], handle: str, settings: tuple[str, str, str], temporary: Path | None) -> None:
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=45, creationflags=creationflags)
            output = (result.stdout + "\n" + result.stderr).strip()
            self.events.put(("check", (handle, result.returncode, output, settings)))
        except subprocess.TimeoutExpired:
            self.events.put(("check", (handle, -1, "45초 안에 응답하지 않았습니다.", settings)))
        except Exception as exc:
            self.events.put(("check", (handle, -1, str(exc), settings)))
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
                self.temporary_files.discard(temporary)

    def _start(self) -> None:
        try:
            folder = self._account_folder()
            browser, cookie_file, temporary = self._auth_selection()
            if browser not in ALLOWED_BROWSERS:
                raise ValueError("로그인 브라우저를 선택하세요.")
            command = build_command(folder.name, folder, browser, cookie_file)
            folder.mkdir(parents=True, exist_ok=True)
        except (ValueError, RuntimeError, OSError) as exc:
            if "temporary" in locals() and temporary:
                temporary.unlink(missing_ok=True)
                self.temporary_files.discard(temporary)
            messagebox.showerror("입력 확인", str(exc))
            return
        self.stopping = False
        self.auth_error = False
        self.cookie_error = False
        self.status.set(f"저장 중: @{folder.name}")
        self.start_button.config(state="disabled")
        self.check_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self._append(f"저장 위치: {folder}\n")
        self._append("X 계정의 미디어 게시물을 찾는 중입니다. 게시물 수가 많으면 시간이 걸립니다.\n")
        threading.Thread(target=self._run, args=(command, temporary), daemon=True).start()

    def _run(self, command: list[str], temporary: Path | None) -> None:
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", bufsize=1,
                                       creationflags=creationflags)
            self.process = process
            if self.stopping:
                process.terminate()
            assert process.stdout is not None
            for line in process.stdout:
                self.events.put(("log", line))
            code = process.wait()
            self.events.put(("done", code))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.process = None
            if temporary:
                temporary.unlink(missing_ok=True)
                self.temporary_files.discard(temporary)

    def _stop(self) -> None:
        self.stopping = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.status.set("중단 중...")

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    line = str(value)
                    if "AuthRequired" in line:
                        self.auth_error = True
                    if "cookies:" in line and ("Permission denied" in line or "access is denied" in line.lower()):
                        self.cookie_error = True
                    self._append(line)
                elif kind == "check":
                    handle, code, output, settings = value
                    self.checking = False
                    self.check_button.config(state="normal")
                    self.start_button.config(state="normal")
                    if settings != (self.handle.get(), self.browser.get(), self.cookie_file.get()):
                        self.status.set("확인 중 설정이 바뀌었습니다. 다시 접근 확인을 누르세요.")
                    elif code == 0:
                        self.status.set(f"접근 확인됨: @{handle} 미디어를 읽을 수 있습니다.")
                    elif "Permission denied" in output and "cookies:" in output:
                        self.status.set("브라우저 쿠키 접근 실패. 브라우저를 완전히 종료하거나 cookies.txt를 선택하세요.")
                    elif "AuthRequired" in output:
                        self.status.set("로그인 확인 실패. X 로그인 브라우저 또는 cookies.txt를 선택하세요.")
                    else:
                        self.status.set("접근 확인 실패. 아래 로그를 확인하세요.")
                    if code != 0:
                        self._append(f"[접근 확인] {output[-1500:]}\n")
                else:
                    self.start_button.config(state="normal")
                    self.check_button.config(state="normal")
                    self.stop_button.config(state="disabled")
                    if kind == "error":
                        self.status.set("오류")
                        self._append(f"오류: {value}\n")
                    elif self.stopping:
                        self.status.set("중단됨. 다시 시작하면 기존 파일은 건너뜁니다.")
                    elif value == 0:
                        self.status.set("완료. 저장 폴더를 확인하세요.")
                    elif self.cookie_error:
                        self.status.set("브라우저 쿠키 접근 실패. 브라우저를 완전히 종료하거나 cookies.txt를 선택하세요.")
                    elif self.auth_error:
                        self.status.set("X 로그인이 필요합니다. 로그인 브라우저 또는 cookies.txt를 선택하세요.")
                    else:
                        self.status.set(f"완료되지 않음 (종료 코드 {value}). 아래 로그를 확인하세요.")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _append(self, message: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", message)
        self.log.see("end")
        self.log.config(state="disabled")

    def _open_folder(self) -> None:
        try:
            path = self._account_folder()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except (ValueError, OSError) as exc:
            messagebox.showerror("폴더 열기", str(exc))

    def _close(self) -> None:
        self._stop()
        for temporary in tuple(self.temporary_files):
            temporary.unlink(missing_ok=True)
        self.session.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
