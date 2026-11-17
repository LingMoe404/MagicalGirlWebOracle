"""Deterministic site constraints and local query-coverage reranking.

No network, model, tokenization service, or query persistence. This is a
lexical heuristic, not a semantic relevance or factual accuracy assessment.
"""
import re
from urllib.parse import urlsplit

_SITE=re.compile(r'''"[^"]*"|'[^']*'|(?<![\w:/.-])(?P<negative>-)?(?P<operator>site:)(?P<value>"[^"]*"|'[^']*'|[^\s()]+)?''',re.I)
_WORDS=re.compile(r'[a-z0-9]+|[\u3400-\u9fff]+',re.I)
_STOP=set('a an the and or of to in on for with is are what how can do does'.split())


def _host(value):
    return value.lower().rstrip('.').removeprefix('www.').encode('idna').decode('ascii')


def site_constraints(query):
    rules=[]
    for match in _SITE.finditer(query):
        if match.group('operator') is None:
            continue  # an operator inside a quoted phrase is literal text
        raw=match.group('value')
        if not raw or raw[0] in ('\"', "'") and (len(raw)<2 or raw[-1]!=raw[0]):
            raise ValueError('invalid site filter')
        value=match.group('value').strip('"\'').removeprefix('*.')
        try:
            parsed=urlsplit(value if '://' in value else 'https://'+value)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError()
            if parsed.port is not None:
                raise ValueError()
            host=_host(parsed.hostname)
            if not re.fullmatch(r'[a-z0-9.-]+',host) or '..' in host:
                raise ValueError()
        except (ValueError,UnicodeError):
            raise ValueError('invalid site filter; use a domain or domain/path') from None
        rules.append((bool(match.group('negative')),host,parsed.path.rstrip('/')))
    return rules


def filter_sites(results,rules):
    if not rules:
        return list(results)
    positive=[rule for rule in rules if not rule[0]]
    negative=[rule for rule in rules if rule[0]]
    def matches(result,rule):
        try:
            parsed=urlsplit(result.url)
            host=_host(parsed.hostname or '')
            return parsed.scheme in ('http','https') and (host==rule[1] or host.endswith('.'+rule[1])) and parsed.path.startswith(rule[2])
        except (ValueError,UnicodeError):
            return False
    return [result for result in results if (not positive or any(matches(result,rule) for rule in positive))
            and not any(matches(result,rule) for rule in negative)]


def terms(text):
    out=set()
    for word in _WORDS.findall(text.casefold()):
        if '\u3400'<=word[0]<='\u9fff':
            out.update(word[i:i+2] for i in range(max(1,len(word)-1)))
        elif word not in _STOP:
            out.add(word)
    return out


def rerank_local(query,results):
    # Remove real site operators from lexical scoring; quoted literals stay.
    clean=_SITE.sub(lambda match:'' if match.group('operator') else match.group(0),query[:8192])
    query_terms=terms(clean)
    if not query_terms:
        return sorted((result.model_copy(deep=True) for result in results),key=lambda result:result.score,reverse=True)
    scored=[]
    for result in results:
        title=terms(result.title[:4000])
        snippet=terms(result.snippet[:8000])
        content=terms(result.content[:12000])
        coverage=sum(3 if term in title else 2 if term in snippet else 1 if term in content else 0 for term in query_terms)/(3*len(query_terms))
        copy=result.model_copy(deep=True)
        copy.score=min(1.0,0.8*coverage+0.2*max(0,min(1,result.score)))
        copy.metadata={**copy.metadata,'reranked_by':'local','local_query_coverage':round(coverage,6)}
        scored.append(copy)
    return sorted(scored,key=lambda result:result.score,reverse=True)
