import { describe, expect, it } from "vitest";
import { feedbackDraft } from "./feedback-draft";

describe("feedback draft", () => {
  it("leaves unknown fields empty", () => {
    const text = feedbackDraft();
    expect(text).toContain("Счет: \n");
    expect(text).toContain("Шапка: \nДанные: \nИтоги: ");
    expect(text).not.toContain("отсутствует");
  });
  it("uses recognized columns and absolute row numbers", () => {
    const text = feedbackDraft({ sheet_name: "ОСВ", header_start_row: 6, header_end_row: 7,
      data_start_row: 8, data_end_row: 201, control_rows: [202], service_rows: [],
      header_source: "LLM", footer_source: "RULES",
      fields: [{key: "account", label: "Счет", column: 1, column_letter: "A", source: "LLM"}] });
    expect(text).toContain("Счет: A");
    expect(text).toContain("Шапка: 6–7\nДанные: 8–201\nИтоги: 202");
  });
});
