import type { InjectionKey } from "vue";
export interface TextDraft {
  text: string;
  saved: string;
}
export const textDraftsKey: InjectionKey<Map<string, TextDraft>> =
  Symbol("text-drafts");
