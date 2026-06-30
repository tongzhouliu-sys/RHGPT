"use client";

import React from "react";
import { useAppJob } from "../../context/AppContext";
import { ProgressBanner } from "../../components/shared/ProgressBanner";
import { NodeCard } from "../../components/shared/NodeCard";

interface TaskTabProps {
  onGoToChat: () => void;
}

/* ── 紧凑步骤数据（不需滑动）──────────────────────────── */
const STEPS_COMPACT = [
  { icon: "⚡", num: "01", label: "竞速生成", desc: "多模型并发，最快者锁定领跑", color: "var(--accent, #6366f1)" },
  { icon: "🔍", num: "02", label: "交叉评审", desc: "多视角审阅，检测逻辑漏洞", color: "#ec4899" },
  { icon: "🧩", num: "03", label: "逻辑拆解", desc: "深度推理，补全技术细节", color: "#8b5cf6" },
  { icon: "🛠️", num: "04", label: "方案优化", desc: "二次重构，确保高可用性", color: "#10b981" },
  { icon: "📦", num: "05", label: "总结交付", desc: "聚合成果，输出 Markdown", color: "#f59e0b" },
];

export const TaskTab: React.FC<TaskTabProps> = React.memo(({ onGoToChat }) => {
  const { phase, order, nodes, jobId, expandedKeys, toggleExpand, cancel, againRound } = useAppJob();

  const running = phase === "running";
  const finished = phase === "done";
  const currentRunningKey = order.find((k) => nodes[k]?.status === "running");

  return (
    <div className="mobile-tab-content">
      {/* 统一头部样式，与其他页面一致 */}
      <div className="mobile-chat-header">
        <span className="brand" style={{ fontSize: "16px" }}>RHCLOUD V1</span>
        <span style={{ fontSize: "12px", color: "var(--muted)" }}>任务执行中心</span>
      </div>

      {/* 空闲 + 无任务状态：紧凑运行流程介绍（一屏内，不需滑动） */}
      {phase === "idle" && order.length === 0 && (
        <div className="mobile-card" style={{ padding: "16px" }}>
          {/* 小标题 */}
          <div style={{ marginBottom: "14px", borderBottom: "2px solid var(--border)", paddingBottom: "10px" }}>
            <span style={{
              display: "block",
              fontSize: "11px",
              fontWeight: 800,
              color: "var(--accent)",
              letterSpacing: "1px",
              textTransform: "uppercase",
              marginBottom: "4px",
            }}>AI 多模型接力运行流程</span>
            <p style={{ fontSize: "12px", color: "var(--muted)", margin: 0, lineHeight: 1.4 }}>
              每个任务经历 5 轮严密的多模型协同竞速接力
            </p>
          </div>

          {/* 5步骤列表 */}
          <div style={{ display: "flex", flexDirection: "column", marginBottom: "14px" }}>
            {STEPS_COMPACT.map((step) => (
              <div key={step.num} style={{
                display: "flex",
                alignItems: "center",
                gap: "10px",
                padding: "10px 10px 10px 12px",
                borderBottom: "1px solid var(--border)",
                borderLeft: `2px solid ${step.color}`,
              }}>
                <span style={{ fontSize: "16px", flexShrink: 0, width: "20px", textAlign: "center" }}>{step.icon}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <span style={{
                      fontSize: "10px",
                      fontWeight: 700,
                      color: step.color,
                      flexShrink: 0,
                    }}>
                      {step.num}
                    </span>
                    <strong style={{ fontSize: "13px", color: "var(--text)" }}>{step.label}</strong>
                  </div>
                  <p style={{ fontSize: "11px", color: "var(--muted)", margin: "2px 0 0 0", lineHeight: 1.3 }}>
                    {step.desc}
                  </p>
                </div>
              </div>
            ))}
          </div>

          {/* CTA */}
          <button
            type="button"
            onClick={onGoToChat}
            style={{ width: "100%", height: "42px", fontSize: "13px" }}
          >
            💬 立即发起接力任务
          </button>
        </div>
      )}

      {/* 有任务时的 mini 头部信息 */}
      {(phase !== "idle" || order.length > 0) && (
        <>
          <div className="mobile-task-header">
            <div>
              <span style={{ fontSize: "12px", color: "var(--muted)" }}>任务 ID: </span>
              <span style={{ fontFamily: "var(--mono)", fontWeight: 600 }}>{jobId ? jobId.slice(0, 8) : "提交中..."}</span>
            </div>
            {running && (
              <button
                className="ghost"
                onClick={cancel}
                style={{ padding: "4px 10px", fontSize: "12px", color: "var(--danger)", borderColor: "var(--danger)" }}
              >
                🛑 终止
              </button>
            )}
          </div>

          <ProgressBanner
            running={running}
            finished={finished}
            phase={phase}
            orderLength={order.length}
            currentRunningKey={currentRunningKey}
          />

          <div className="relay" style={{ marginTop: "16px" }}>
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
                  isMobile
                />
              );
            })}
          </div>

          {finished && (
            <div style={{ marginTop: "16px", paddingBottom: "8px" }}>
              <button
                onClick={() => { againRound(); onGoToChat(); }}
                style={{ width: "100%", height: "48px", fontSize: "14px" }}
              >
                🔄 再来一轮接力
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
});

TaskTab.displayName = "TaskTab";
