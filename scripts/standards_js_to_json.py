#!/usr/bin/env python3
"""One-time tool: extract data from static/standards_data.js to data/standards.default.json.

Uses Node-style JS evaluation via a regex-based extractor specific to our file.
Run once. Result is a baseline JSON committed to the repo.
"""

import json
import re
import os


def extract_object_literal(js_text: str, var_name: str) -> str:
    """Find `const VAR_NAME = {...};` and return the {...} portion."""
    pattern = rf'const\s+{var_name}\s*=\s*'
    m = re.search(pattern, js_text)
    if not m:
        raise ValueError(f"{var_name} not found")
    start = m.end()
    if js_text[start] != '{':
        raise ValueError(f"Expected {{ after {var_name}=, got {js_text[start]}")
    # Find matching brace
    depth = 0
    in_str = False
    str_char = None
    i = start
    while i < len(js_text):
        c = js_text[i]
        if in_str:
            if c == '\\':
                i += 2
                continue
            if c == str_char:
                in_str = False
        elif c in ('"', "'"):
            in_str = True
            str_char = c
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return js_text[start:i+1]
        i += 1
    raise ValueError("Unterminated object literal")


def js_obj_to_json(js_obj_str: str) -> str:
    """Convert JS object literal to JSON: quote unquoted keys, strip trailing commas."""
    out = re.sub(
        r'(?P<pre>[\{,]\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:',
        lambda m: f'{m.group("pre")}"{m.group("key")}":',
        js_obj_str,
    )
    # Strip trailing commas before } or ]
    out = re.sub(r',(\s*[\}\]])', r'\1', out)
    return out


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(project_root, 'static', 'standards_data.js')
    out_path = os.path.join(project_root, 'data', 'standards.default.json')

    with open(js_path) as f:
        js_text = f.read()

    result = {
        'metadata': {
            'version': '1.0',
            'description': 'Time standards for CT Age Group, Eastern Zone, and USA Motivational programs',
            'last_updated_in_code': '2025-01-01',
        },
        'programs': {},
        'conversion_factors': {},
        'whatif_events': {},
    }

    # Parse CT_STANDARDS
    ct_str = extract_object_literal(js_text, 'CT_STANDARDS')
    ct = json.loads(js_obj_to_json(ct_str))
    result['programs']['ct_age_group'] = {
        'display_name': 'CT Age Group',
        'subtitle': '2025 LC',
        'season': '2025',
        'effective_date': '2025-01-01',
        'gender_keys': ['girls', 'boys'],
        'gender_labels': {'girls': 'Girls', 'boys': 'Boys'},
        'groups': ct,
    }

    # Parse EZ_STANDARDS (uses women/men keys instead of girls/boys)
    ez_str = extract_object_literal(js_text, 'EZ_STANDARDS')
    ez = json.loads(js_obj_to_json(ez_str))
    result['programs']['eastern_zone'] = {
        'display_name': 'Eastern Zone',
        'subtitle': '2025 LC',
        'season': '2025',
        'effective_date': '2025-01-01',
        'gender_keys': ['women', 'men'],
        'gender_labels': {'women': 'Women', 'men': 'Men'},
        'groups': ez,
    }

    # Parse USA_STANDARDS
    usa_str = extract_object_literal(js_text, 'USA_STANDARDS')
    usa = json.loads(js_obj_to_json(usa_str))
    result['programs']['usa_motivational'] = {
        'display_name': 'USA Motivational',
        'subtitle': '2024-2028',
        'season': '2024-2028',
        'effective_date': '2024-09-01',
        'multi_level': True,  # Has B/BB/A/AA/AAA/AAAA
        'groups': usa,
    }

    # Parse CONVERSION_FACTORS
    cf_str = extract_object_literal(js_text, 'CONVERSION_FACTORS')
    cf_json = re.sub(
        r'(?P<pre>[\{,]\s*)"([^"]+)"\s*:',
        lambda m: f'{m.group("pre")}"{m.group(2)}":',
        js_obj_to_json(cf_str),
    )
    result['conversion_factors'] = json.loads(cf_json)

    # Parse WHATIF_EVENTS
    wi_str = extract_object_literal(js_text, 'WHATIF_EVENTS')
    result['whatif_events'] = json.loads(js_obj_to_json(wi_str))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"Wrote {out_path}")
    print(f"  Programs: {list(result['programs'].keys())}")
    print(f"  CT groups: {list(result['programs']['ct_age_group']['groups'].keys())}")
    print(f"  EZ groups: {list(result['programs']['eastern_zone']['groups'].keys())}")
    print(f"  USA groups: {len(result['programs']['usa_motivational']['groups'])}")
    print(f"  Conversion factors: {len(result['conversion_factors'])}")
    print(f"  Whatif events groups: {len(result['whatif_events'])}")


if __name__ == '__main__':
    main()
