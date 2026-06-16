"""Script to fix read.py implementation."""

import re

# Read the file
with open('src/mdrack/cli/commands/read.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove _echo_json function
content = re.sub(r'def _echo_json\(payload: dict\[str, Any\]\) -> None:.*?(?=\n(?:def |#|@))', '', content, flags=re.DOTALL)

# Add import for _output from parent module
content = content.replace(
    'from mdrack.config.loader import load_config',
    'from mdrack.cli import _output\nfrom mdrack.config.loader import load_config'
)

# Replace _echo_json calls with _output
content = content.replace('_echo_json(payload)', '_output(ctx, payload)')

# Fix get_neighbors to use count=1 for immediate neighbors only
content = content.replace(
    'neighbors = get_neighbors(conn, chunk_id)',
    'neighbors = get_neighbors(conn, chunk_id, count=1)'
)

# Add ctx.exit(1) after error returns for non-found items
content = content.replace(
    '''                _output(ctx, payload)
                return''',
    '''                _output(ctx, payload)
                ctx.exit(1)'''
)

# Write the fixed content
with open('src/mdrack/cli/commands/read.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed read.py')
