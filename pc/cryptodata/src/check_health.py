"""Unified API-free audit; evaluates all configured data branches and backup."""
import json,sys
from data_audit import audit
if __name__=='__main__':
    health,matrix=audit()
    print(json.dumps(health,ensure_ascii=False,indent=2))
    sys.exit(2 if health['issues'] else 0)
