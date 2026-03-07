#!/opt/forge-venv/bin/python3
import os
import time
import json
import sqlite3
import subprocess
import signal
import sys
import shutil
from datetime import datetime
# Use local imports of rich/typer from the venv; this script is executed with the venv python
import typer
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))
app = typer.Typer()
console = Console()
DB = os.getenv('DATA_DIR', '/data') + '/forge.db'
PID_FILE = os.getenv('WORKSPACE_DIR', '/workspace') + '/forge.pid'
# import DB helper for audit logging from CLI actions
try:
    from memory.database import db as audit_db
except Exception:
    audit_db = None


def db_conn():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _run_systemctl(action: str) -> (bool, bool):
    """Attempt to control a systemd 'forge' service.
    Returns (invoked, success). invoked==True means systemctl was present and called.
    success==True means the systemctl command returned exit code 0.
    """
    if not shutil.which('systemctl'):
        return (False, False)
    try:
        subprocess.run(['systemctl', action, 'forge'], check=True)
        console.print(f'systemctl {action} forge -> success')
        return (True, True)
    except subprocess.CalledProcessError as e:
        console.print(f'systemctl {action} forge -> failed: {e}')
        return (True, False)


def _find_forge_pids():
    """Find PIDs of running forge/uvicorn processes.
    Strategy:
      1. Try pgrep -f "web.server:create_app" (specific)
      2. Fallback to scanning /proc and matching cmdline containing 'web.server:create_app' or 'uvicorn' and the repo path
    """
    pids = []
    # 1) pgrep
    if shutil.which('pgrep'):
        try:
            out = subprocess.run(['pgrep', '-f', 'web.server:create_app'], capture_output=True, text=True, check=False)
            if out.returncode == 0 and out.stdout.strip():
                for line in out.stdout.splitlines():
                    line = line.strip()
                    if line.isdigit():
                        pids.append(int(line))
                return pids
        except Exception:
            pass

    # 2) /proc scan (portable Linux fallback)
    repo_dir = os.path.abspath(os.path.dirname(__file__))
    if os.path.isdir('/proc'):
        for entry in os.listdir('/proc'):
            if not entry.isdigit():
                continue
            pid = int(entry)
            try:
                with open(f'/proc/{pid}/cmdline', 'rb') as f:
                    data = f.read().replace(b'\x00', b' ').decode(errors='ignore')
                if 'web.server:create_app' in data:
                    pids.append(pid)
                    continue
                # uvicorn might be generic; ensure it looks like our repo
                if 'uvicorn' in data and repo_dir in data:
                    pids.append(pid)
            except Exception:
                continue
    return sorted(set(pids))


def _signal_and_wait(pids, sig, timeout=10):
    remaining = set(pids)
    for pid in list(remaining):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            remaining.discard(pid)
        except PermissionError:
            console.print(f'No permission to signal pid {pid}')
    # wait until gone
    end = time.time() + timeout
    while remaining and time.time() < end:
        for pid in list(remaining):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remaining.discard(pid)
            except PermissionError:
                # still exists but we can't check; remove to avoid infinite loop
                remaining.discard(pid)
        time.sleep(0.2)
    return list(remaining)


@app.command()
def start(detach: bool = typer.Option(True, help='Run in background when starting without systemd')):
    """Start the forge service/app.

    Prefers systemctl start when systemd is available. Otherwise spawns uvicorn in the background
    and writes a pid file to WORKSPACE_DIR/forge.pid.
    """
    invoked, success = _run_systemctl('start')
    if invoked:
        if success:
            console.print('Requested systemd start.')
            try:
                if audit_db:
                    audit_db.write_log('cli','start','systemctl start invoked',payload={'systemctl':True})
            except Exception:
                pass
            return
        else:
            console.print('systemctl start failed; falling back to direct start')
    # Fallback: spawn uvicorn (prefer venv path, then PATH)
    uvicorn_candidates = ['/opt/forge-venv/bin/uvicorn']
    uvicorn_exe = None
    for c in uvicorn_candidates:
        if os.path.exists(c) and os.access(c, os.X_OK):
            uvicorn_exe = c
            break
    if not uvicorn_exe:
        uvicorn_exe = shutil.which('uvicorn')
    if not uvicorn_exe:
        console.print('No uvicorn executable found (tried /opt/forge-venv/bin/uvicorn and PATH). Cannot start.')
        raise typer.Exit(code=1)
    cmd = [uvicorn_exe, 'web.server:create_app', '--host', '0.0.0.0', '--port', '7860', '--log-level', 'info']
    # run in background
    stdout = open(os.getenv('WORKSPACE_DIR','/workspace') + '/forge.out.log', 'a')
    stderr = open(os.getenv('WORKSPACE_DIR','/workspace') + '/forge.err.log', 'a')
    try:
        if detach:
            p = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, cwd=os.path.dirname(__file__), start_new_session=True)
            with open(PID_FILE, 'w') as f:
                f.write(str(p.pid))
            console.print(f'Started uvicorn as pid {p.pid}; pidfile at {PID_FILE}')
            try:
                if audit_db:
                    audit_db.write_log('cli', 'start', 'uvicorn started (fallback)', payload={'pid': p.pid, 'pidfile': PID_FILE})
            except Exception:
                pass
        else:
            # foreground
            subprocess.run(cmd)
    except Exception as e:
        console.print(f'Failed to start uvicorn: {e}')
        try:
            if audit_db:
                audit_db.write_log('cli', 'start_failed', str(e))
        except Exception:
            pass
        raise typer.Exit(code=1)


