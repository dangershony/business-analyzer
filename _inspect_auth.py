"""Print structure of auth.json (key names only, values masked)."""
import json

with open('/home/dan/.hermes/profiles/local-company-hunter/auth.json') as f:
    d = json.load(f)

def walk(o, p=''):
    if isinstance(o, dict):
        for k, v in o.items():
            walk(v, p + '/' + k)
    elif isinstance(o, list):
        print(p, 'list[%d]' % len(o))
    else:
        s = str(o)
        masked = s[:4] + '...' if len(s) > 8 else str(o)
        print(p, '=', type(o).__name__, masked)

walk(d['credential_pool']['nous'][0], '/credential_pool/nous[0]/')
