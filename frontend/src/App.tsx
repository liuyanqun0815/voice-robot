import React, { useEffect, useState } from "react";

import { OpsDashboardPage } from "./pages/OpsDashboardPage";
import { VoicePage } from "./pages/VoicePage";

type PageId = "voice" | "ops";

function readPageFromHash(): PageId {
  return window.location.hash === "#/ops" ? "ops" : "voice";
}

export function App(): JSX.Element {
  const [page, setPage] = useState<PageId>(readPageFromHash);

  useEffect(() => {
    const onHashChange = (): void => setPage(readPageFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = (target: PageId): void => {
    window.location.hash = target === "ops" ? "#/ops" : "#/";
    setPage(target);
  };

  return (
    <>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "10px 16px",
          borderBottom: "1px solid #ddd",
          background: "#fafafa",
          fontFamily: "Arial, sans-serif",
        }}
      >
        <strong style={{ fontSize: 18 }}>Voice Robot</strong>
        <nav style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            onClick={() => navigate("voice")}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: page === "voice" ? "2px solid #3d9e62" : "1px solid #ccc",
              background: page === "voice" ? "#e8f5e9" : "#fff",
              cursor: "pointer",
            }}
          >
            语音对话
          </button>
          <button
            type="button"
            onClick={() => navigate("ops")}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: page === "ops" ? "2px solid #3b82f6" : "1px solid #ccc",
              background: page === "ops" ? "#e8f0fe" : "#fff",
              cursor: "pointer",
            }}
          >
            运维仪表盘
          </button>
        </nav>
      </header>
      {page === "voice" ? <VoicePage /> : <OpsDashboardPage />}
    </>
  );
}
