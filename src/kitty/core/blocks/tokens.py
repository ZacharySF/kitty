"""Tokens written in block text that link to something else: references, page links and tags."""
from __future__ import annotations

import re

#: ((uuid)) block references and {{embed:uuid}} embeds.
REF = re.compile(r'\(\(([0-9a-fA-F-]{36})\)\)|\{\{embed:([0-9a-fA-F-]{36})\}\}')
#: [[Page title]] and [[Page title|label]] links.
WIKI = re.compile(r'\[\[([^\[\]\n|]{1,200})(?:\|[^\[\]\n]{0,200})?\]\]')
#: #tag tokens (letters, digits, underscore, then also / and -).
TAG = re.compile(r'(?<![\w#])#([\w][\w/-]{0,99})', re.UNICODE)
