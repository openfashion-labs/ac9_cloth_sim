# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject omitted distribution requirements independently of an ordinary successful ZIP build."""
import argparse,hashlib,json,re,tomllib,zipfile
from pathlib import Path

REQUIRED=('Check distribution CPU safety','Derive split distribution preview from the canonical full ZIP')
ROLES={'full','main','runtime'}
DOCS_GROUP="${{ github.event_name == 'pull_request' && format('docs-pr-{0}', github.event.pull_request.number) || 'pages' }}"

def validate_workflow(text):
    missing=[name for name in REQUIRED if not re.search(r'(?m)^\s*- name: '+re.escape(name)+r'\s*$',text)]
    if not re.search(r'(?m)^\s*name: split-distribution-preview\s*$',text):missing.append('split-distribution-preview')
    if missing:raise ValueError('Required distribution CI omitted: '+', '.join(missing))

def validate_docs_concurrency(text):
    match=re.search(r'(?m)^concurrency:[ \t]*\n((?:[ \t]+[^\n]*(?:\n|$))+)',text)
    lines=[] if match is None else [line.strip() for line in match[1].splitlines()]
    if 'group: '+DOCS_GROUP not in lines or 'cancel-in-progress: false' not in lines:
        raise ValueError('PR docs must use a separate queue while main retains the pages group')

def validate_plan(root):
    scope=json.loads((root/'.github/release_scope.json').read_text(encoding='utf-8'))
    manifest=tomllib.loads((root/'blender_manifest.toml').read_text(encoding='utf-8'))
    if scope.get('schema')!=1 or scope.get('version')!=manifest['version']:raise ValueError('Release scope version differs from package')
    if scope.get('distribution')!='split' or scope.get('channel')!='preview':raise ValueError('Expected an explicit split preview scope')
    if scope.get('required_packages')!=['full','main','runtime']:raise ValueError('Expected all three distribution roles')
    publication=scope.get('publication')
    if not isinstance(publication,dict) or type(publication.get('ready')) is not bool or not isinstance(publication.get('remaining'),list):raise ValueError('Explicit publication state required')
    if publication['ready'] and publication['remaining']:raise ValueError('Ready publication still has unresolved requirements')
    if type(publication.get('asset_publication_approved')) is not bool:raise ValueError('Explicit asset publication approval state required')
    validate_workflow((root/'.github/workflows/release.yml').read_text(encoding='utf-8'))
    publisher=(root/'.github/workflows/publish-distribution.yml').read_text(encoding='utf-8')
    docs=(root/'.github/workflows/docs.yml').read_text(encoding='utf-8')
    if 'tools/publish_distribution.py' not in publisher or 'group: pages' not in publisher:
        raise ValueError('Explicit reviewed-artifact publication workflow is missing')
    if 'catalog_site.py overlay' not in docs or 'catalog_site.py guard' not in docs:
        raise ValueError('Persistent catalog and stale-generation guard must be connected')
    validate_docs_concurrency(docs)
    if '--clobber' in (root/'.github/workflows/release.yml').read_text(encoding='utf-8'):
        raise ValueError('Legacy release workflow must not overwrite public assets')
    return scope

def validate_packages(build,version):
    packages=build.get('packages',{})
    if set(packages)!=ROLES:raise ValueError('A full ZIP alone cannot satisfy split distribution')
    result={}
    for role,entry in packages.items():
        p=Path(entry['path'])
        with p.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if p.stat().st_size!=entry['bytes'] or digest!=entry['sha256']:raise ValueError(role+': artifact differs from build record')
        with zipfile.ZipFile(p) as z:
            if z.testzip() is not None:raise ValueError(role+': corrupt archive')
            names=z.namelist();m=tomllib.loads(z.read('blender_manifest.toml').decode('utf-8'))
            if m['id']!=('ac9_cloth_sim_runtime' if role=='runtime' else 'ac9_cloth_sim'):raise ValueError(role+': wrong extension ID')
            if role!='runtime' and m['version']!=version:raise ValueError(role+': wrong main version')
            wheels=[n for n in names if n.startswith('wheels/') and n.endswith('.whl')]
            if role=='main':
                if wheels or m.get('wheels') or 'runtime_contract.json' not in names:raise ValueError('Light main must have a runtime contract and no wheels')
            elif len(wheels)!=2:raise ValueError(role+': expected the two runtime wheels')
            result[role]={'bytes':entry['bytes'],'sha256':entry['sha256']}
    return result

def require_publish_ready(scope):
    if scope['publication']['ready'] is not True or scope['publication']['remaining']:
        raise ValueError('Publication incomplete: '+', '.join(scope['publication']['remaining']))

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--phase',choices=('plan','packages','publish'),required=True);p.add_argument('--build',type=Path);a=p.parse_args()
    scope=validate_plan(a.root);result={'phase':a.phase,'version':scope['version'],'publication_ready':scope['publication']['ready'],'remaining':scope['publication']['remaining']}
    if a.phase=='packages':
        if a.build is None:p.error('--build is required for package verification')
        result['packages']=validate_packages(json.loads(a.build.read_text(encoding='utf-8')),scope['version'])
    if a.phase=='publish':require_publish_ready(scope)
    print(json.dumps(result))

if __name__=='__main__':main()
