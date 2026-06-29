"use client";

import { useEffect, useState } from "react";
import AgentLogo from "./AgentLogo";
import { fetchProviders, type ProviderInfo } from "../../lib/api";

export default function ModelProbe() {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<ProviderInfo[]>([]);

  useEffect(() => {
    fetchProviders().then((list) => setModels(list));
  }, []);

  const total = models.length;

  return (
    <div className="probe-compact-container" onMouseLeave={() => setOpen(false)}>
      <div
        className="probe-compact-bar"
        onClick={() => setOpen((prev) => !prev)}
        onMouseEnter={() => setOpen(true)}
        title="点击查看所有大模型连通状态"
      >
        <div className="probe-mini-icons">
          {models.slice(0, 5).map((m) => (
            <div key={m.id} className="probe-mini-item">
              <AgentLogo provider={m.site} size={18} />
              <span className="probe-dot-overlay" />
            </div>
          ))}
        </div>
        <span style={{ fontSize: "12px", fontFamily: "var(--mono)", fontWeight: 600, color: "var(--accent)" }}>
          📡 {total} 模型就绪
        </span>
      </div>

      {open && (
        <div className="probe-popover">
          <div className="probe-popover-header">
            <span>📡 大模型可用性探针</span>
            <span style={{ color: "var(--success)" }}>{total}/{total} 在线</span>
          </div>
          <div className="probe-popover-list">
            {models.map((m) => (
              <div key={m.id} className="probe-popover-item">
                <AgentLogo provider={m.site} size={22} />
                <div className="probe-popover-info">
                  <span className="probe-popover-name">{m.label}</span>
                  <span className="probe-popover-sub">
                    {m.api ? "官方 API 路线" : "Web 自动化路线"}
                    {m.model && ` · ${m.model}`}
                  </span>
                </div>
                <span className="dot ok" style={{ width: 8, height: 8 }} title="在线就绪" />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
