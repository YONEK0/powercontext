"""Start an installed Docker Engine in WSL when PID 1 is not systemd."""
import os
from pathlib import Path
import shutil
import subprocess
import time

if subprocess.run(['docker', 'info'], capture_output=True).returncode == 0:
    raise SystemExit(0)
if os.geteuid() != 0:
    raise SystemExit('Docker Engine is stopped. Start Docker Desktop, or run this WSL helper as root: sudo python3 scripts/start_docker_wsl.py')
if not shutil.which('dockerd'):
    raise SystemExit('Docker Engine is not installed. See README.md prerequisites.')
runtime = Path(__file__).resolve().parents[1] / '.runtime'
runtime.mkdir(mode=0o700, exist_ok=True)
log_path = runtime / 'dockerd.log'
with log_path.open('ab') as log:
    process = subprocess.Popen(['dockerd'], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
for _ in range(45):
    if subprocess.run(['docker', 'info'], capture_output=True).returncode == 0:
        print('Started Docker Engine; log:', log_path)
        break
    if process.poll() is not None:
        raise SystemExit('Docker Engine exited; inspect ' + str(log_path))
    time.sleep(1)
else:
    raise SystemExit('Docker Engine did not become ready; inspect ' + str(log_path))
