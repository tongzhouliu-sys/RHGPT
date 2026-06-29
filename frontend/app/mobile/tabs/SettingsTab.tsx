"use client";

import React, { useEffect, useState } from "react";
import { PIPELINES, useAppJob, useAppSettings } from "../../context/AppContext";
import { ModelPillGrid } from "../../components/shared/ModelPillGrid";
import ModelProbe from "../../components/ModelProbe";

export const SettingsTab: React.FC = React.memo(() => {
  const { theme, pipeline, availableModels, selectedModels, toggleTheme, toggleModel, selectOnlyAPI, selectAllModels, setPipeline } =
    useAppSettings();
  const { phase } = useAppJob();
  const running = phase === "running";

  /* ── 首次弹窗 ── */
  const [showWelcome, setShowWelcome] = useState(false);
  useEffect(() => {
    const welcomed = localStorage.getItem("rh_welcomed");
    if (!welcomed) {
      setShowWelcome(true);
    }
  }, []);
  const dismissWelcome = () => {
    setShowWelcome(false);
    localStorage.setItem("rh_welcomed", "1");
  };

  return (
    <div className="mobile-tab-content">
      {/* 公益项目首次弹窗 */}
      {showWelcome && (
        <div className="welcome-modal-overlay" onClick={dismissWelcome}>
          <div className="welcome-modal" onClick={(e) => e.stopPropagation()}>
            <div className="welcome-modal-icon">🌟</div>
            <h2 className="welcome-modal-title">公益项目声明</h2>
            <p className="welcome-modal-body">本项目为公益项目。</p>
            <p className="welcome-modal-thanks">感谢 <strong>WUWEI</strong> 提供大模型支持！</p>
            <p className="welcome-modal-thanks">感谢 <strong>KAISANG</strong> 为开发过程提供指导！</p>
            <p className="welcome-modal-thanks" style={{ marginTop: "10px", fontSize: "13px" }}>
              反馈邮箱：
              <a
                href="mailto:ceo@985.edu.kg"
                style={{ color: "var(--accent)", textDecoration: "underline" }}
                onClick={(e) => e.stopPropagation()}
              >
                ceo@985.edu.kg
              </a>
            </p>
            <button className="welcome-modal-btn" onClick={dismissWelcome}>
              知道了
            </button>
          </div>
        </div>
      )}

      <div className="mobile-chat-header">
        <span className="brand" style={{ fontSize: "16px" }}>RHCLOUD V1</span>
        <span style={{ fontSize: "12px", color: "var(--muted)" }}>系统与模型配置</span>
      </div>

      {/* 主题控制 */}
      <div className="mobile-card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <strong style={{ fontSize: "15px" }}>外观主题</strong>
          <div style={{ fontSize: "12px", color: "var(--muted)", marginTop: "2px" }}>
            当前皮肤: {theme === "dark" ? "深色暗夜" : "浅色明亮"}
          </div>
        </div>
        <button className="theme-toggle" onClick={toggleTheme} style={{ padding: "8px 16px" }}>
          {theme === "dark" ? "☀️ 浅色" : "🌙 深色"}
        </button>
      </div>

      {/* 流水线配置 */}
      <div className="mobile-card" style={{ marginTop: "14px" }}>
        <label className="field" style={{ margin: "0 0 8px 0" }}>
          选择默认执行流水线
        </label>
        <div className="pipeline-pills">
          {PIPELINES.map((p) => (
            <button
              key={p.value}
              type="button"
              className={`pipeline-pill ${pipeline === p.value ? "active" : ""}`}
              onClick={() => setPipeline(p.value)}
              disabled={running}
            >
              {p.shortLabel}
            </button>
          ))}
        </div>
      </div>

      {/* 模型选择网格 */}
      <div className="mobile-card" style={{ marginTop: "14px" }}>
        <ModelPillGrid
          availableModels={availableModels}
          selectedModels={selectedModels}
          toggleModel={toggleModel}
          selectOnlyAPI={selectOnlyAPI}
          selectAllModels={selectAllModels}
          disabled={running}
        />
      </div>

      {/* 探针常驻 */}
      <div className="mobile-card" style={{ marginTop: "14px", padding: "12px" }}>
        <ModelProbe alwaysOpen={true} />
      </div>
    </div>
  );
});

SettingsTab.displayName = "SettingsTab";
