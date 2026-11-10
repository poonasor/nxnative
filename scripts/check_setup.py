#!/usr/bin/env python3
"""Check this Mac for everything a Mac-only build needs, and report every problem at once with the fix for each.

    python3 scripts/check_setup.py [NSP_FOLDER_OR_NUP]

build_ipa_from_nsp.py checks the same things, but it stops at the first one that fails and only after it has started. This reads only: it installs and changes
nothing, and exits 0 when everything it needs is there, 1 otherwise. It runs under any Python 3.9+, even one without the packages the build needs (that is one of the checks).
An optional game folder or .nup is checked for being there, and the build folder's disk space is taken from where it would go.
"""
import importlib.util, os, platform, re, shutil, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NEED_FREE_GB = 100          # the game, its build, shader caches and the finished app (the README's figure for Ocarina of Time)
NEED_RAM_GB = 16
MIN_MACOS = (26, 6)         # building and playing (the README's figure)
PY_MODULES = [('lz4.block', 'lz4'), ('capstone', 'capstone>=5,<6'), ('fontTools', 'fonttools'), ('zbic', 'zbic'), ('numpy', 'numpy'), ('PIL', 'pillow')]
BREW_TOOLS = {'zstd': 'zstd', 'glslangValidator': 'glslang'}

results = []   # (status, name, detail, fix)


def add(ok, name, detail='', fix='', warn=False):
    results.append(('ok' if ok else 'warn' if warn else 'FAIL', name, detail, fix))
    return ok


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors='replace')
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def vtuple(s):
    return tuple(int(x) for x in re.findall(r'\d+', s)[:3])


def check_machine():
    if not add(sys.platform == 'darwin', 'macOS', sys.platform, 'the Mac host runs only on macOS'): return
    ver = vtuple(platform.mac_ver()[0])
    add(ver >= MIN_MACOS, f'macOS {".".join(map(str, MIN_MACOS))} or later', platform.mac_ver()[0], 'update macOS (System Settings → General → Software Update)')
    arm = platform.machine() == 'arm64'
    code, out = run(['sysctl', '-n', 'sysctl.proc_translated'])
    rosetta = out.strip() == '1'
    add(arm and not rosetta, 'Apple silicon, native Python', f'machine={platform.machine()}{" (under Rosetta)" if rosetta else ""}',
        'the host runs the game\'s arm64 code: use an Apple silicon Mac and an arm64 Python (/usr/bin/python3, or Homebrew\'s under /opt/homebrew)')
    code, out = run(['sysctl', '-n', 'hw.memsize'])
    gb = int(out) / 2**30 if code == 0 and out.isdigit() else 0
    add(gb >= NEED_RAM_GB, f'{NEED_RAM_GB} GB of memory or more', f'{gb:.0f} GB', 'the game\'s footprint is about 10 GB: a Mac with less will swap heavily', warn=gb >= 8)


def check_disk(where):
    p = where if where.exists() else where.parent
    while not p.exists() and p != p.parent: p = p.parent
    free = shutil.disk_usage(p).free / 2**30
    add(free >= NEED_FREE_GB, f'{NEED_FREE_GB} GB free for the build', f'{free:.0f} GB free on the disk of {p}', 'free up space, or build on another drive (the work folder is <NSP folder>/<title ID>_build)')


def check_xcode():
    code, out = run(['xcode-select', '-p'])
    dev = out if code == 0 else ''
    if not add(code == 0 and 'CommandLineTools' not in dev, 'Xcode selected', dev or 'no developer folder', 'install Xcode, open it once, then: sudo xcode-select -s /Applications/Xcode.app'):
        return
    code, out = run(['xcodebuild', '-version'])
    if code != 0:
        add(False, 'xcodebuild runs', out.splitlines()[-1] if out else '', 'open Xcode once and accept its licence: sudo xcodebuild -license accept')
        return
    add(True, 'Xcode', out.splitlines()[0])
    code, out = run(['xcrun', '-sdk', 'macosx', 'metal', '--version'])
    have = out.splitlines()[0] if out else ''
    if not add(code == 0, 'Metal compiler', have or 'does not run', 'xcodebuild -downloadComponent MetalToolchain'): return
    # the shader libraries are tested with one Metal compiler (dm/gfx/tools/dmsh_chain.lock): another builds only with --allow-other-metal and can render wrong pictures
    lock = REPO / 'dm/gfx/tools/dmsh_chain.lock'
    want = next((l.split('\t', 1)[1].strip() for l in lock.read_text(errors='replace').splitlines() if l.startswith('metal_macosx\t')), '') if lock.exists() else ''
    if want: add(have == want, 'Metal compiler is the tested one', want if have == want else f'{have} (tested with {want})',
                 'install the Xcode the lock names (27.2); --allow-other-metal builds with another, such shaders can render wrong pictures', warn=True)