@app.command()
def stop(force: bool = typer.Option(False, help='Force-stop if regular stop does not work')):
    """Stop the running forge service/app.

    Tries systemctl stop forge if available. Otherwise finds running uvicorn/web.server processes
    that belong to this repo and sends SIGTERM (then SIGKILL if --force).
    """
    invoked, success = _run_systemctl('stop')
    if invoked and success:
        console.print('Requested systemd stop (may require root).')
        try:
            if audit_db:
                audit_db.write_log('cli','stop','systemctl stop invoked', payload={'systemctl': True})
        except Exception:
            pass
        return
    # If systemctl exists but failed, fall back after warning
    if invoked and not success:
        console.print('systemctl stop failed or returned non-zero; falling back to process kill')

    pids = _find_forge_pids()
    # Also try pidfile
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            if pid and pid not in pids:
                pids.append(pid)
    except Exception:
        pass

    if not pids:
        console.print('No forge/uvicorn process found to stop.')
        # remove stale pidfile
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except Exception:
            pass
        return

    console.print(f'Sending SIGTERM to PIDs: {pids}')
    remaining = _signal_and_wait(pids, signal.SIGTERM, timeout=10)
    if remaining:
        console.print(f'Processes still running after SIGTERM: {remaining}')
        try:
            if audit_db:
                audit_db.write_log('cli','stop','sigterm_failed', payload={'pids': remaining})
        except Exception:
            pass
        if force:
            console.print('Sending SIGKILL to remaining processes')
            _signal_and_wait(remaining, signal.SIGKILL, timeout=3)
            try:
                if audit_db:
                    audit_db.write_log('cli','stop','sigkill_sent', payload={'pids': remaining})
            except Exception:
                pass
        else:
            console.print('Use --force to send SIGKILL to remaining processes')
    else:
        console.print('Stopped successfully')
        try:
            if audit_db:
                audit_db.write_log('cli','stop','stopped', payload={'pids': pids})
        except Exception:
            pass
    # cleanup pidfile
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


@app.command()
def pause():
    """Pause (SIGSTOP) the running forge processes."""
    invoked = False
    if shutil.which('systemctl'):
        # Try to use systemctl kill to send SIGSTOP to the service
        try:
            subprocess.run(['systemctl', 'kill', '-s', 'SIGSTOP', 'forge'], check=True)
            invoked = True
            console.print('Sent SIGSTOP via systemctl (may require root).')
            try:
                if audit_db:
                    audit_db.write_log('cli','pause','systemctl_sigstop')
            except Exception:
                pass
        except subprocess.CalledProcessError as e:
            console.print(f'systemctl SIGSTOP failed: {e}')
    if invoked:
        return
    pids = _find_forge_pids()
    if not pids:
        console.print('No forge/uvicorn process found to pause.')
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGSTOP)
            console.print(f'Paused pid {pid}')
        except Exception as e:
            console.print(f'Failed to pause pid {pid}: {e}')


@app.command()
def resume():
    """Resume (SIGCONT) previously paused forge processes."""
    invoked = False
    if shutil.which('systemctl'):
        try:
            subprocess.run(['systemctl', 'kill', '-s', 'SIGCONT', 'forge'], check=True)
            invoked = True
            console.print('Sent SIGCONT via systemctl (may require root).')
            try:
                if audit_db:
                    audit_db.write_log('cli','resume','systemctl_sigcont')
            except Exception:
                pass
        except subprocess.CalledProcessError as e:
            console.print(f'systemctl SIGCONT failed: {e}')
    if invoked:
        return
    pids = _find_forge_pids()
    if not pids:
        console.print('No forge/uvicorn process found to resume.')
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGCONT)
            console.print(f'Resumed pid {pid}')
        except Exception as e:
            console.print(f'Failed to resume pid {pid}: {e}')


