"""解析明确的歌词段落/角色标签，保留原始来源；不执行文本中的指令。"""
import re


SECTION = re.compile(r'^\[(裁判|诸葛亮|周瑜|intro\b|verse\b|chorus\b|hook\b|bridge\b|outro\b).*\]$', re.I)
PREFIX = re.compile(r'^\(([^()]+)\)\s*(.*)$')


def parse(text):
    result, section, speaker = [], '', None
    for source_line, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if SECTION.match(line):
            section = line[1:-1]
            speaker = (section if section in {'裁判', '诸葛亮', '周瑜'} else
                       re.split(r'[,，]', section)[-1].strip() if re.search(r'[,，]', section) else None)
            continue
        match = PREFIX.match(line)
        label = None
        if match and match[1] in {'Referee', 'A', 'B'}:
            label, line = match.groups()
            if label == 'Referee':
                speaker = '裁判'
        if not line:
            raise ValueError('歌词标签后没有实际文本')
        result.append({'index': len(result)+1, 'text': line, 'speaker': speaker,
                       'section': section, 'source_line': source_line, 'role_label': label})
    if not result:
        raise ValueError('没有实际歌词')
    return result
