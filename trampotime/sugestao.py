"""Sugestão de ticket pela descrição: k vizinhos mais próximos (KNN) sobre TF-IDF.

Os exemplos são o que já ligou um texto a um ticket: o resumo das issues abertas no Jira (aba do navegador)
e as linhas das planilhas anteriores. A descrição nova é comparada com cada exemplo pela similaridade do
cosseno; os k mais parecidos votam no ticket, com peso pela similaridade. Funciona com poucos exemplos,
ao contrário de uma rede neural, e não precisa de nenhuma biblioteca.
"""
import math
import re
import unicodedata
from collections import Counter, defaultdict

K = 5
CONFIANCA_MINIMA = 0.2


def termos(texto: str) -> Counter:
    """Palavras e trigramas de letras, sem acento e em minúsculas ('validação' casa com 'validacao', 'valida')."""
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    palavras = [p for p in re.findall(r"[a-z0-9]+", texto) if len(p) >= 2]
    t: Counter = Counter(f"p:{p}" for p in palavras)
    for p in palavras:
        p = f"_{p}_"
        t.update(f"t:{p[i:i + 3]}" for i in range(len(p) - 2))
    return t


def sugerir(texto: str, exemplos: list[tuple[str, str]], k: int = K) -> list[dict]:
    """Até 3 tickets para a descrição, do mais provável ao menos, com a confiança de 0 a 1."""
    exemplos = list(dict.fromkeys((t, c) for t, c in exemplos if t and c))
    consulta = termos(texto)
    if not consulta or not exemplos:
        return []

    vetores = [termos(t) for t, _ in exemplos]
    df = Counter(termo for v in vetores for termo in v)
    n = len(vetores)
    idf = {termo: math.log((n + 1) / (f + 1)) + 1 for termo, f in df.items()}

    def pesar(v: Counter) -> dict:
        w = {termo: q * idf.get(termo, math.log(n + 1) + 1) for termo, q in v.items()}
        norma = math.sqrt(sum(x * x for x in w.values())) or 1.0
        return {termo: x / norma for termo, x in w.items()}

    q = pesar(consulta)
    vizinhos = sorted(
        ((sum(q.get(termo, 0) * x for termo, x in pesar(v).items()), exemplos[i]) for i, v in enumerate(vetores)),
        key=lambda s: s[0], reverse=True,
    )[:k]

    votos: dict[str, float] = defaultdict(float)
    melhor: dict[str, tuple[float, str]] = {}
    for sim, (texto_exemplo, chave) in vizinhos:
        if sim <= 0:
            continue
        votos[chave] += sim
        if chave not in melhor or sim > melhor[chave][0]:
            melhor[chave] = (sim, texto_exemplo)
    total = sum(votos.values()) or 1.0
    sugestoes = [
        # confiança: quão parecido é o exemplo mais próximo, descontado pela divisão dos votos
        {"chave": chave, "exemplo": melhor[chave][1], "confianca": round(melhor[chave][0] * voto / total, 2)}
        for chave, voto in sorted(votos.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return [s for s in sugestoes if s["confianca"] >= CONFIANCA_MINIMA][:3]


def similaridade(a: str, b: str) -> float:
    """Cosseno entre os termos de dois textos (0 a 1), sem depender de outros exemplos."""
    ta, tb = termos(a), termos(b)
    if not ta or not tb:
        return 0.0
    produto = sum(q * tb.get(t, 0) for t, q in ta.items())
    return produto / (math.sqrt(sum(q * q for q in ta.values())) * math.sqrt(sum(q * q for q in tb.values())))