def check_tools():
    for tool, pkg in BREW_TOOLS.items():
        path = shutil.which(tool) or next((str(Path(d) / tool) for d in ('/opt/homebrew/bin', '/usr/local/bin') if (Path(d) / tool).exists()), None)
        add(bool(path), tool, path or 'not found', f'brew install {pkg}')
    add(bool(shutil.which('make')), 'make', shutil.which('make') or 'not found', 'xcode-select --install (or install Xcode)')
    add(bool(shutil.which('clang')), 'clang', shutil.which('clang') or 'not found', 'xcode-select --install (or install Xcode)')
    xlate = REPO / 'dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate'
    if not xlate.exists():
        add(False, 'shader translator', f'{xlate} is missing', 'check the repository out again')
    else:
        code, out = run([str(xlate), '--source-sha'])
        add(code == 0 and bool(out), 'shader translator runs', out.splitlines()[0][:40] if out else (out or 'no output'),
            'the translator is a committed native executable; if macOS blocked it: xattr -dr com.apple.quarantine dm/gfx/tools; otherwise check the repository out again')
    cross = REPO / 'dm/gfx/tools/spirv_cross_bin/spirv-cross'
    add(cross.exists() and os.access(cross, os.X_OK), 'spirv-cross', str(cross.relative_to(REPO)) if cross.exists() else 'missing', 'check the repository out again')


def check_python():
    add(sys.version_info >= (3, 9), 'Python', sys.version.split()[0] + f' ({sys.executable})', 'use a Python 3.9 or later (3.14 is what the repository is tested on)')
    gone = [pip for mod, pip in PY_MODULES if importlib.util.find_spec(mod.split('.')[0]) is None]
    if sys.version_info < (3, 14) and importlib.util.find_spec('backports.zstd') is None and importlib.util.find_spec('compression') is None: gone.append('backports.zstd')
    in_venv = sys.prefix != getattr(sys, 'base_prefix', sys.prefix)
    fix = ('python3 -m venv <drive>/venv && <drive>/venv/bin/pip install -r .github/requirements/tools.txt, then run the scripts with <drive>/venv/bin/python '
           '(Homebrew\'s Python refuses system-wide pip installs: externally-managed-environment)')
    add(not gone, 'Python modules', ('missing: ' + ', '.join(gone)) if gone else 'all present' + (' (in a venv)' if in_venv else ''), fix)


def check_game(path):
    if not path: return
    p = Path(path).expanduser()
    if p.is_file() and p.suffix.lower() in ('.nup', '.nsp', '.xci'):
        add(True, 'game file', str(p)); return
    found = sorted(x.name for x in p.glob('*') if x.suffix.lower() in ('.nup', '.nsp', '.xci')) if p.is_dir() else []
    add(bool(found), 'game folder', f'{p}: {", ".join(found)[:80] or "no .nup, .nsp or .xci in it"}', 'point the script at the folder with your own dump of the game (a NUP needs no keys; an NSP needs prod.keys)')


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg in ('-h', '--help'): print(__doc__); return 0
    check_machine()
    check_python()
    check_xcode()
    check_tools()
    check_disk(Path(arg).expanduser().resolve() if arg else REPO)
    check_game(arg)
    width = max(len(r[1]) for r in results)
    for st, name, detail, fix in results:
        mark = {'ok': '  ok  ', 'warn': ' warn ', 'FAIL': ' FAIL '}[st]
        print(f'[{mark}] {name.ljust(width)}  {detail}')
        if st != 'ok' and fix: print(f'         → {fix}')
    bad = [r for r in results if r[0] == 'FAIL']
    warn = [r for r in results if r[0] == 'warn']
    print(f'\n{len(results) - len(bad) - len(warn)} ok, {len(warn)} warning(s), {len(bad)} to fix' + ('' if bad else ': ready to build'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
