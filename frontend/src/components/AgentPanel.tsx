import { Send } from "lucide-react";
import { useState } from "react";

import { askAgent } from "../api/client";
import type { OpportunityCard } from "../types";

export function AgentPanel({ selectedCard }: { selectedCard?: OpportunityCard }) {
  const [question, setQuestion] = useState("Where is the stop?");
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit() {
    setLoading(true);
    try {
      const result = await askAgent(question, selectedCard?.instrument_id);
      setAnswer(result.answer);
    } finally {
      setLoading(false);
    }
  }

  return (
    <aside className="agent-panel">
      <div className="panel-heading">
        <h2>Agent</h2>
        <span className="count">{selectedCard?.instrument_id ?? "Context"}</span>
      </div>
      <textarea value={question} onChange={(event) => setQuestion(event.target.value)} />
      <button type="button" onClick={submit} disabled={loading}>
        <Send size={16} />
        {loading ? "Thinking" : "Ask"}
      </button>
      {answer && <div className="answer">{answer}</div>}
    </aside>
  );
}
