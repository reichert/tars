"""Vocabulário (hotwords) e substituições do ditado, lidos de arquivos editáveis."""

import logging
import os
import re
from pathlib import Path

log = logging.getLogger("ditado")


def _read_lines(path: Path) -> list[str]:
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return []
    try:
        content = raw.decode("utf-8-sig")  # -sig descarta o BOM que alguns editores gravam
    except UnicodeDecodeError:
        log.warning("%s não está em UTF-8; lendo como latin-1", path)
        content = raw.decode("latin-1")
    lines = (line.strip() for line in content.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


class Vocabulary:
    """Termos (hotwords) e substituições lidos de arquivos; recarrega quando o arquivo muda.

    vocabulario.txt: um termo por linha, em ordem de prioridade (o fim é cortado se passar do limite).
    substituicoes.txt: `errado => certo` por linha; troca expressão inteira, sem diferenciar maiúsculas.

    `tokenizer` (API do `tokenizers`: `encode(texto, add_special_tokens=False).ids`) só serve para
    avisar quando o vocabulário passa do limite; pode ser atribuído depois (o Transcriber faz isso).
    """

    # O faster-whisper corta os hotwords em max_length // 2 - 1 tokens (448 // 2 - 1).
    MAX_TOKENS = 223

    def __init__(self, vocab_path: Path, subs_path: Path, tokenizer=None) -> None:
        self.vocab_path = Path(vocab_path)
        self.subs_path = Path(subs_path)
        self.tokenizer = tokenizer
        self.hotwords: str | None = None
        self.terms: frozenset[str] = frozenset()
        self.subs: dict[str, str] = {}
        self.subs_pattern: re.Pattern | None = None
        self._keys: tuple = (None, None)
        self.refresh()

    @staticmethod
    def _key(path: Path) -> tuple[int, int, int] | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        # mtime sozinho pode não mudar num save rápido; tamanho e inode cobrem save parcial e por rename.
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def refresh(self) -> None:
        keys = (self._key(self.vocab_path), self._key(self.subs_path))
        if keys == self._keys:
            return
        # Grava antes de ler: se o arquivo estiver com erro, avisa uma vez e mantém o vocabulário anterior.
        self._keys = keys
        try:
            terms = _read_lines(self.vocab_path)
            subs: dict[str, str] = {}
            for line in _read_lines(self.subs_path):
                src, sep, dst = line.partition("=>")
                if not sep or not src.strip():
                    log.warning("Linha ignorada em %s: %r (use `errado => certo`)", self.subs_path, line)
                    continue
                subs[src.strip().lower()] = dst.strip()
            hotwords = ", ".join(terms) or None
            tokens = 0
            if hotwords and self.tokenizer is not None:
                tokens = len(self.tokenizer.encode(" " + hotwords, add_special_tokens=False).ids)
        except Exception:
            log.exception("Falha ao carregar vocabulário/substituições; mantendo os anteriores")
            return

        self.hotwords = hotwords
        self.terms = frozenset(t.lower() for t in terms)
        self.subs = subs
        # Uma passada só, da expressão mais longa para a mais curta: as trocas não se encadeiam.
        alternatives = "|".join(re.escape(src) for src in sorted(subs, key=len, reverse=True))
        self.subs_pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE) if subs else None
        log.info("Vocabulário: %d termos (%d tokens), %d substituições", len(terms), tokens, len(subs))
        if tokens > self.MAX_TOKENS:
            log.warning(
                "Vocabulário passa de %d tokens; os termos do fim de %s serão ignorados",
                self.MAX_TOKENS,
                self.vocab_path,
            )

    def apply(self, text: str) -> str:
        if self.subs_pattern is None:
            return text

        def replace(match: re.Match) -> str:
            dst = self.subs[match.group(0).lower()]
            if match.group(0)[:1].isupper() and dst[:1].islower():
                dst = dst[0].upper() + dst[1:]  # mantém a maiúscula de início de frase
            return dst

        text = self.subs_pattern.sub(replace, text)
        return re.sub(r" {2,}", " ", text).strip()  # destino vazio (`né =>`) remove a palavra

    def is_echo(self, text: str) -> bool:
        """True se o texto parece só a lista de vocabulário repetida (alucinação em áudio sem fala)."""
        items = [item.strip(" .!?;:").lower() for item in text.split(",")]
        items = [item for item in items if item]
        # Exige uma lista longa: ditar "Docker, Redis e Celery" de propósito não pode ser descartado.
        return len(items) >= 5 and sum(item in self.terms for item in items) >= 0.8 * len(items)


def has_loop(text: str) -> bool:
    """Detecta o loop de repetição do Whisper ("revisado, revisado, revisado, ...").

    Repetições curtas são comuns na fala ("não, não, não", "muito, muito, muito"), então
    1 ou 2 palavras precisam se repetir 5 vezes seguidas; trechos de 3 a 6 palavras, 3 vezes.
    """
    words = re.findall(r"[^\W\d_]+", text.lower())  # ignora números: "versão 1.1.1" não é loop
    for n in range(1, 7):
        repeats = 5 if n <= 2 else 3
        for start in range(len(words) - n * repeats + 1):
            gram = words[start : start + n]
            if all(words[start + i * n : start + (i + 1) * n] == gram for i in range(1, repeats)):
                return True
    return False
