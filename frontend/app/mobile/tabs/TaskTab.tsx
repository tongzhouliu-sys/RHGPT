"use client";

import React from "react";
import { useAppJob } from "../../context/AppContext";
import { ProgressBanner } from "../../components/shared/ProgressBanner";
import { NodeCard } from "../../components/shared/NodeCard";

interface TaskTabProps {
  onGoToChat: () => void;
}

export const TaskTab: React.FC<TaskTabProps> = React.memo(({ onGoToChat }) => {
  const { phase, order, nodes, jobId, expandedKeys, toggleExpand, cancel } = useAppJob();

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

      {/* 空闲 + 无任务状态：简洁提示 */}
      {phase === "idle" && order.length === 0 && (
        <div className="mobile-card" style={{ textAlign: "center", padding: "32px 20px" }}>
          <div style={{ fontSize: "48px", marginBottom: "16px" }}>📋</div>
          <p style={{ fontSize: "15px", fontWeight: 600, color: "var(--text)", margin: "0 0 8px 0" }}>
            暂无进行中的任务
          </p>
          <p style={{ fontSize: "13px", color: "var(--muted)", margin: "0 0 20px 0" }}>
            在对话页面发起接力任务后，可在此实时查看执行进度与每步生成结果
          </p>
          <button
            type="button"
            onClick={onGoToChat}
            style={{ padding: "10px 24px", fontSize: "14px" }}
          >
            💬 去发起任务
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
        </>
      )}
    </div>
  );
});

TaskTab.displayName = "TaskTab";
