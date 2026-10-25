import { defineOnce } from "./define";

interface DashboardCardRegistration {
  tag: string;
  editorTag: string;
  card: CustomElementConstructor;
  editor: CustomElementConstructor;
  name: string;
  description: string;
}

export function registerDashboardCard(
  registration: DashboardCardRegistration,
): void {
  defineOnce(registration.tag, registration.card);
  defineOnce(registration.editorTag, registration.editor);

  window.customCards ??= [];
  if (!window.customCards.some((card) => card.type === registration.tag)) {
    window.customCards.push({
      type: registration.tag,
      name: registration.name,
      description: registration.description,
      preview: true,
      documentationURL:
        "https://github.com/tinkerologie/ha-unifi-insights#dashboard-cards",
    });
  }
}
