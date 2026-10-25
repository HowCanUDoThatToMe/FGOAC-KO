"""Offline installer window using the bundled standard Python/Tk runtime."""
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from offline_support import verify_bundle


class Installer:
    def __init__(self, root):
        self.root = root
        self.package = Path(__file__).resolve().parent.parent
        self.events = queue.Queue()
        self.running = False
        self.cancel_path = None
        root.title('FGO Arcade 한글패치 · 오프라인 설치')
        root.geometry('920x550')
        root.minsize(880, 480)
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='FGO Arcade 한글패치', font=('맑은 고딕', 18, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='인터넷 연결 없이 설치합니다. 받은 ZIP 파일을 모두 같은 폴더에 풀어 주세요.').pack(anchor='w', pady=(8, 16))
        row = ttk.Frame(frame); row.pack(fill='x')
        self.path = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.path)
        self.entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.choose = ttk.Button(row, text='게임 폴더 선택', command=self.choose_folder)
        self.choose.pack(side='right')
        ttk.Label(frame, text='App과 Server 폴더가 함께 들어 있는 게임 폴더를 선택하세요.').pack(anchor='w', pady=(6, 14))
        controls = ttk.Frame(frame); controls.pack(fill='x')
        self.buttons = []
        for label, action in [('설치 / 업데이트', 'install'), ('상태 확인', 'status'), ('한글 게임 실행', 'launch'), ('제거', 'uninstall'), ('중단 복구', 'recover'), ('자료 검사', 'verify')]:
            button = ttk.Button(controls, text=label, command=lambda a=action: self.start(a))
            button.pack(side='left', padx=(0, 7)); self.buttons.append(button)
        self.stop = ttk.Button(controls, text='취소', command=self.cancel, state='disabled')
        self.stop.pack(side='right')
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.pack(fill='x', pady=12)
        self.output = scrolledtext.ScrolledText(frame, height=15, state='disabled', font=('맑은 고딕', 10))
        self.output.pack(fill='both', expand=True)
        self.write('전체 자료를 내려받은 뒤 사용할 수 있는 오프라인 설치기입니다.\n설치 후에는 기존 로컬 플랫폼의 서버 시작 → 게임 시작을 사용하세요.\n설치·업데이트·제거 전에는 플랫폼·게임·서버를 모두 종료하세요.\n게임은 자동으로 실행하지 않습니다.\n')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def write(self, text):
        self.output.configure(state='normal'); self.output.insert('end', text)
        self.output.see('end'); self.output.configure(state='disabled')

    def choose_folder(self):
        selected = filedialog.askdirectory(title='App과 Server가 있는 게임 폴더 선택', mustexist=True)
        if selected: self.path.set(selected)

    def start(self, action):
        if self.running: return
        target = self.path.get().strip()
        if action != 'verify' and not target:
            messagebox.showinfo('게임 폴더 선택', '게임 폴더를 먼저 선택하세요.'); return
        if action in ('install', 'uninstall', 'recover'):
            name = {'install':'설치 / 업데이트', 'uninstall':'제거', 'recover':'중단 복구'}[action]
            notice = ''
            if action == 'install':
                notice = ('\n\n동봉 engine/licenses의 구성품 고지와 사용 조건을 확인해 주세요.\n'
                          'Python LICENSE의 Microsoft 구성품 조건을 포함합니다.\n'
                          '조건에 동의하고 설치하려면 확인을 누르세요.')
            if not messagebox.askokcancel('작업 확인', name + ' 작업을 진행합니다.\n\n' + target + notice): return
        self.running = True
        for button in self.buttons + [self.choose, self.entry]: button.configure(state='disabled')
        self.stop.configure(state='normal'); self.progress.start()
        self.write('\n작업을 시작합니다.\n')
        threading.Thread(target=self.worker, args=(action, target), daemon=True).start()

    def worker(self, action, target):
        code = 2
        try:
            verify_bundle(self.package)
            with tempfile.TemporaryDirectory(prefix='fgoac-offline-cancel-') as temp:
                self.cancel_path = Path(temp) / 'cancel'
                command = [sys.executable, '-I', '-X', 'utf8', '-B', str(Path(__file__).with_name('engine.py')), action,
                           '--package', str(self.package), '--cancel', str(self.cancel_path)]
                if target: command += ['--target', target]
                with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding='utf-8', errors='replace') as process:
                    for line in process.stdout: self.events.put(('text', line))
                    code = process.wait()
        except Exception as error:
            self.events.put(('text', '오류: ' + str(error) + '\n'))
        finally:
            self.cancel_path = None
            self.events.put(('done', code))

    def cancel(self):
        if self.cancel_path:
            self.cancel_path.write_text('cancel', encoding='ascii')
            self.write('취소를 요청했습니다. 현재 단계가 안전하게 끝날 때까지 기다려 주세요.\n')

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'text': self.write(value)
            else:
                self.running = False; self.progress.stop(); self.stop.configure(state='disabled')
                for button in self.buttons + [self.choose, self.entry]: button.configure(state='normal')
                self.write('작업 완료.\n' if value == 0 else '작업이 완료되지 않았습니다. 위 내용을 확인하세요.\n')
        self.root.after(100, self.poll)

    def close(self):
        if self.running:
            messagebox.showinfo('작업 진행 중', '취소 버튼을 누른 뒤 작업이 끝날 때까지 기다려 주세요.')
        else: self.root.destroy()


def main():
    runtime = Path(sys.executable).parent
    os.environ['TCL_LIBRARY'] = str(runtime / 'tcl/tcl8.6')
    os.environ['TK_LIBRARY'] = str(runtime / 'tcl/tk8.6')
    root = tk.Tk(); app = Installer(root)
    if '--self-test' in sys.argv:
        root.withdraw(); root.update_idletasks()
        assert len(app.buttons) == 6 and not app.running
        root.destroy(); print('OFFLINE_GUI_SELF_TEST_PASSED'); return
    root.mainloop()


if __name__ == '__main__': main()
