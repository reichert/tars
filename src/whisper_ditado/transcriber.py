"""Transcrição com faster-whisper: texto corrido (ditado) e palavras com tempo (reuniões)."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from faster_whisper import WhisperModel

from whisper_ditado.vocab import Vocabulary, has_loop

log = logging.getLogger("ditado")

SAMPLE_RATE = 16000

# Textos que o Whisper "alucina" quando não há fala (vêm de legendas do treino).
HALLUCINATIONS = {
    "obrigado por assistir.",
    "obrigado por assistir!",
    "thanks for watching!",
    "thank you for watching.",
}


@dataclass
class TranscriberOptions:
    model: str = "large-v3-turbo"
    compute_type: str = "float16"
    language: str | None = "pt"
    # Mostra ao modelo o estilo esperado: português com termos técnicos escritos em inglês.
    prompt: str | None = None
    beam_size: int = 5


@dataclass
class Word:
    start_s: float
    end_s: float
    text: str
    prob: float


class Transcriber:
    # Chamado antes de decodificar cada trecho em `transcribe_words` (o gerador do faster-whisper
    # decodifica ~30 s por `next()`); as reuniões usam para ceder a GPU ao ditado.
    pause: Callable[[], None] | None = None

    def __init__(self, opts: TranscriberOptions, vocab: Vocabulary) -> None:
        self.opts = opts
        self.vocab = vocab
        log.info("Carregando %s (%s) na GPU...", opts.model, opts.compute_type)
        try:
            # Usa o cache local primeiro: sobe sem rede e sem consultar o Hugging Face.
            self.model = self._load(local_files_only=True)
        except Exception as exc:
            log.warning("Falha ao carregar do cache (%s); tentando baixar...", exc, exc_info=True)
            self.model = self._load(local_files_only=False)
        if self.vocab.tokenizer is None:  # o tokenizer só existe depois de carregar o modelo
            self.vocab.tokenizer = self.model.hf_tokenizer
        self._warmup()

    def _load(self, local_files_only: bool) -> WhisperModel:
        return WhisperModel(
            self.opts.model,
            device="cuda",
            compute_type=self.opts.compute_type,
            local_files_only=local_files_only,
        )

    def unload(self) -> None:
        """Libera pesos e recursos nativos; este transcritor não deve mais ser usado."""
        self.model.model.unload_model(to_cpu=False)

    def _warmup(self) -> None:
        # A primeira inferência na GPU é lenta (init do cuBLAS/cuDNN); paga isso no boot.
        started = time.perf_counter()
        noise = (np.random.default_rng(0).standard_normal(SAMPLE_RATE) * 0.01).astype(np.float32)
        for vad in (False, True):
            segments, _ = self.model.transcribe(noise, language=self.opts.language, vad_filter=vad)
            list(segments)
        log.info("Modelo pronto (aquecimento em %.1fs)", time.perf_counter() - started)

    def _refresh_vocab(self) -> None:
        try:
            self.vocab.refresh()
        except Exception:  # problema no vocabulário nunca deve custar o ditado
            log.exception("Falha ao recarregar o vocabulário")

    def transcribe_text(self, audio: np.ndarray) -> str:
        self._refresh_vocab()
        text = self._transcribe(audio)
        if has_loop(text):
            # Com hotwords o Whisper às vezes entra em loop ("revisado, revisado, ..."). Bloquear
            # n-gramas repetidos resolve, mas distorce repetições naturais, então é só o plano B.
            log.info("Loop de repetição detectado; transcrevendo de novo")
            text = self._transcribe(audio, no_repeat_ngram_size=3) or text
        return self.vocab.apply(text)

    def transcribe_words(self, audio: np.ndarray, offset_s: float = 0.0) -> list[Word]:
        """Palavras com tempo absoluto (`offset_s` + posição no áudio); sem substituições."""
        self._refresh_vocab()
        words, text = self._words(audio, offset_s)
        if has_loop(text):
            # Mesmo plano B do ditado (`transcribe_text`): refaz o bloco bloqueando n-gramas.
            log.info("Loop de repetição detectado na reunião; transcrevendo o bloco de novo")
            retry, _ = self._words(audio, offset_s, no_repeat_ngram_size=3)
            words = retry or words
        return words

    def _words(self, audio: np.ndarray, offset_s: float, **options) -> tuple[list[Word], str]:
        segments, _ = self.model.transcribe(
            audio,
            language=self.opts.language,
            initial_prompt=self.opts.prompt,
            hotwords=self.vocab.hotwords,
            beam_size=self.opts.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=False,
            word_timestamps=True,
            **options,
        )
        words: list[Word] = []
        texts: list[str] = []
        for seg in self._paced(segments):
            if self._discard(seg):
                continue
            texts.append(seg.text.strip())
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append(Word(offset_s + w.start, offset_s + w.end, text, w.probability))
        return words, " ".join(texts)

    def _paced(self, segments):
        it = iter(segments)
        while True:
            if self.pause is not None:
                self.pause()
            try:
                yield next(it)
            except StopIteration:
                return

    def _transcribe(self, audio: np.ndarray, **options) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language=self.opts.language,
            initial_prompt=self.opts.prompt,
            hotwords=self.vocab.hotwords,
            beam_size=self.opts.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=True,
            **options,
        )
        return " ".join(seg.text.strip() for seg in segments if not self._discard(seg))

    def _discard(self, seg) -> bool:
        """True para segmentos vazios, alucinações conhecidas e ecos do prompt/vocabulário."""
        text = seg.text.strip()
        lower = text.lower()
        if not text or lower in HALLUCINATIONS or "amara.org" in lower:
            return True
        # Em áudio quase sem fala o Whisper às vezes "repete" o próprio prompt ou o vocabulário.
        prompt = (self.opts.prompt or "").strip(" .!?").lower()
        if (prompt and lower.strip(" .!?") == prompt) or self.vocab.is_echo(text):
            return True
        return seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0
