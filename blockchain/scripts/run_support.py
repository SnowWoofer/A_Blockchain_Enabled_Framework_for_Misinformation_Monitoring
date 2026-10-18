"""Run-scoped diagnostics and exclusive access to a named benchmark deployment."""
from contextlib import contextmanager
import datetime as dt
import fcntl
import os
from pathlib import Path
import sys
import threading
import traceback
import uuid


class RunLog:
    def __init__(self, root, name, kind, verbose=False):
        stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')
        self.folder=Path(root)/'results/repair'/name/f'{kind}-{stamp}-{uuid.uuid4().hex[:8]}'
        self.folder.mkdir(parents=True,exist_ok=False)
        self.path=self.folder/'logs.txt';self.verbose=verbose
        self.stage='initialisation';self._stop=threading.Event()

    def __enter__(self):
        sys.stdout.flush();sys.stderr.flush()
        self.console=os.fdopen(os.dup(1),'w',buffering=1)
        self.saved=(os.dup(1),os.dup(2))
        self.handle=self.path.open('w',buffering=1);self.path.chmod(0o600)
        os.dup2(self.handle.fileno(),1);os.dup2(self.handle.fileno(),2)
        if self.verbose:
            self.reader=threading.Thread(target=self._tail,daemon=True);self.reader.start()
        self.say(f'Logs: {self.path.relative_to(self.folder.parents[3])}')
        return self

    def _tail(self):
        with self.path.open() as handle:
            while not self._stop.wait(.1):
                content=handle.read()
                if content:self.console.write(content)
            self.console.write(handle.read())

    def say(self,message):
        if not self.verbose:print(message,file=self.console,flush=True)
        print(message,flush=True)

    def step(self,message):
        self.stage=message;self.say(message)

    def __exit__(self,kind,error,tb):
        if error:
            traceback.print_exception(kind,error,tb)
            self.console.write(f'FAILED during {self.stage} ({kind.__name__}). See {self.path}\n')
        sys.stdout.flush();sys.stderr.flush();self._stop.set()
        if self.verbose:self.reader.join(timeout=2)
        os.dup2(self.saved[0],1);os.dup2(self.saved[1],2)
        for fd in self.saved:os.close(fd)
        self.handle.close();self.console.close()
        return False


@contextmanager
def benchmark_lock(runtime):
    with (runtime/'.benchmark.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('another feed or Caliper benchmark is using this deployment') from None
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)
