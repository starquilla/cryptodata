"""One durable, bounded acceptance request, never an automatic retry."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from ai_runtime import save

ROOT=Path(__file__).resolve().parent
ACTIONS=('read_file','write_file','read_url','execute_url','command','unsandboxed','mcp')

def verify_settings(value,workspace):
    permissions=value.get('permissions',{})
    if (set(permissions.get('deny',[]))!={a+'(*)' for a in ACTIONS}
            or permissions.get('allow') or permissions.get('ask')
            or set(value)-{'permissions','trustedWorkspaces'}
            or [str(Path(p).resolve()) for p in value.get('trustedWorkspaces',[])]!=[str(workspace.resolve())]):
        raise ValueError('Unexpected permissions or workspace trust; review required')

def parse_trace(text):
    events=[json.loads(line) for line in text.splitlines() if line.strip()]
    if not events or events[0].get('event')!='init' or events[-1].get('event')!='result':
        raise ValueError('Incomplete event stream')
    types=[]
    for event in events:
        if event.get('event') not in ('init','step_update','result'):
            raise ValueError('Unknown event')
        step=event.get('step_update',{})
        if step:
            kind=step.get('step_type')
            types.append(kind)
            if kind not in ('user_input','agent_response','checkpoint') or step.get('tool_info') or step.get('subagent_info') or step.get('tool_name'):
                raise ValueError('Tool or unsupported step observed; review required')
    result=events[-1]['result']
    if result.get('status')!='SUCCESS' or not result.get('response','').strip() or len(result['response'].encode())>16000:
        raise ValueError('Model did not return a bounded successful response')
    return result, sorted(set(types))

def main():
    home=ROOT/'private/antigravity-home'
    workspace=home/'workspace'
    settings=home/'.gemini/antigravity-cli/settings.json'
    before=settings.read_bytes()
    verify_settings(json.loads(before),workspace)
    tool=Path('E:/công cụ lập trình/Antigravity-CLI/1.3.6')
    binary=tool/'agy.exe'
    manifest=json.loads((tool/'release-manifest.json').read_text(encoding='utf-8'))
    if hashlib.sha512(binary.read_bytes()).hexdigest()!=manifest['sha512'].lower():
        raise ValueError('Binary hash changed')
    folder=home/'pilot-text-v1'
    folder.mkdir(exist_ok=True)
    receipt=folder/'receipt.json'
    # Exclusive creation prevents two launches, including after an uncertain crash.
    try:
        with receipt.open('x',encoding='utf-8') as stream:
            json.dump({'state':'started','started_at':dt.datetime.now(dt.timezone.utc).isoformat()},stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        print('Existing pilot receipt retained; model NOT called again.')
        return
    report={'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'request_id':'antigravity-text-v1','state':'needs_review',
            'automatic_retry':False,'queue_ai_enabled':False,'quota_remaining':'UNAVAILABLE_NOT_ESTIMATED',
            'provider':'antigravity','version':'1.3.6','input_scope':'fixed synthetic text only'}
    process=None
    try:
        env=os.environ.copy()
        for key in list(env):
            if any(x in key.upper() for x in ('API_KEY','ACCESS_TOKEN','GOOGLE_APPLICATION_CREDENTIALS')) or key.upper() in ('NO_BROWSER','GEMINI_CLI_HOME','GOOGLE_GENAI_USE_VERTEXAI','GOOGLE_GENAI_USE_GCA','AGY_ADC_AUTH'):
                env.pop(key,None)
        env.update(USERPROFILE=str(home),HOME=str(home),LOCALAPPDATA=str(home/'AppData/Local'),
                   APPDATA=str(home/'AppData/Roaming'),TEMP=str(home/'temp'),TMP=str(home/'temp'),
                   AGY_CLI_DISABLE_AUTO_UPDATE='true')
        prompt='Summarize this synthetic text in one short sentence. Use no tools, files, commands, browsing or subagents: A LAN worker retained its receipt while its coordinator was offline. On reconnection the server acknowledged the same completion without creating a second job.'
        command=[str(binary),'--print',prompt,'--output-format','stream-json',
                 '--model','gemini-3.8-flash-high','--mode','plan',
                 '--disable-slash-commands','--print-timeout','90s']
        with (folder/'stdout.log').open('wb') as out,(folder/'stderr.log').open('wb') as err:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                                     env=env,cwd=workspace,creationflags=subprocess.CREATE_NO_WINDOW)
            deadline=time.monotonic()+110
            while process.poll() is None:
                if time.monotonic()>deadline or any((folder/p).stat().st_size>256000 for p in ('stdout.log','stderr.log')):
                    raise ValueError('Timeout or output limit; no automatic retry')
                time.sleep(.2)
        report['exit_code']=process.returncode
        if process.returncode:
            raise ValueError('CLI failed; inspect private logs; no automatic retry')
        result,kinds=parse_trace((folder/'stdout.log').read_text(encoding='utf-8'))
        report.update(state='succeeded',response=result['response'],usage=result.get('usage'),
                      duration_seconds=result.get('duration_seconds'),observed_step_types=kinds,
                      tool_steps_observed=0,subagent_steps_observed=0)
    except Exception as error:
        report['error']=str(error)
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        report['settings_unchanged']=settings.read_bytes()==before
        save(receipt,report)
        save(ROOT/'evidence/ANTIGRAVITY-PILOT-v1.json',report)
        save(Path('E:/tai nguyen codex/AI-LAN-Coordinator/ANTIGRAVITY-PILOT-v1.json'),report)
    print(json.dumps(report,ensure_ascii=True,indent=2))

if __name__=='__main__':
    main()
