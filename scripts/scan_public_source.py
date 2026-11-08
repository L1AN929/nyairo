"""Scan the whole source tree; exceptions identify exact public fixture matches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
PATTERNS={
 'telegram_bot_token':re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b'),
 'openai_style_key':re.compile(r'\bsk-[A-Za-z0-9]{16,}\b'),
 'aws_key_id':re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
 'github_token':re.compile(r'\b(?:ghp|gho|ghs)_[A-Za-z0-9]{30,}\b'),
 'slack_token':re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b'),
 'cloudflare_token':re.compile(r'\bcfat_[A-Za-z0-9_-]{30,}\b'),
 'private_key_block':re.compile('-----BEGIN '+r'[A-Z ]*PRIVATE KEY'+'-----'),
 'windows_home':re.compile(r'[Cc]:[\\/]'+r'Users[\\/][^\s"\'<>`/\\]+'),
 'wsl_home':re.compile('/mnt/c/'+r'Users/[^\s"\'<>`/\\]+'),
 'telegram_identity':re.compile(r'telegram:[0-9]{10,}'),
}
STATE_SUFFIXES={'.sqlite','.sqlite3','.db','.db3','.jsonl','.log','.pem','.key'}
BLOCKED_HOST_TOKEN_SHA256=frozenset(['071a3fac2ca94b29f56cefa24661e2ed054a4f54c1995b9e0a6f946e95686cda', '07e29c5993747fe78a96cacf7c172aec66d0fc7e545d7283b409cf1eb8859201', '25df7caaf692453cc7cd8042c5d718e706f3dd9bff6ceef75d91708e3c2e509c', '50440c13e062a5094d39afad48c63739f189a3b5d036c284d33a2f27660355f2'])
HOST_TOKEN_RE=re.compile(r'(?<![A-Za-z0-9.-])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![A-Za-z0-9.-])')


def source_files(root):
    for folder,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name not in ('.git','.venv','venv','__pycache__','node_modules')]
        for name in list(dirs):
            path=Path(folder)/name
            if path.is_symlink():
                yield path,None
                dirs.remove(name)
        for name in names:
            path=Path(folder)/name
            if path.is_symlink():yield path,None;continue
            try:text=path.read_text(encoding='utf8')
            except (OSError,UnicodeDecodeError):text=None
            yield path,text


def scan(root,exceptions=None):
    root=Path(root).resolve()
    if exceptions is None:
        exceptions=json.loads((ROOT/'config/public-scan-exceptions.json').read_text(encoding='utf8'))['matches']
    public_data=json.loads((ROOT/'config/public-scan-exceptions.json').read_text(encoding='utf8')).get('public_data_files',[])
    approved_data={x['path']:x['sha256'] for x in public_data}
    approved={(x['path'],x['pattern'],x['match_sha256']) for x in exceptions}
    hits=[];known=[];state=[];scanned=0
    for path,text in source_files(root):
        relative=path.relative_to(root).as_posix()
        if path.is_symlink():
            state.append(relative)
        elif path.suffix.lower() in STATE_SUFFIXES:
            try:
                digest=hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                digest=None
            if digest is None or approved_data.get(relative)!=digest:
                state.append(relative)
        if text is None:continue
        scanned+=1
        for category,pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                digest=hashlib.sha256(match.group().encode()).hexdigest()
                item={'path':relative,'line':text.count('\n',0,match.start())+1,'pattern':category,'match_sha256':digest}
                (known if (relative,category,digest) in approved else hits).append(item)
        for match in HOST_TOKEN_RE.finditer(text):
            tok=match.group().lower()
            parts=tok.split('.')
            candidates=[tok]+['.'.join(parts[i:]) for i in range(1,len(parts)-1)]
            for cand in candidates:
                digest=hashlib.sha256(cand.encode()).hexdigest()
                if digest in BLOCKED_HOST_TOKEN_SHA256:
                    item={'path':relative,'line':text.count('\n',0,match.start())+1,'pattern':'redacted_infra_host','match_sha256':digest}
                    (known if (relative,'redacted_infra_host',digest) in approved else hits).append(item)
                    break
    return {'status':'PASS' if not hits and not state else 'FAIL','scanned_text_files':scanned,
            'unapproved_matches':hits,'known_public_matches':known,'state_or_symlink_files':state,
            'scope':'Entire source tree including vendor, documentation and website. Dependency environments excluded. No matched secret values are printed.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT);args=parser.parse_args()
    result=scan(args.root);print(json.dumps(result));return result['status']!='PASS'


if __name__=='__main__':raise SystemExit(main())
