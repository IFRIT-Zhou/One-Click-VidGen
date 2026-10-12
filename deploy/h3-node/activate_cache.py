import json,subprocess,time,fcntl
from pathlib import Path
root=Path('/opt/ocv-h3')
def run(*args):return subprocess.check_output(args,text=True).strip()
lock=open('/run/lock/ocv-gpu-phase.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert not (root/'model-cache-copy.active').exists(),'Copy still active'
manifest=json.loads((root/'models-cache/manifest.json').read_text())
assert len(manifest['files'])==4
for rel,entry in manifest['files'].items():
 p=(root/'models-cache'/rel).resolve()
 assert p.is_relative_to(root/'models-cache') and p.is_file() and p.stat().st_size==entry['bytes']
 assert len(entry['sha256'])==64
live=json.loads(run('docker','inspect','ocv-h3-comfyui'))[0]
if live['State']['Running']: raise SystemExit('H3 must remain stopped during image update')
image=live['Config']['Image']
compose=root/'compose.yaml';original=compose.read_text()
assert '/mnt/g/trae_voice_over_video/models/comfyui:/models:ro' in original
(root/'build/compose-before-cache.yaml').write_text(original)
compose.write_text(original.replace('/mnt/g/trae_voice_over_video/models/comfyui:/models:ro','/opt/ocv-h3/models-cache:/models:ro'))
backup='ocv-h3-comfyui-pre-cache-'+time.strftime('%Y%m%d%H%M%S')
subprocess.run(['docker','rename','ocv-h3-comfyui',backup],check=True)
create=['docker','create','--name','ocv-h3-comfyui','--restart','no',
        '--shm-size',str(live['HostConfig']['ShmSize']),'--gpus','all']
for bind in live['HostConfig'].get('Binds') or []:
 create += ['-v', '/opt/ocv-h3/models-cache:/models:ro' if bind.split(':')[1]=='/models' else bind]
for mount in live['HostConfig'].get('Mounts') or []:
 if mount['Type'] not in {'bind','volume'}: raise SystemExit('Unsupported original mount type')
 source='/opt/ocv-h3/models-cache' if mount['Target']=='/models' else mount['Source']
 definition=f"type={mount['Type']},src={source},dst={mount['Target']}"
 if mount.get('ReadOnly'): definition += ',readonly'
 create += ['--mount',definition]
for port,bindings in (live['HostConfig'].get('PortBindings') or {}).items():
 for binding in bindings or []:
  create += ['-p',f"{binding['HostIp']}:{binding['HostPort']}:{port}"]
for env in live['Config'].get('Env') or []: create += ['--env',env]
if live['Config'].get('WorkingDir'): create += ['--workdir',live['Config']['WorkingDir']]
if live['Config'].get('User'): create += ['--user',live['Config']['User']]
entrypoint=live['Config'].get('Entrypoint') or []
if entrypoint: create += ['--entrypoint',entrypoint[0]]
create += [image]+entrypoint[1:]+(live['Config'].get('Cmd') or [])
result=subprocess.run(create,capture_output=True,text=True)
if result.returncode:
 subprocess.run(['docker','rename',backup,'ocv-h3-comfyui'],check=True)
 compose.write_text(original)
 raise SystemExit('Could not recreate H3; original name/config restored')
(root/'build/previous-h3-before-cache.txt').write_text(backup)
after=json.loads(run('docker','inspect','ocv-h3-comfyui'))[0]
if after['State']['Running']: raise SystemExit('Unexpected running H3 after create')
if after['HostConfig']['DeviceRequests'] != live['HostConfig']['DeviceRequests']: raise SystemExit('GPU request changed')
if after['HostConfig']['PortBindings'] != live['HostConfig']['PortBindings']: raise SystemExit('Port mappings changed')
assert any(m['Destination']=='/models' and m['Source']=='/opt/ocv-h3/models-cache' and not m['RW'] for m in after['Mounts'])
assert sorted((m['Source'],m['Destination'],m['RW']) for m in after['Mounts'] if m['Destination']!='/models')==sorted((m['Source'],m['Destination'],m['RW']) for m in live['Mounts'] if m['Destination']!='/models')
print('H3 recreated stopped with verified ext4 model cache')
