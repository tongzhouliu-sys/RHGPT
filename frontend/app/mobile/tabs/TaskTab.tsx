"use client";

import React from "react";
import { useAppJob } from "../../context/AppContext";
import { ProgressBanner } from "../../components/shared/ProgressBanner";
import { NodeCard } from "../../components/shared/NodeCard";

import { SystemWorkflowSteps } from "../../components/shared/SystemWorkflowSteps";

interface TaskTabProps {
  onGoToChat: () => void;
}

export const TaskTab: React.FC<TaskTabProps> = React.memo(({ onGoToChat }) => {
  const { phase, order, nodes, jobId, expandedKeys, toggleExpand, cancel } = useAppJob();

  const running = phase === "running";
  const finished = phase === "done";
  const currentRunningKey = order.find((k) => nodes[k]?.status === "running");

  if (phase === "idle" && order.length === 0) {
    return (
      <div className="mobile-tab-content">
        <SystemWorkflowSteps onGoToChat={onGoToChat} />
      </div>
    );
  }

  return (
    <div className="mobile-tab-content">
      {/* Mini Top Header */}
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
            />
          );
        })}
      </div>
    </div>
  );
});

TaskTab.displayName = "TaskTab";
