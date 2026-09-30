"""Readable, editable installation scripts with server-resolved quoted paths."""
from pathlib import Path
import shlex

TEMPLATE_DIR = Path(__file__).with_name('install_templates')


def installation_template(engine_id: str, target: Path, build_root: Path, *, update: bool = False) -> str:
    suffix = '.update.sh' if update else '.sh'
    script = (TEMPLATE_DIR / f'{engine_id}{suffix}').read_text(encoding='utf-8')
    source = build_root / engine_id / ('source' if engine_id == 'llama.cpp' else str(target).lstrip('/'))
    values = {'target': target, 'python': target / 'bin' / 'python', 'target_dir': target.parent, 'source': source}
    for key, value in values.items():
        script = script.replace('{{' + key + '}}', shlex.quote(str(value)))
    return script


def frontend_installation_template(engine_id: str, build_root: Path) -> str:
    """Send one script whose only variable is the user-selected target path."""
    script = (TEMPLATE_DIR / f'{engine_id}.sh').read_text(encoding='utf-8')
    source = shlex.quote(str(build_root / engine_id / 'source')) if engine_id == 'llama.cpp' else shlex.quote(str(build_root / engine_id)) + '{{target}}'
    return script.replace('{{source}}', source).replace('{{python}}', '{{target}}/bin/python')
