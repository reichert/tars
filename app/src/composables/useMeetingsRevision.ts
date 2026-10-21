// Contador compartilhado: quem altera uma reunião sem que o backend publique `meeting_updated`
// (renomear, excluir, resumir...) chama `notifyMeetingsChanged()` para a lista recarregar.
import { ref } from "vue";

export const meetingsRevision = ref(0);

export function notifyMeetingsChanged(): void {
  meetingsRevision.value++;
}
