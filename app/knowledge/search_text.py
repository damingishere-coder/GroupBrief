"""Local tokenization and source-text verification, independent of SQLite."""
import re
import unicodedata


def normalize(text):
    pieces, positions = [], []
    index = 0
    while index < len(text):
        end = index+1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        value = unicodedata.normalize('NFKC', text[index:end]).casefold()
        for char in value:
            char = ' ' if char.isspace() else char
            if char == ' ' and pieces and pieces[-1] == ' ':
                positions[-1] = (positions[-1][0], end)
            else:
                pieces.append(char); positions.append((index,end))
        index=end
    return ''.join(pieces),positions


def han(char):
    value=ord(char)
    return 0x3400<=value<=0x9fff or 0x20000<=value<=0x323af or 0xf900<=value<=0xfaff


def units(text):
    text=normalize(text)[0]
    i=0
    while i<len(text):
        if han(text[i]):
            j=i+1
            while j<len(text) and han(text[j]):
                j+=1
            yield 'c',text[i:j]
            i=j
        elif text[i].isalnum() or text[i]=='_':
            j=i+1
            while j<len(text) and not han(text[j]) and (text[j].isalnum() or text[j]=='_'):
                j+=1
            yield 'w',text[i:j]
            i=j
        else:
            i+=1


def tokenize(text):
    tokens=[]
    for kind,value in units(text):
        if kind=='c':
            tokens.extend('u'+c for c in value)
            tokens.extend('b'+value[i:i+2] for i in range(len(value)-1))
        else:
            tokens.append('w'+value)
    return ' '.join(tokens)


def query_terms(query):
    if not query.strip() or len(query)>256 or query.count('"')%2:
        raise ValueError('请输入 1—256 字符的关键词，并配对使用双引号')
    terms=[normalize(a or b)[0].strip() for a,b in re.findall(r'"([^"]*)"|(\S+)',query)]
    terms=list(dict.fromkeys(t for t in terms if t))
    if len(terms)>12:
        raise ValueError('一次最多查询 12 个关键词或短语')
    tokens=[]
    for term in terms:
        for kind,value in units(term):
            if kind=='c':
                tokens.extend(['u'+value] if len(value)==1 else ['b'+value[i:i+2] for i in range(len(value)-1)])
            else:
                tokens.append('w'+value)
    if not tokens or len(tokens)>80:
        raise ValueError('查询需要有效文字，且最多包含 80 个检索词元')
    match=' AND '.join('"'+t.replace('"','""')+'"' for t in dict.fromkeys(tokens))
    return terms,match


def evidence_snippet(text,terms,max_chars=360):
    normalized,positions=normalize(text)
    ranges=[]
    for term in terms:
        index=normalized.find(term)
        if index<0:
            return None
        while index>=0:
            ranges.append((positions[index][0],positions[index+len(term)-1][1]))
            index=normalized.find(term,index+max(1,len(term)))
    ranges.sort()
    merged=[]
    for a,b in ranges:
        if merged and a<=merged[-1][1]:
            merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:
            merged.append((a,b))
    start=max(0,merged[0][0]-60) if merged else 0
    end=min(len(text),start+max_chars)
    parts=[]
    if start:
        parts.append({'text':'…','match':False})
    cursor=start
    for a,b in merged:
        if a>=end:
            break
        a,b=max(a,start),min(b,end)
        if b<=start:
            continue
        if a>cursor:
            parts.append({'text':text[cursor:a],'match':False})
        parts.append({'text':text[a:b],'match':True})
        cursor=b
    if cursor<end:
        parts.append({'text':text[cursor:end],'match':False})
    if end<len(text):
        parts.append({'text':'…','match':False})
    return parts
