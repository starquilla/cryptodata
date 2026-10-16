"""Fail-closed Codex JSONL validation, including one verified startup notice."""
import json

CODE_MODE_DISABLED_NOTICE=('Code Mode is unavailable because code-mode host is disabled. '
    'Code mode will fail closed; enable features.code_mode_host and install codex-code-mode-host.')
CODE_MODE_DISABLED_NOTICE_MARKDOWN=('Code Mode is unavailable because code-mode host is disabled. '
    'Code mode will fail closed; enable `features.code_mode_host` and install `codex-code-mode-host`.')
KNOWN_DISABLED_NOTICES=(CODE_MODE_DISABLED_NOTICE,CODE_MODE_DISABLED_NOTICE_MARKDOWN)

def validate_trace(text,answer):
    if not isinstance(answer,str) or not answer.strip() or len(answer.encode('utf-8'))>16000:
        raise ValueError('Missing or oversized final answer')
    events=[json.loads(line) for line in text.splitlines() if line.strip()]
    if not events or events[0].get('type')!='thread.started' or events[-1].get('type')!='turn.completed':
        raise ValueError('Incomplete Codex turn')
    counts={}
    messages=[]
    notices=0
    for event in events:
        kind=event.get('type')
        counts[kind]=counts.get(kind,0)+1
        if kind not in ('thread.started','turn.started','turn.completed','item.started','item.updated','item.completed'):
            raise ValueError('Unsupported or failed Codex event')
        if kind.startswith('item.'):
            item=event.get('item',{})
            item_type=item.get('type')
            if item_type=='error':
                if (kind!='item.completed' or set(item)!={'id','type','message'}
                        or item.get('message') not in KNOWN_DISABLED_NOTICES):
                    raise ValueError('Unrecognized Codex error item')
                notices+=1
            elif item_type=='agent_message':
                if kind=='item.completed': messages.append(item.get('text'))
            elif item_type!='reasoning':
                raise ValueError('Tool or unsupported Codex item')
    if any(counts.get(name)!=1 for name in ('thread.started','turn.started','turn.completed')) or notices>1:
        raise ValueError('Ambiguous or repeated Codex turn/notice')
    if len(messages)!=1 or not isinstance(messages[0],str) or messages[0].strip()!=answer.strip():
        raise ValueError('Final answer does not match completed agent message')
    return {'event_counts':counts,'known_code_mode_disabled_notice_count':notices,
            'tool_items':0,'completed_turns':1,'final_answer_matches':True}