@app.command()
def pause_app():
    """Application-level pause: set maintenance flag so agents stop creating outbound API calls and stop taking new tasks."""
    conn = db_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO checkpoints (key, value) VALUES ('maintenance','1')")
    conn.commit()
    console.print('Set application maintenance=1')
    try:
        if audit_db:
            audit_db.write_log('cli','pause_app','set maintenance')
    except Exception:
        pass


@app.command()
def resume_app():
    """Clear application-level maintenance flag."""
    conn = db_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO checkpoints (key, value) VALUES ('maintenance','')")
    conn.commit()
    console.print('Cleared application maintenance flag')
    try:
        if audit_db:
            audit_db.write_log('cli','resume_app','cleared maintenance')
    except Exception:
        pass


@app.command()
def maintenance_status():
    """Show application maintenance status."""
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM checkpoints WHERE key='maintenance'")
    row = c.fetchone()
    val = row[0] if row else ''
    console.print(f'maintenance={val}')


@app.command()
def status():
    """Show current task board and agent states"""
    conn = db_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM projects')
    projects = c.fetchall()
    table = Table(title='Projects')
    table.add_column('id')
    table.add_column('name')
    table.add_column('status')
    table.add_column('updated_at')
    for p in projects:
        table.add_row(str(p['id']), p['name'], p['status'], p['updated_at'])
    console.print(table)
    c.execute('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 10')
    tasks = c.fetchall()
    t2 = Table(title='Recent Tasks')
    t2.add_column('id')
    t2.add_column('desc')
    t2.add_column('assigned_to')
    t2.add_column('status')
    for t in tasks:
        t2.add_row(str(t['id']), t['description'][:50], t['assigned_to'] or '-', t['status'])
    console.print(t2)


@app.command()
def logs(agent: str = typer.Option(None, help='Agent name to tail')):
    """Tail logs for a specific agent or all agents"""
    log_dir = os.getenv('WORKSPACE_DIR', '/workspace') + '/logs'
    if agent:
        path = f"{log_dir}/{agent}.log"
        if not os.path.exists(path):
            console.print(f"No log for agent {agent}")
            raise typer.Exit(code=1)
        with open(path) as f:
            for line in f:
                console.print(line.strip())
    else:
        # print all logs
        if not os.path.isdir(log_dir):
            console.print('No logs found')
            raise typer.Exit()
        for fname in os.listdir(log_dir):
            console.print(f"--- {fname} ---")
            with open(os.path.join(log_dir, fname)) as f:
                for line in f:
                    console.print(line.strip())


@app.command()
def task(description: str):
    """Send a task to Henry"""
    conn = db_conn()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute('INSERT INTO tasks (description, assigned_to, status, created_at, updated_at) VALUES (?,?,?,?,?)', (description, 'henry', 'pending', now, now))
    conn.commit()
    console.print('Task sent to Henry')


@app.command()
def chat():
    """Open a simple interactive chat with Henry. Type \"exit\" to quit."""
    console.print('Entering chat with Henry. Type "exit" to quit.')
    conn = db_conn()
    c = conn.cursor()
    while True:
        msg = console.input('You: ')
        if msg.strip().lower() in ('exit','quit'):
            break
        now = datetime.utcnow().isoformat()
        c.execute('INSERT INTO messages (sender, recipient, content, created_at) VALUES (?,?,?,?)', ('user','henry',msg,now))
        conn.commit()
        # trigger Henry by updating a wakeup flag
        c.execute("UPDATE checkpoints SET value = ? WHERE key = 'wake_signal'", (now,))
        conn.commit()
        time.sleep(1)
        c.execute('SELECT content FROM messages WHERE sender = "henry" ORDER BY created_at DESC LIMIT 1')
        row = c.fetchone()
        if row:
            console.print('[Henry] ' + row[0])
        else:
            console.print('[Henry] (no response yet)')


@app.command()
def skills():
    """List registered skills"""
    conn = db_conn()
    c = conn.cursor()
    c.execute('SELECT * FROM skills')
    skills = c.fetchall()
    table = Table(title='Skills')
    table.add_column('id')
    table.add_column('name')
    table.add_column('description')
    for s in skills:
        table.add_row(str(s['id']), s['name'], s['description'][:80])
    console.print(table)


