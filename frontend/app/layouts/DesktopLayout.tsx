"use client";

import React, { useEffect, useState } from "react";
import { USE_MOCK } from "../../lib/api";
import { PIPELINES, useAppJob, useAppSettings } from "../context/AppContext";
import ModelProbe from "../components/ModelProbe";
import AvatarScene from "../components/AvatarScene";
import { ModelPillGrid } from "../components/shared/ModelPillGrid";
import { ProgressBanner } from "../components/shared/ProgressBanner";
import { NodeCard } from "../components/shared/NodeCard";

export const DesktopLayout: React.FC = React.memo(() => {
  const {
    theme,
    pipeline,
    availableModels,
    selectedModels,
    toggleTheme,
    toggleModel,
    selectOnlyAPI,
    selectAllModels,
    setPipeline,
  } = useAppSettings();

  const {
    question,
    setQuestion,
    phase,
    order,
    nodes,
    jobId,
    banner,
    expandedKeys,
    concurrentBusy,
    setConcurrentBusy,
    run,
    cancel,
    againRound,
    onExport,
    toggleExpand,
    chatHistory,
    loadHistoryItem,
    deleteHistoryItem,
    retryHistoryItem,
  } = useAppJob();

  const running = phase === "running";
  const finished = phase === "done";

  const currentRunningKey = order.find((k) => nodes[k]?.status === "running");
  const currentProvider = currentRunningKey ? nodes[currentRunningKey]?.provider : undefined;

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
    <main className="wrap">
      {/* 公益项目首次弹窗 */}
      {showWelcome && (
        <div className="welcome-modal-overlay" onClick={dismissWelcome}>
          <div className="welcome-modal" onClick={(e) => e.stopPropagation()}>
            <div className="welcome-modal-icon">🌟</div>
            <h2 className="welcome-modal-title">公益项目声明</h2>
            <p className="welcome-modal-body">本项目为公益项目。</p>
            <p className="welcome-modal-thanks">感谢 <strong>WUWEI</strong> 提供大模型支持！</p>
            <p className="welcome-modal-thanks">感谢 <strong>KAISHANG</strong> 为开发过程提供指导！</p>
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

      {/* 顶部 Navigation / Masthead */}
      <header className="masthead">
        <div className="brand-group">
          <div className="brand-row">
            <span className="brand">RHCLOUD V1</span>
            <h1>AI 智能接力协作控制台</h1>
          </div>
          <span className="sub">multi-model relay console {USE_MOCK ? "· MOCK 模式" : ""}</span>
        </div>

        <div className="masthead-actions">
          {/* 大模型可用性探针（右侧图标展示） */}
          <ModelProbe />

          {/* 深色 / 浅色皮肤切换按钮 */}
          <button className="theme-toggle" onClick={toggleTheme} title="切换外观主题">
            {theme === "dark" ? "☀️ 浅色" : "🌙 深色"}
          </button>
        </div>
      </header>

      {/* 交互控制面板 */}
      <section className="panel">
        <label className="field" htmlFor="q">
          输入您的任务或提问 (Prompt)
        </label>
        <textarea
          id="q"
          rows={4}
          placeholder="例如：帮我设计一个高并发短链服务架构，包含缓存与数据库设计"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={running}
        />

        <ModelPillGrid
          availableModels={availableModels}
          selectedModels={selectedModels}
          toggleModel={toggleModel}
          selectOnlyAPI={selectOnlyAPI}
          selectAllModels={selectAllModels}
          disabled={running}
        />

        <div className="controls">
          <div className="grow">
            <label className="field">
              选择执行流水线
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
          <button id="start-btn" onClick={run} disabled={running || !question.trim()}>
            {running ? "🚀 智能协同中…" : "🚀 开始接力执行"}
          </button>
          {running && (
            <button className="ghost" onClick={cancel} style={{ color: "var(--danger)", borderColor: "var(--danger)" }}>
              🛑 终止任务
            </button>
          )}
        </div>
      </section>

      {/* 并发已满或限制时的友好降级交互卡片 */}
      {concurrentBusy && (
        <section className="panel" style={{ border: "1px solid var(--warn)", background: "rgba(251, 191, 36, 0.08)", marginTop: "24px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", color: "var(--warn)", fontWeight: 700, fontSize: "16px" }}>
            <span style={{ fontSize: "22px" }}>⚠️</span>
            <span>当前通道并发已满 (Max Concurrent Jobs Reached)</span>
          </div>
          <p style={{ margin: "12px 0 18px 0", fontSize: "14px", lineHeight: "1.6", color: "var(--text)" }}>
            系统检测到当前模型通道繁忙。您无需终止任务，建议立即一键切换为高效【直连 API 模型组】继续工作，或点击重试：
          </p>
          <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
            <button
              type="button"
              onClick={() => {
                setConcurrentBusy(false);
                selectOnlyAPI();
                setTimeout(() => {
                  const btn = document.getElementById("start-btn");
                  if (btn) btn.click();
                }, 100);
              }}
              style={{ background: "linear-gradient(135deg, var(--warn), #f59e0b)", color: "#000" }}
            >
              ⚡ 切换为【直连 API】继续重试
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setConcurrentBusy(false);
                const btn = document.getElementById("start-btn");
                if (btn) btn.click();
              }}
            >
              🔄 原模型组重试
            </button>
          </div>
        </section>
      )}

      {/* 动态会议协同场景 (运行中或已有执行步骤时展示) */}
      {(running || phase !== "idle") && (
        <AvatarScene activeProvider={currentProvider} stepKey={currentRunningKey} question={question} />
      )}

      {/* 任务执行时间轴与节点卡片 */}
      {phase !== "idle" && (
        <>
          <ProgressBanner
            running={running}
            finished={finished}
            phase={phase}
            orderLength={order.length}
            currentRunningKey={currentRunningKey}
          />

          <div className="relay">
            {order.map((key, idx) => {
              const n = nodes[key];
              if (!n) return null;
              const isLast = idx === order.length - 1;
              const open = expandedKeys[key] !== undefined ? expandedKeys[key] : isLast;
              return (
                <NodeCard
                  key={key}
                  nodeKey={key}
                  node={n}
                  open={open}
                  onToggleExpand={toggleExpand}
                />
              );
            })}
          </div>
        </>
      )}

      {banner && <div className="banner" style={{ marginTop: "20px", color: "var(--danger)" }}>{banner}</div>}

      {/* 导出结果面板 */}
      {finished && jobId && (
        <section className="panel" style={{ marginTop: "28px" }}>
          <h3 style={{ margin: "0 0 16px 0", color: "var(--accent)" }}>📥 任务导出与续写</h3>
          <div className="controls" style={{ marginTop: 0 }}>
            <button className="ghost" onClick={() => onExport("merged")} disabled={USE_MOCK}>
              导出合并 Markdown
            </button>
            <button className="ghost" onClick={() => onExport("steps")} disabled={USE_MOCK}>
              导出单步打包
            </button>
            <button className="ghost" onClick={() => onExport("json")} disabled={USE_MOCK}>
              导出 JSON
            </button>
            <button onClick={againRound}>再来一轮接力</button>
          </div>
          {USE_MOCK && <p style={{ fontSize: "12px", color: "var(--muted)", marginTop: "12px" }}>Mock 模式下没有落盘文件，导出按钮已禁用。连接真实后端后可用。</p>}
        </section>
      )}

      {/* 历史记录 */}
      {chatHistory.length > 0 && (
        <section className="panel" style={{ marginTop: "28px" }}>
          <h3 style={{ margin: "0 0 16px 0", color: "var(--accent)" }}>🕒 历史记录</h3>
          <div className="desktop-history-list">
            {chatHistory.map((item) => (
              <div key={item.jobId} className="desktop-history-item">
                <div
                  className="desktop-history-content"
                  onClick={() => {
                    if (item.finalStatus === "error") {
                      retryHistoryItem(item);
                    } else if (item.finalStatus === "done") {
                      loadHistoryItem(item);
                    }
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <div className="desktop-history-q">{item.question}</div>
                  <div className="desktop-history-meta">
                    <span>{new Date(item.timestamp).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                    <span className={`status-tag ${item.finalStatus}`}>
                      {item.finalStatus === "error" ? "🔄 点击重试" : item.finalStatus === "done" ? "👁️ 查看详情" : item.finalStatus}
                    </span>
                  </div>
                </div>
                <button
                  className="desktop-history-delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteHistoryItem(item.jobId);
                  }}
                  title="删除此记录"
                >
                  🗑️
                </button>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
});

DesktopLayout.displayName = "DesktopLayout";
