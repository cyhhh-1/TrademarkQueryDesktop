"""Package the current working tree, including uncommitted source changes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

root = Path(__file__).resolve().parents[1]
out = root / 'home-development-package'
out.mkdir(exist_ok=True)
archive = out / 'TrademarkQueryDesktop_0.1.26_2026-09-30_开发交接.zip'
bundle = out / 'git-history.bundle'
subprocess.run(['git', 'bundle', 'create', str(bundle), '--all'], cwd=root, check=True)
subprocess.run(['git', 'bundle', 'verify', str(bundle)], cwd=root, check=True, capture_output=True)

prune = {'.git', 'node_modules', '.venv', '.python311', '__pycache__', '.pytest_cache',
         '.vite', '.local', 'diagnostics', 'home-development-package', 'target'}
skip_trees = {'worker/build', 'worker/dist', 'apps/desktop/dist',
              'apps/desktop/src-tauri/resources/worker'}
files = []
for directory, dirs, names in os.walk(root):
    base = Path(directory)
    dirs[:] = sorted(d for d in dirs if d not in prune and not d.endswith('.egg-info')
                     and (base / d).relative_to(root).as_posix() not in skip_trees)
    for name in sorted(names):
        p = base / name
        rel = p.relative_to(root).as_posix()
        if name.endswith(('.pyc', '.pyo', '.tmp')) or name == '.python311-embed.zip':
            continue
        if rel.startswith('release/') and not (name == '商标查询助手_0.1.26_x64-setup.exe'
                or name in {'商标查询助手安装与使用说明_0.1.20.docx', '商标查询助手安装与使用说明_0.1.20.pdf'}):
            continue
        if rel.startswith('deliverables/') and p.suffix.lower() != '.xlsx':
            continue
        files.append((rel, p))

def git_text(*args):
    return subprocess.run(['git', '-c', 'core.quotepath=false', *args], cwd=root,
                          check=True, capture_output=True).stdout

extra = {
    'START_HERE.md': ('# 商标查询助手 · 回家开发包\n\n'
        '当前版本 0.1.26，打包日期 2026-09-30。\n\n'
        '请先读 [docs/回家继续开发.md](docs/回家继续开发.md)，里面有可直接发给 Codex 的提示词、'
        '环境初始化、启动测试命令和历史文档勘误。\n\n'
        '这是包含未提交改动的源码快照。新电脑首次运行需要安装开发工具和联网恢复依赖。'
        '请勿用旧 Git HEAD 覆盖当前文件。\n').encode('utf-8'),
    'handoff/git-history.bundle': bundle.read_bytes(),
    'handoff/git-status.txt': git_text('status', '--short', '--untracked-files=normal'),
    'handoff/git-diff-stat.txt': git_text('diff', '--stat'),
    'handoff/git-log.txt': git_text('log', '-20', '--oneline'),
    'handoff/git-head.txt': git_text('rev-parse', 'HEAD'),
}
prefix = 'TrademarkQueryDesktop/'
manifest = {}
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for name, p in files:
        data = p.read_bytes()
        manifest[name] = hashlib.sha256(data).hexdigest()
        z.writestr(prefix + name, data)
    for name, data in extra.items():
        manifest[name] = hashlib.sha256(data).hexdigest()
        z.writestr(prefix + name, data)
    z.writestr(prefix + 'SHA256SUMS.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))

# Independently read every compressed entry and check against the manifest.
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for name, expected in manifest.items():
        assert hashlib.sha256(z.read(prefix + name)).hexdigest() == expected, name
    required = ['worker/src/trademark_worker/local_ocr.py', 'apps/desktop/src-tauri/src/settings.rs',
                'apps/desktop/package-lock.json', 'worker/tests/test_local_ocr.py',
                'docs/回家继续开发.md', 'scripts/setup-home-dev.ps1', 'handoff/git-history.bundle']
    assert all(name in manifest for name in required)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(out / (archive.name + '.sha256')).write_text(digest + '  ' + archive.name + '\n', encoding='utf-8')
(out / '校验结果.txt').write_text(
    f'ZIP CRC: OK\nSHA256: {digest}\nFiles verified: {len(manifest)}\n'
    'Git bundle: verified\nApplication tests: not rerun for packaging\n', encoding='utf-8')
print(json.dumps({'archive': str(archive), 'files': len(manifest),
                  'size_mb': round(archive.stat().st_size / 1048576, 2),
                  'sha256': digest, 'verified': True}, ensure_ascii=False))
