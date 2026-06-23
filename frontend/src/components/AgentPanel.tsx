import { Send } from "lucide-react";
import { useState } from "react";

import { askAgent } from "../api/client";
import { useI18n } from "../i18n";
import type { OpportunityCard } from "../types";

export function AgentPanel({ selectedCard }: { selectedCard?: OpportunityCard }) {
  const { t } = useI18n();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit() {
    setLoading(true);
    try {
      const result = await askAgent(question.trim() || t("agent.defaultQuestion"), selectedCard?.instrument_id);
      setAnswer(result.answer);
    } finally {
      setLoading(false);
    }
  }

  return (
    <aside className="agent-panel">
      <div className="panel-heading">
        <h2>{t("agent.title")}</h2>
        <span className="count">{selectedCard?.instrument_id ?? t("agent.context")}</span>
      </div>
      <textarea
        value={question}
        placeholder={t("agent.defaultQuestion")}
        onChange={(event) => setQuestion(event.target.value)}
      />
      <button type="button" onClick={submit} disabled={loading}>
        <Send size={16} />
        {loading ? t("agent.thinking") : t("agent.ask")}
      </button>
      {answer && <div className="answer">{answer}</div>}
    </aside>
  );
}
