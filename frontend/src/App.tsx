import { useState } from "react";
import { UploadForm } from "./components/UploadForm";
import { StatusPoller } from "./components/StatusPoller";
import { Dashboard } from "./components/Dashboard";
import { MetricsTable } from "./components/MetricsTable";
import { RatiosTable } from "./components/RatiosTable";
import { RiskTable } from "./components/RiskTable";
import { ChatPanel } from "./components/ChatPanel";
import { ExplainPanel } from "./components/ExplainPanel";
import type { StatusResponse } from "./api/types";

type View = "upload" | "processing" | "results";
type ResultsTab = "dashboard" | "metrics" | "ratios" | "risks" | "chat";

interface ExplainTarget {
  metricName: string;
  year: number;
}

/**
 * Top-level workflow state machine. No routing library — the flow is
 * strictly linear (upload -> processing -> results), so plain useState is
 * enough. Each stage's data-fetching lives in its own component; this file
 * only tracks *which* stage/tab is active and the doc_id being worked on.
 */
function App() {
  const [view, setView] = useState<View>("upload");
  const [docId, setDocId] = useState<string | null>(null);
  const [pdfName, setPdfName] = useState<string | null>(null);
  const [companyName, setCompanyName] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<ResultsTab>("dashboard");
  const [explainTarget, setExplainTarget] = useState<ExplainTarget | null>(null);

  function handleUploaded(newDocId: string, newPdfName: string) {
    setDocId(newDocId);
    setPdfName(newPdfName);
    setView("processing");
  }

  function handleReady(status: StatusResponse) {
    setCompanyName(status.company_name);
    setView("results");
    setActiveTab("dashboard");
  }

  function handleReset() {
    setView("upload");
    setDocId(null);
    setPdfName(null);
    setCompanyName(null);
    setExplainTarget(null);
    setActiveTab("dashboard");
  }

  return (
    <div className="app">
      <header>
        <h1>FinTool</h1>
        {docId && (
          <p className="muted">
            {pdfName} — doc_id: <code>{docId}</code>
            {companyName ? ` — ${companyName}` : ""}
            {" "}
            <button className="link-button" onClick={handleReset}>upload a different document</button>
          </p>
        )}
      </header>

      <main>
        {view === "upload" && <UploadForm onUploaded={handleUploaded} />}

        {view === "processing" && docId && (
          <StatusPoller docId={docId} onReady={handleReady} />
        )}

        {view === "results" && docId && (
          <div className="results-layout">
            <div className="results-main">
              <nav className="tabs">
                <button className={activeTab === "dashboard" ? "active" : ""} onClick={() => setActiveTab("dashboard")}>
                  Dashboard
                </button>
                <button className={activeTab === "metrics" ? "active" : ""} onClick={() => setActiveTab("metrics")}>
                  Metrics
                </button>
                <button className={activeTab === "ratios" ? "active" : ""} onClick={() => setActiveTab("ratios")}>
                  Ratios
                </button>
                <button className={activeTab === "risks" ? "active" : ""} onClick={() => setActiveTab("risks")}>
                  Risk
                </button>
                <button className={activeTab === "chat" ? "active" : ""} onClick={() => setActiveTab("chat")}>
                  Chat
                </button>
              </nav>

              {activeTab === "dashboard" && (
                <Dashboard
                  docId={docId}
                  onNavigate={(tab) => setActiveTab(tab)}
                  onExplain={(metricName, year) => setExplainTarget({ metricName, year })}
                />
              )}
              {activeTab === "metrics" && <MetricsTable docId={docId} />}
              {activeTab === "ratios" && (
                <RatiosTable
                  docId={docId}
                  onExplain={(metricName, year) => setExplainTarget({ metricName, year })}
                />
              )}
              {activeTab === "risks" && <RiskTable docId={docId} />}
              {activeTab === "chat" && <ChatPanel docId={docId} />}
            </div>

            {explainTarget && (
              <ExplainPanel
                docId={docId}
                metricName={explainTarget.metricName}
                year={explainTarget.year}
                onClose={() => setExplainTarget(null)}
              />
            )}
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
