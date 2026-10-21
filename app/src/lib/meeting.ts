// Funções puras dos componentes de reunião: texto da transcrição, nome do arquivo exportado,
// duração legível e linhas de alerta.
import type { AlertKind, MeetingDetail, Segment, Speaker } from "../api/types";
import { mmss, timeOfDay } from "./format";

const pad = (n: number) => String(n).padStart(2, "0");

/** Nome mostrado/copiado para um falante (igual ao backend: sem rótulo vira "Desconhecido"). */
export function speakerName(speaker: Speaker | undefined): string {
  return speaker?.label || "Desconhecido";
}

/** Corpo da transcrição no formato de `export.md` (`[mm:ss] Nome: texto`, sem cabeçalho). */
export function transcriptText(segments: Segment[], speakers: Speaker[]): string {
  const byId = new Map(speakers.map((s) => [s.id, s]));
  return segments
    .map((seg) => {
      const name = speakerName(seg.speaker_id ? byId.get(seg.speaker_id) : undefined);
      return `[${mmss(seg.start_s)}] ${name}: ${seg.text}${seg.dictated ? " (ditado)" : ""}`;
    })
    .join("\n");
}

/** `reuniao-AAAAMMDD-HHMM.md`, como o backend sugere no Content-Disposition. */
export function exportFileName(startedAt: string): string {
  const d = new Date(startedAt);
  return `reuniao-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.md`;
}

/** "< 1 min", "42 min", "1 h 05 min". */
export function formatDuration(seconds: number): string {
  const minutes = Math.floor(Math.max(0, seconds) / 60);
  if (minutes < 1) return "< 1 min";
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${pad(minutes % 60)} min`;
}

export function peopleLabel(count: number): string {
  return count === 1 ? "1 pessoa" : `${count} pessoas`;
}

/** Rótulos dos alertas já registrados (a pílula ao vivo usa outros textos, em `ALERT_MESSAGES`). */
const ALERT_LABELS: Record<AlertKind, string> = {
  mic_silent: "Microfone sem sinal",
  pc_silent: "Sem som do PC",
  pc_wrong_output: "Áudio da chamada fora da saída gravada",
  mic_dead: "Microfone parou",
  pc_dead: "Captura do PC parou",
};

/** "Microfone sem sinal de 12:10 a 12:40": os offsets do alerta somados ao início da reunião. */
export function alertLines(meeting: Pick<MeetingDetail, "started_at" | "alerts">): string[] {
  const start = new Date(meeting.started_at).getTime();
  const clock = (offset: number) => timeOfDay(new Date(start + offset * 1000).toISOString());
  return meeting.alerts.map(
    (a) => `${ALERT_LABELS[a.kind] ?? a.kind} de ${clock(a.start_s)} a ${clock(a.end_s)}`,
  );
}
