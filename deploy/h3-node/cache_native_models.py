#!/usr/bin/env python3
"""Windows native source read -> WSL stdin -> ext4, avoiding 9p model reads."""
import base64,json,os,pathlib,subprocess,sys,time,hashlib
import cache_fast_models as common


def receive(relative):
    if relative not in common.FILES:raise ValueError('Unexpected file')
    destination=common.DEST/relative
    destination.parent.mkdir(parents=True,exist_ok=True)
    partial=destination.with_suffix(destination.suffix+'.partial')
    if partial.exists():raise RuntimeError('Partial exists')
    digest=hashlib.sha256();count=0
    with partial.open('xb') as out:
        while chunk:=sys.stdin.buffer.read(8*1024*1024):
            common.checkpoint()
            out.write(chunk);digest.update(chunk);count+=len(chunk)
        out.flush();os.fsync(out.fileno())
    if count!=common.FILES[relative]:raise RuntimeError('Wrong byte count')
    verified=common.sha256(partial)
    if verified!=digest.hexdigest():raise RuntimeError('File rehash mismatch')
    print(json.dumps({'receiver_sha256':verified,'bytes':count}),flush=True)


def native_copy(relative):
    source='G:\\trae_voice_over_video\\models\\comfyui\\'+relative.replace('/','\\')
    ps=r'''
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$sourcePath='SOURCE'
$stream=[System.IO.File]::OpenRead($sourcePath)
$buffer=New-Object byte[] 8388608
$hash=[System.Security.Cryptography.SHA256]::Create()
$start=New-Object System.Diagnostics.ProcessStartInfo
$start.FileName='C:\Windows\System32\wsl.exe'
$start.Arguments='-d RayWorker -u root -- python3 /opt/ocv-h3/cache_native_models.py --receive RELATIVE'
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
$start.RedirectStandardInput=$true
$start.RedirectStandardOutput=$true
$process=[System.Diagnostics.Process]::Start($start)
try {
 while(($read=$stream.Read($buffer,0,$buffer.Length)) -gt 0) {
  $null=$hash.TransformBlock($buffer,0,$read,$buffer,0)
  $process.StandardInput.BaseStream.Write($buffer,0,$read)
 }
 $null=$hash.TransformFinalBlock((New-Object byte[] 0),0,0)
 $process.StandardInput.Close()
 $reply=$process.StandardOutput.ReadToEnd()
 $process.WaitForExit()
 if($process.ExitCode -ne 0){throw 'Receiver failed'}
 $hex=([System.BitConverter]::ToString($hash.Hash)).Replace('-','').ToLowerInvariant()
 Write-Output ('source_sha256='+$hex)
 Write-Output $reply
} finally {$stream.Dispose();$hash.Dispose()}
'''.replace('SOURCE',source).replace('RELATIVE',relative)
    encoded=base64.b64encode(ps.encode('utf-16le')).decode()
    proc=subprocess.run(['/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-EncodedCommand',encoded],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',timeout=14400)
    if proc.returncode:raise RuntimeError('Native copy process failed: '+proc.stdout[-400:])
    source_hash=None;received=None
    for line in proc.stdout.splitlines():
        if line.startswith('source_sha256='):source_hash=line.partition('=')[2]
        if line.startswith('{'):
            report=json.loads(line)
            if 'receiver_sha256' in report:received=report
            else:print(json.dumps(report),flush=True)
    if not received or source_hash!=received['receiver_sha256']:raise RuntimeError('Native source/target hash mismatch')
    return received


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--receive':return receive(sys.argv[2])
    common.assert_stopped();common.await_tts_idle();print(json.dumps(common.capacity()),flush=True)
    marker=common.ROOT/'model-cache-copy.active'
    # Parent/root has stopped the old copy before this controlled replacement.
    if marker.exists():raise RuntimeError('Another copy marker exists')
    marker.write_text(str(os.getpid()))
    manifest_path=common.DEST/'manifest.json';common.DEST.mkdir(exist_ok=True)
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'source':str(common.SOURCE),'created_at':time.time(),'files':{}}
    try:
        for relative,size in common.FILES.items():
            source=common.SOURCE/relative;target=common.DEST/relative;before=source.stat()
            if target.exists():
                known=manifest['files'].get(relative)
                if not known or target.stat().st_size!=size or common.sha256(target)!=known['sha256']:raise RuntimeError('Unverified existing cache')
                continue
            partial=target.with_suffix(target.suffix+'.partial')
            if partial.exists():partial.rename(partial.with_suffix('.aborted-'+str(int(time.time()))))
            started=time.monotonic();report=native_copy(relative);after=source.stat()
            if before.st_mtime_ns!=after.st_mtime_ns or after.st_size!=size:raise RuntimeError('Source changed during transfer')
            partial.chmod(0o644);partial.replace(target)
            elapsed=time.monotonic()-started
            manifest['files'][relative]={'bytes':size,'sha256':report['receiver_sha256'],'source_mtime_ns':before.st_mtime_ns,'copy_and_verify_seconds':elapsed,'transport':'windows-native-stdin'}
            common.atomic_json(manifest_path,manifest)
            print(json.dumps({'copied_verified':relative,'seconds':round(elapsed,2),'bytes':size}),flush=True)
        override=common.ROOT/'compose.models-cache.yaml'
        override.write_text('services:\n  h3:\n    volumes:\n      - /opt/ocv-h3/models-cache:/models:ro\n')
        print(json.dumps({'complete':True,'manifest':str(manifest_path),'container_recreated':False}),flush=True)
    finally:marker.unlink(missing_ok=True)


if __name__=='__main__':main()
