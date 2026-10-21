// A consulta automática da lista de modelos (cache vazio) roda no máximo uma vez por sessão do app:
// a seção Claude remonta a cada troca de aba, e sem `claude` instalado/logado cada abertura
// repetiria a consulta. O botão "Atualizar modelos" não passa por aqui.
let attempted = false;

/** Reserva a tentativa automática; `false` se ela já foi feita nesta sessão. */
export function takeAutoRefresh(): boolean {
  if (attempted) return false;
  attempted = true;
  return true;
}

/** Só para os testes. */
export function resetAutoRefresh(): void {
  attempted = false;
}
