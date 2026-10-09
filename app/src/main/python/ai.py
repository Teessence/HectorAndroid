"""Ask Claude to fill in an ingredient's nutrition from a link or a description.

Raw HTTPS via urllib on purpose: the official `anthropic` SDK pulls in
pydantic-core (a compiled extension) that the embedded Android Python can't
reliably install, and this app ships with Flask only.

Claude gets the web_fetch / web_search server tools, so a pasted product link
is opened on Anthropic's side, and a JSON schema (structured outputs) so the
reply always parses into form fields. Nothing is saved here — the page fills
the form and the user reviews it and presses Create.
"""
import json
import urllib.error
import urllib.request

API_URL = 'https://api.anthropic.com/v1/messages'
MODEL = 'claude-opus-5-5'
MAX_PAUSE_RESUMES = 3

UNITS = ['g', 'ml', 'piece', 'tbsp', 'tsp', 'cup', 'oz', 'serving']

# Form field -> unit the value must be expressed in.
NUMBER_FIELDS = [
    ('serving_size', 'in `unit`'), ('package_size', 'in `unit`'), ('package_cost', 'EUR'),
    ('calories', 'kcal'), ('protein', 'g'), ('fat', 'g'), ('saturates', 'g'),
    ('carbs', 'g'), ('fiber', 'g'), ('sugar', 'g'), ('salt', 'g'),
    ('vit_a', 'μg RAE'), ('vit_c', 'mg'), ('vit_d', 'μg'), ('vit_e', 'mg'), ('vit_k', 'μg'),
    ('vit_b1', 'mg'), ('vit_b2', 'mg'), ('vit_b3', 'mg'), ('vit_b6', 'mg'),
    ('vit_b9', 'μg'), ('vit_b12', 'μg'),
    ('calcium', 'mg'), ('iron', 'mg'), ('magnesium', 'mg'), ('phosphorus', 'mg'),
    ('potassium', 'mg'), ('zinc', 'mg'), ('selenium', 'μg'), ('iodine', 'μg'),
    ('copper', 'mg'), ('manganese', 'mg'),
]
TEXT_FIELDS = ['name', 'shop', 'company']

_nullable_number = {'anyOf': [{'type': 'number'}, {'type': 'null'}]}
_nullable_string = {'anyOf': [{'type': 'string'}, {'type': 'null'}]}

SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string'},
        'ingredient': {
            'type': 'object',
            'properties': {
                **{f: _nullable_string for f in TEXT_FIELDS},
                'unit': {'anyOf': [{'type': 'string', 'enum': UNITS}, {'type': 'null'}]},
                **{f: _nullable_number for f, _ in NUMBER_FIELDS},
            },
            'required': TEXT_FIELDS + ['unit'] + [f for f, _ in NUMBER_FIELDS],
            'additionalProperties': False,
        },
    },
    'required': ['reply', 'ingredient'],
    'additionalProperties': False,
}

SYSTEM = (
    "You help fill in a food-tracking app's ingredient form. The user pastes a product "
    "link, a nutrition label, or a description. If there is a link, open it with "
    "web_fetch; if they only name a product, you may use web_search to find its label.\n\n"
    "Fill `ingredient` with values for ONE serving of `serving_size` `unit` "
    "(prefer 100 g / 100 ml when the label is per 100 g or 100 ml). Units per field: "
    + ', '.join(f'{f} = {u}' for f, u in NUMBER_FIELDS) + ". "
    "Use `null` for anything you could not find or reasonably estimate — never invent "
    "precise numbers. Estimates from standard food composition data (e.g. USDA) are fine "
    "for vitamins and minerals a label omits; say which values are estimates in `reply`. "
    "`name` should be short and specific (product + variant). `company` is the brand. "
    "`reply` is one or two plain sentences for the user: what you found, the source, and "
    "anything to double-check. If the user asks to change something, return the full "
    "updated ingredient."
)


class AIError(Exception):
    pass


def _post(api_key, body):
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode('utf-8'),
        headers={
            'content-type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            # Server-side fallback if a safety classifier declines the request.
            'anthropic-beta': 'server-side-fallback-2026-07-01',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode('utf-8')).get('error', {}).get('message', '')
        except Exception:
            detail = ''
        if e.code == 401:
            raise AIError('Claude rejected the API key. Check it in Settings → AI.')
        if e.code == 429:
            raise AIError('Rate limited by the Claude API. Try again in a minute.')
        if e.code >= 500 or e.code == 529:
            raise AIError('The Claude API is busy right now. Try again shortly.')
        raise AIError(f'Claude API error {e.code}: {detail or e.reason}')
    except urllib.error.URLError as e:
        raise AIError(f"Couldn't reach the Claude API ({e.reason}). Check the internet connection.")


def fill_ingredient(api_key, conversation):
    """conversation: [{'role': 'user'|'assistant', 'text': str}, ...] ending with
    a user turn. Returns {'reply': str, 'ingredient': {field: value|None}}."""
    if not api_key:
        raise AIError('Add your Claude API key in Settings → AI first.')
    messages = [{'role': m['role'], 'content': m['text']}
                for m in conversation
                if m.get('role') in ('user', 'assistant') and (m.get('text') or '').strip()]
    if not messages or messages[-1]['role'] != 'user':
        raise AIError('Type a link or a description first.')

    body = {
        'model': MODEL,
        'max_tokens': 16000,
        'system': SYSTEM,
        'fallbacks': 'default',
        'output_config': {
            'effort': 'medium',
            'format': {'type': 'json_schema', 'schema': SCHEMA},
        },
        'tools': [
            {'type': 'web_fetch_20260209', 'name': 'web_fetch', 'max_uses': 3},
            {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 3},
        ],
        'messages': messages,
    }

    resp = _post(api_key, body)
    # Server tools can pause a long turn; resend with the partial turn appended
    # and the API resumes where it left off.
    for _ in range(MAX_PAUSE_RESUMES):
        if resp.get('stop_reason') != 'pause_turn':
            break
        body['messages'] = messages + [{'role': 'assistant', 'content': resp['content']}]
        resp = _post(api_key, body)

    stop = resp.get('stop_reason')
    if stop == 'refusal':
        raise AIError("Claude declined this request. Try describing the product differently.")
    if stop == 'max_tokens':
        raise AIError('The answer was cut off. Try a shorter request.')
    texts = [b.get('text', '') for b in resp.get('content', []) if b.get('type') == 'text']
    if not texts:
        raise AIError('Claude returned no answer. Try again.')
    try:
        data = json.loads(texts[-1])
    except ValueError:
        raise AIError('Claude returned an unreadable answer. Try again.')

    ing = data.get('ingredient') or {}
    clean = {}
    for f in TEXT_FIELDS:
        v = ing.get(f)
        clean[f] = v.strip() if isinstance(v, str) and v.strip() else None
    clean['unit'] = ing.get('unit') if ing.get('unit') in UNITS else None
    for f, _ in NUMBER_FIELDS:
        v = ing.get(f)
        clean[f] = round(float(v), 4) if isinstance(v, (int, float)) and v >= 0 else None
    return {'reply': (data.get('reply') or '').strip(), 'ingredient': clean}