@app.command()
def uninstall(purge_data: bool = typer.Option(False, '--purge-data', help='Remove data directories and database'), remove_venv: bool = typer.Option(False, '--remove-venv', help='Remove virtualenv (if created at /opt/forge-venv)'), remove_systemd: bool = typer.Option(False, '--remove-systemd', help='If a systemd unit is installed, disable and remove it'), yes: bool = typer.Option(False, '--yes', '-y', help='Do not prompt for confirmation (use with caution)'), dry_run: bool = typer.Option(False, '--dry-run', help='Show what would be removed but do not delete')):
    """Uninstall FORGE. Stops services and optionally removes data, venv, and systemd unit files.

    This command follows common CLI best-practices:
      - prompts for confirmation unless --yes is passed
      - supports a dry-run mode that prints all actions without performing them
      - offers a choice to keep or purge user data
    """
    console.print('[bold]FORGE Uninstall[/bold]\n')
    actions = []
    # Stop service first
    console.print('Stopping any running forge service/processes...')
    try:
        # prefer systemctl stop if available
        invoked, success = _run_systemctl('stop')
        if invoked and success:
            actions.append('systemctl stop forge')
        else:
            # call internal stop routine to kill processes
            stop(force=True)
            actions.append('stopped uvicorn processes')
    except Exception as e:
        console.print(f'[red]Warning stopping service:[/red] {e}')
    # Systemd unit removal
    if remove_systemd:
        unit_path = '/etc/systemd/system/forge.service'
        console.print('\n[bold]Systemd unit removal requested[/bold]')
        if dry_run:
            console.print(f'Would disable and remove systemd unit at {unit_path} (if present)')
        else:
            if shutil.which('systemctl'):
                try:
                    subprocess.run(['systemctl', 'disable', '--now', 'forge'], check=False)
                    console.print('systemctl disable --now forge (attempted)')
                except Exception:
                    pass
            if os.path.exists(unit_path):
                try:
                    os.remove(unit_path)
                    console.print(f'Removed {unit_path}')
                    # reload systemd
                    if shutil.which('systemctl'):
                        subprocess.run(['systemctl', 'daemon-reload'], check=False)
                except Exception as e:
                    console.print(f'Failed to remove {unit_path}: {e}')
            else:
                console.print(f'No unit file at {unit_path}')
        actions.append('remove systemd unit')
    # Prepare list of data paths
    workspace = os.getenv('WORKSPACE_DIR', '/workspace')
    data_dir = os.getenv('DATA_DIR', '/data')
    db_path = data_dir.rstrip('/') + '/forge.db'
    logs_dir = os.path.join(workspace, 'logs')
    pidfile = os.getenv('WORKSPACE_DIR', '/workspace') + '/forge.pid'
    venv_path = '/opt/forge-venv'

    removal_items = []
    if purge_data:
        removal_items.extend([db_path, data_dir, logs_dir])
    if remove_venv:
        removal_items.append(venv_path)
    # always suggest removing pidfile and logs/out files
    removal_items.extend([pidfile, os.path.join(workspace, 'forge.out.log'), os.path.join(workspace, 'forge.err.log')])

    # Deduplicate and filter non-empty
    removal_items = [p for p in sorted(set(removal_items)) if p]

    if dry_run:
        console.print('\n[bold]Dry run mode - the following items would be removed:[/bold]')
        for p in removal_items:
            console.print('  - ' + p)
        console.print('\nNo changes made.')
        raise typer.Exit()

    if not yes:
        console.print('\nThe following paths will be removed:')
        for p in removal_items:
            console.print('  - ' + p)
        console.print('\nType [bold red]yes[/bold red] to confirm and continue, or anything else to abort:')
        choice = console.input('> ')
        if choice.strip().lower() != 'yes':
            console.print('Aborting uninstall')
            raise typer.Exit()

    # Backup DB if user chose to purge data and DB exists
    if purge_data and os.path.exists(db_path):
        try:
            backup_path = f'/tmp/forge_db_backup_{int(time.time())}.db'
            shutil.copy2(db_path, backup_path)
            console.print(f'Backed up database to {backup_path}')
        except Exception as e:
            console.print(f'Failed to backup DB: {e}')
    # Perform deletions
    for p in removal_items:
        try:
            if not os.path.exists(p):
                console.print(f'Not found: {p}')
                continue
            if os.path.isfile(p) or os.path.islink(p):
                os.remove(p)
                console.print(f'Removed file: {p}')
            elif os.path.isdir(p):
                shutil.rmtree(p)
                console.print(f'Removed dir: {p}')
            else:
                console.print(f'Unknown path type, skipping: {p}')
        except Exception as e:
            console.print(f'Failed to remove {p}: {e}')
    # Optionally remove systemd unit in repo path (local copy)
    repo_unit = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'systemd', 'forge.service')
    try:
        if os.path.exists(repo_unit):
            console.print(f'Local unit file in repo: {repo_unit} (left in place)')
    except Exception:
        pass

    console.print('\nUninstall complete.')
    try:
        if audit_db:
            audit_db.write_log('cli','uninstall','uninstalled', payload={'purge_data': purge_data, 'remove_venv': remove_venv, 'remove_systemd': remove_systemd})
    except Exception:
        pass

if __name__ == '__main__':
    app()
